"""Reserve points, issue remotely, then finalize; never call Shopify under our locks."""
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, connection, transaction
from django.db.models import Exists, OuterRef, Q

from loyalty.models import AuditLog, CustomerProfile, PointsLot, PointsTransaction, ProgramConfig, Redemption, Reward
from shops.shopify import (DiscountCodeTaken, ShopifyError, ShopifyTransientError,
                          create_discount_code, find_discount_by_code, numeric_id)
from .earning import json_values
from .lifecycle import offset_debt
from .revocation_allocation import RevokeLot, plan_redemption
from .timing import parse_now, resolve_now

logger = logging.getLogger(__name__)
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
BACKOFF = (1, 5, 15, 60)
MESSAGES = {
    "ISSUED": "Đã phát hành mã giảm giá.", "PENDING": "Đang chờ phát hành mã giảm giá.",
    "PROGRAM_DISABLED": "Chương trình đang tắt hoặc chưa được cấu hình.",
    "CUSTOMER_BLOCKED": "Khách hàng đã bị chặn.", "CUSTOMER_ANONYMIZED": "Hồ sơ khách đã ẩn danh.",
    "CUSTOMER_NOT_FOUND": "Chưa có hồ sơ khách hàng.", "REWARD_NOT_FOUND": "Không tìm thấy phần thưởng.",
    "REWARD_INACTIVE": "Phần thưởng chưa hoặc không còn hoạt động.",
    "INSUFFICIENT_POINTS": "Không đủ điểm khả dụng.", "DAILY_LIMIT": "Đã đạt giới hạn đổi trong 24 giờ.",
    "UNUSED_CODES_LIMIT": "Đã đạt giới hạn mã chưa dùng của phần thưởng.",
    "STOCK_EXHAUSTED": "Phần thưởng đã hết số lượng.", "CUSTOMER_LIMIT": "Đã đạt giới hạn đổi phần thưởng của khách.",
    "UNSUPPORTED_REWARD": "Không hỗ trợ phần thưởng phần trăm có trần giảm giá.",
    "KEY_REUSED": "Khóa yêu cầu đã được dùng cho khách hoặc phần thưởng khác.",
    "INVALID_KEY": "Khóa yêu cầu phải có từ 1 đến 193 ký tự.", "INVALID_ID": "Mã đối tượng không hợp lệ.",
    "ISSUE_FAILED": "Không thể phát hành mã; điểm đã được hoàn.",
    "ALREADY_USED": "Mã đã được sử dụng.", "CODE_EXPIRED": "Mã đã hết hạn.",
    "CANCELLED": "Lượt đổi thưởng đã bị hủy.", "LEDGER_INCONSISTENT": "Số dư không khớp các lô điểm khả dụng.",
    "CODE_COLLISION": "Chưa tạo được mã duy nhất; hãy thử lại với cùng khóa yêu cầu.",
}


@dataclass
class RedeemOutcome:
    status: str
    redemption_id: int | None = None
    code: str | None = None
    reason_code: str = ""
    message: str = ""


def outcome(code, row=None):
    status = "ISSUED" if code == "ISSUED" else "PENDING" if code == "PENDING" else "REJECTED"
    return RedeemOutcome(status, row.pk if row else None,
        row.discount_code if row and status == "ISSUED" else None, code, MESSAGES[code])


def row_outcome(row):
    code = {"ISSUED": "ISSUED", "PENDING_CODE": "PENDING", "FAILED": "ISSUE_FAILED",
            "USED": "ALREADY_USED", "EXPIRED": "CODE_EXPIRED", "CANCELLED": "CANCELLED"}[row.status]
    return outcome(code, row)


def generate_code():
    return "LP-" + "".join(secrets.choice(ALPHABET) for _ in range(10))


def save_redemption(row, now, **changes):
    Redemption.objects.filter(pk=row.pk).update(**changes, updated_at=now)
    for name, value in changes.items():
        setattr(row, name, value)
    row.updated_at = now


def counted_redemptions():
    refunded = PointsTransaction.objects.filter(redemption_id=OuterRef("pk"), type="REDEEM_REFUND")
    return Redemption.objects.annotate(points_refunded=Exists(refunded)).filter(
        Q(status__in=["PENDING_CODE", "ISSUED", "USED"]) | Q(status="EXPIRED", points_refunded=False))


def existing_key(key, shop, customer_id, reward_id):
    row = Redemption.objects.select_related("profile").filter(idempotency_key=key).first()
    if row is None:
        return None
    if (row.profile.shop_id != shop.pk or row.profile.shopify_customer_id != customer_id or row.reward_id != reward_id):
        return outcome("KEY_REUSED")
    return row_outcome(row)


def _rejection(profile, reward, config, now):
    if not config or not config.is_enabled:
        return "PROGRAM_DISABLED"
    if profile.is_blocked:
        return "CUSTOMER_BLOCKED"
    if profile.is_anonymized:
        return "CUSTOMER_ANONYMIZED"
    if not reward.is_active or (reward.active_from and reward.active_from > now) or (reward.active_to and reward.active_to <= now):
        return "REWARD_INACTIVE"
    if profile.available_balance < reward.points_cost:
        return "INSUFFICIENT_POINTS"
    personal = Redemption.objects.filter(profile=profile)
    if personal.filter(created_at__gt=now - timedelta(hours=24), created_at__lte=now).exclude(
            status__in=["FAILED", "CANCELLED"]).count() >= config.max_redemptions_per_day:
        return "DAILY_LIMIT"
    if personal.filter(reward=reward, status="ISSUED").count() >= config.max_unused_codes_per_reward:
        return "UNUSED_CODES_LIMIT"
    counted = counted_redemptions().filter(reward=reward)
    if reward.stock_limit is not None and counted.count() >= reward.stock_limit:
        return "STOCK_EXHAUSTED"
    if reward.per_customer_limit is not None and counted.filter(profile=profile).count() >= reward.per_customer_limit:
        return "CUSTOMER_LIMIT"
    if reward.discount_type == "PERCENTAGE" and reward.max_discount_amount is not None:
        return "UNSUPPORTED_REWARD"
    return None


def redeem_reward(shop, shopify_customer_id, reward_id, client_key, now=None):
    issue_at = now  # None keeps the live clock running across remote I/O.
    now = resolve_now(now)
    if not isinstance(client_key, str) or not client_key.strip() or len(client_key) > 193:
        return outcome("INVALID_KEY")
    try:
        customer_id, reward_id = numeric_id(shopify_customer_id), numeric_id(reward_id)
    except ValueError:
        return outcome("INVALID_ID")
    key = f"redeem:{client_key}"
    previous = existing_key(key, shop, customer_id, reward_id)
    if previous:
        return previous
    try:
        with transaction.atomic():
            profile = CustomerProfile.objects.select_for_update().filter(shop=shop, shopify_customer_id=customer_id).first()
            if not profile:
                return outcome("CUSTOMER_NOT_FOUND")
            previous = existing_key(key, shop, customer_id, reward_id)
            if previous:
                return previous
            # A shared reward lock serializes stock reservation across different customers.
            reward = Reward.objects.select_for_update().filter(shop=shop, pk=reward_id).first()
            if reward is None:
                return outcome("REWARD_NOT_FOUND")
            config = ProgramConfig.get_effective(shop, at=now)
            rejected = _rejection(profile, reward, config, now)
            if rejected:
                return outcome(rejected)
            lots = list(PointsLot.objects.select_for_update().filter(profile=profile, status="AVAILABLE",
                        points_remaining__gt=0).order_by("pk"))
            plan = plan_redemption([RevokeLot(lot.pk, lot.source_type, lot.source_order_id, lot.status,
                        lot.points_remaining, lot.expires_at) for lot in lots], reward.points_cost, profile.available_balance)
            if plan.unrecovered_points:
                return outcome("LEDGER_INCONSISTENT")
            row = None
            for _ in range(10):
                try:
                    with transaction.atomic():
                        row = Redemption.objects.create(profile=profile, reward=reward, points_spent=reward.points_cost,
                            idempotency_key=key, discount_code=generate_code(), status="PENDING_CODE", attempts=0,
                            code_expires_at=now + timedelta(days=reward.code_valid_days))
                    break
                except IntegrityError:
                    if Redemption.objects.filter(idempotency_key=key).exists():
                        raise
            if row is None:
                return outcome("CODE_COLLISION")
            save_redemption(row, now, created_at=now)
            by_id = {lot.pk: lot for lot in lots}
            allocations = []
            for item in plan.allocations:
                lot = by_id[item.lot_id]
                lot.points_remaining -= item.points
                lot.status = item.status_after
                lot.save(update_fields=["points_remaining", "status", "updated_at"])
                allocations.append({"lot_id": lot.pk, "points": item.points,
                                    "expires_at": lot.expires_at.isoformat() if lot.expires_at else None})
            profile.available_balance -= reward.points_cost
            profile.save(update_fields=["available_balance", "updated_at"])
            PointsTransaction.objects.create(profile=profile, type="REDEEM", delta=-reward.points_cost,
                balance_after=profile.available_balance, redemption=row, lot=None, order_id=None,
                idempotency_key=key, actor="system", rule_snapshot=json_values({"code": "redeem",
                    "reward_id": reward.pk, "reward_name": reward.name, "reward_description": reward.description,
                    "points_cost": reward.points_cost,
                    "program_config_id": config.pk, "program_config_version": config.version,
                    "lot_allocations": allocations, "now": now.isoformat(), "discount_settings": {
                        "discount_type": reward.discount_type, "discount_value": reward.discount_value,
                        "min_order_amount": reward.min_order_amount, "max_discount_amount": reward.max_discount_amount,
                        "currency": shop.currency}}))
    except IntegrityError:
        previous = existing_key(key, shop, customer_id, reward_id)
        if previous:
            return previous
        raise
    return issue_redemption(row.pk, issue_at, force=True)


def expected_discount(row):
    entry = PointsTransaction.objects.get(redemption=row, type="REDEEM")
    saved = entry.rule_snapshot["discount_settings"]
    value = Decimal(saved["discount_value"])
    if saved["discount_type"] == "PERCENTAGE":
        value /= Decimal("100")
    return {"title": f"Phần thưởng Loyalty #{row.pk}", "starts_at": entry.rule_snapshot["now"],
        "ends_at": row.code_expires_at.isoformat(), "customer_id": f"gid://shopify/Customer/{row.profile.shopify_customer_id}",
        "discount_type": saved["discount_type"], "discount_value": str(value),
        "min_order_amount": saved["min_order_amount"], "currency": saved["currency"]}


def discount_matches(found, expected):
    settings = found.get("settings") if found else None
    if not settings:
        return False
    try:
        minimum_matches = (settings["min_order_amount"] is None and expected["min_order_amount"] is None) or (
            settings["min_order_amount"] is not None and expected["min_order_amount"] is not None
            and Decimal(settings["min_order_amount"]) == Decimal(expected["min_order_amount"])
            and settings["minimum_currency"] == expected["currency"])
        return (settings["title"] == expected["title"] and settings["customer_id"] == expected["customer_id"]
            and settings["discount_type"] == expected["discount_type"]
            and Decimal(settings["discount_value"]) == Decimal(expected["discount_value"])
            and (expected["discount_type"] != "FIXED_AMOUNT" or settings["currency"] == expected["currency"])
            and parse_now(settings["starts_at"]) == parse_now(expected["starts_at"])
            and settings["ends_at"] is not None and parse_now(settings["ends_at"]) == parse_now(expected["ends_at"])
            and settings["usage_limit"] == 1 and settings["applies_once"]
            and settings["all_items"] and settings["one_time"] and not settings["subscription"]
            and not settings["applies_on_each_item"] and minimum_matches
            and settings["combines_with"] == {"orderDiscounts": False, "productDiscounts": False, "shippingDiscounts": False})
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return False


def pending_due(row, now):
    return row.updated_at <= now - timedelta(minutes=BACKOFF[min(max(row.attempts - 1, 0), 3)])


def _rotate_code(row_id, previous_code, now, claim=None):
    with transaction.atomic():
        row = Redemption.objects.select_for_update().get(pk=row_id)
        if row.status != "PENDING_CODE" or row.discount_code != previous_code or (claim and row.issue_claim != claim):
            return
        for _ in range(10):
            try:
                with transaction.atomic():
                    save_redemption(row, now, discount_code=generate_code())
                return
            except IntegrityError:
                continue
        raise ShopifyTransientError("CODE_COLLISION: Chưa tạo được mã duy nhất.")


def accept_discount(row_id, code, discount_id, now, claim=None):
    with transaction.atomic():
        row = Redemption.objects.select_for_update().get(pk=row_id)
        if row.status == "PENDING_CODE" and row.discount_code == code and (not claim or row.issue_claim == claim):
            save_redemption(row, now, status="ISSUED", shopify_discount_id=discount_id,
                            attempts=row.attempts + 1, last_error="", issue_claim="", issue_claim_until=None)
            from .notifications import notify
            notify(row.profile, "REWARD_CODE_ISSUED", row.pk, redemption=row)
        return row_outcome(row)


def claim_issuance(row_id, now, force=False, reconcile=False):
    with transaction.atomic():
        row = Redemption.objects.select_for_update().get(pk=row_id)
        if (row.status != "PENDING_CODE" or (row.issue_claim_until and row.issue_claim_until > now)
                or (reconcile and row.updated_at > now - timedelta(minutes=1))
                or (not reconcile and not force and not pending_due(row, now))):
            return row, None
        claim = secrets.token_hex(16)
        # Longer than the qcluster worker timeout; retry after a crashed worker
        # can reconcile its deterministic code without a DB lock during HTTP.
        save_redemption(row, now, issue_claim=claim, issue_claim_until=now + timedelta(minutes=5))
        return row, claim


def issue_redemption(row_id, now=None, force=False):
    if connection.in_atomic_block:
        # An outer caller may own wallet locks after our inner phase-one block exits.
        # The callback is discarded on rollback, and runs only after the real commit.
        transaction.on_commit(lambda: issue_redemption(row_id, now, force=force), robust=True)
        return row_outcome(Redemption.objects.get(pk=row_id))
    supplied_now = now
    now = resolve_now(now)
    def completed_at():
        return resolve_now(supplied_now)
    row, claim = claim_issuance(row_id, now, force)
    if not claim:
        return row_outcome(row)
    for collision in range(3):
        row = Redemption.objects.select_related("profile").get(pk=row_id)
        if row.status != "PENDING_CODE" or row.issue_claim != claim:
            return row_outcome(row)
        expected, code = expected_discount(row), row.discount_code
        try:
            found = find_discount_by_code(code)
        except ShopifyError:
            # A failed lookup cannot prove that an earlier timed-out create
            # didn't succeed. Keep the debit reserved for reconciliation.
            return _transient(row.pk, completed_at(), claim)
        try:
            if found and discount_matches(found, expected):
                return accept_discount(row.pk, code, found["id"], completed_at(), claim)
            if found:
                raise DiscountCodeTaken("CODE_TAKEN: Mã đã tồn tại với cấu hình khác.")
            discount_id = create_discount_code(code, expected)
            return accept_discount(row.pk, code, discount_id, completed_at(), claim)
        except DiscountCodeTaken:
            try:
                found = find_discount_by_code(code)
                if discount_matches(found, expected):
                    return accept_discount(row.pk, code, found["id"], completed_at(), claim)
                if collision < 2:
                    _rotate_code(row.pk, code, now, claim)
                    continue
            except ShopifyTransientError:
                return _transient(row.pk, completed_at(), claim)
            except ShopifyError:
                return _transient(row.pk, completed_at(), claim)
            return fail_redemption(row.pk, completed_at(), claim)
        except ShopifyTransientError:
            return _transient(row.pk, completed_at(), claim)
        except ShopifyError:
            return fail_redemption(row.pk, completed_at(), claim)


def _transient(row_id, now, claim=None):
    with transaction.atomic():
        row = Redemption.objects.select_for_update().get(pk=row_id)
        if row.status == "PENDING_CODE" and (not claim or row.issue_claim == claim):
            save_redemption(row, now, attempts=row.attempts + 1,
                last_error="SHOPIFY_TRANSIENT: Shopify tạm thời không sẵn sàng; sẽ thử lại.",
                issue_claim="", issue_claim_until=None)
        return row_outcome(row)


def _refund_locked(profile, row, reason_code, now):
    key = f"redeem_refund:{row.pk}"
    if row.status not in ("FAILED", "CANCELLED", "EXPIRED") or PointsTransaction.objects.filter(idempotency_key=key).exists():
        return False
    original = PointsTransaction.objects.get(redemption=row, type="REDEEM")
    allocations = original.rule_snapshot["lot_allocations"]
    if sum(item["points"] for item in allocations) != row.points_spent:
        raise ValueError("Invalid redemption allocation total")
    _, offset = offset_debt(profile.available_balance, row.points_spent)
    debt_left, restored, lot = offset, [], None
    # Do not extend short-lived allocations to the furthest expiry, or make a
    # finite allocation permanent when mixed with non-expiring points.
    for item in allocations:
        expires = parse_now(item["expires_at"]) if item["expires_at"] else None
        # A source that expired during remote I/O receives the existing 24h
        # compensation grace. Every still-valid source retains its exact expiry.
        if expires and expires <= now:
            expires = now + timedelta(hours=24)
        absorbed = min(debt_left, item["points"])
        debt_left -= absorbed
        remaining = item["points"] - absorbed
        created = PointsLot.objects.create(profile=profile, source_type="REFUND_BACK", points_total=item["points"],
            points_remaining=remaining, status="AVAILABLE" if remaining else "CONSUMED",
            available_at=now, expires_at=expires)
        lot = lot or created
        restored.append({"lot_id": created.pk, "source_lot_id": item["lot_id"], "points": item["points"],
                         "offset": absorbed, "expires_at": expires.isoformat() if expires else None})
    before = profile.available_balance
    profile.available_balance += row.points_spent
    profile.save(update_fields=["available_balance", "updated_at"])
    PointsTransaction.objects.create(profile=profile, type="REDEEM_REFUND", delta=row.points_spent,
        balance_after=profile.available_balance, redemption=row, lot=lot, idempotency_key=key, actor="system",
        rule_snapshot={"code": "redeem_refund", "reason_code": reason_code,
                       "lot_allocations": allocations, "restored_allocations": restored,
                       "offset": offset, "now": now.isoformat()})
    AuditLog.objects.create(shop=profile.shop, actor="system", action="redemption.refunded",
        target_type="Redemption", target_id=str(row.pk), before={"available_balance": before},
        after={"available_balance": profile.available_balance, "points": row.points_spent,
               "offset": offset, "reason_code": reason_code, "now": now.isoformat()})
    return True


def refund_redemption_points(redemption, reason_code, now=None):
    now = resolve_now(now)
    try:
        with transaction.atomic():
            profile = CustomerProfile.objects.select_for_update().get(pk=redemption.profile_id)
            row = Redemption.objects.select_for_update().get(pk=redemption.pk)
            return _refund_locked(profile, row, reason_code, now)
    except IntegrityError:
        if PointsTransaction.objects.filter(idempotency_key=f"redeem_refund:{redemption.pk}").exists():
            return False
        raise


def fail_redemption(row_id, now=None, claim=None):
    now = resolve_now(now)
    current = Redemption.objects.get(pk=row_id)
    with transaction.atomic():
        profile = CustomerProfile.objects.select_for_update().get(pk=current.profile_id)
        row = Redemption.objects.select_for_update().get(pk=row_id)
        if row.status == "PENDING_CODE" and (not claim or row.issue_claim == claim):
            save_redemption(row, now, status="FAILED", attempts=row.attempts + 1,
                            last_error="ISSUE_FAILED: Không thể phát hành mã giảm giá.",
                            issue_claim="", issue_claim_until=None)
            _refund_locked(profile, row, "ISSUE_FAILED", now)
        return row_outcome(row)
