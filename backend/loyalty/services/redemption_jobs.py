"""Retry issuance and close codes; remote calls always precede final DB locks."""
import logging
from dataclasses import dataclass, asdict
from datetime import timedelta

from django.db import connection, transaction

from loyalty.models import CustomerProfile, PointsTransaction, ProgramConfig, Redemption
from shops.shopify import ShopifyError, ShopifyTransientError, deactivate_discount, find_discount_by_code
from .lifecycle import validate_limit
from .redemption import (accept_discount, discount_matches, expected_discount, fail_redemption,
                         issue_redemption, pending_due, save_redemption, _refund_locked, _transient,
                         claim_issuance)
from .timing import resolve_now

logger = logging.getLogger(__name__)


def payload_codes(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("discount_codes"), list):
        return set()
    return {item["code"].casefold() for item in payload["discount_codes"]
            if isinstance(item, dict) and isinstance(item.get("code"), str)}


def pending_paid_code(row):
    from webhooks.models import WebhookEvent
    from webhooks.tasks import MAX_ATTEMPTS
    events = WebhookEvent.objects.filter(shop_id=row.profile.shop_id, topic="orders/paid").filter(
        status__in=["RECEIVED", "PROCESSING", "FAILED"])
    for status, attempts, payload in events.values_list("status", "attempts", "payload").iterator():
        if status != "FAILED" or attempts < MAX_ATTEMPTS:
            if (row.discount_code or "").casefold() in payload_codes(payload):
                return True
    return False


def mark_codes_used(shop, order_id, payload, now=None):
    now = resolve_now(now)
    codes = payload_codes(payload)
    if not codes:
        return 0
    # Filter case-insensitively in SQL, keeping both codes and customer data out of logs.
    from django.db.models import Q
    match = Q()
    for code in codes:
        match |= Q(discount_code__iexact=code)
    rows = list(Redemption.objects.filter(match, profile__shop=shop,
        status__in=["ISSUED", "EXPIRED", "USED"]).values_list("pk", "profile_id"))
    changed = 0
    for row_id, profile_id in rows:
        with transaction.atomic():
            CustomerProfile.objects.select_for_update().get(pk=profile_id)
            row = Redemption.objects.select_for_update().get(pk=row_id)
            if row.status == "USED":
                if row.used_order_id != order_id:
                    logger.warning("redemption_id=%s order_id=%s code=USED_ON_OTHER_ORDER", row.pk, order_id)
                continue
            if row.status not in ("ISSUED", "EXPIRED"):
                continue
            if PointsTransaction.objects.filter(redemption=row, type="REDEEM_REFUND").exists():
                logger.warning("redemption_id=%s order_id=%s code=USED_AFTER_REFUND", row.pk, order_id)
            save_redemption(row, now, status="USED", used_order_id=order_id)
            changed += 1
    return changed


def close_issued(row_id, status, now=None):
    """Deactivate first, then atomically close and conditionally refund."""
    if connection.in_atomic_block:
        transaction.on_commit(lambda: close_issued(row_id, status, now), robust=True)
        return "deferred"
    now = resolve_now(now)
    if status not in ("EXPIRED", "CANCELLED"):
        raise ValueError("INVALID_STATUS: Trạng thái đóng mã không hợp lệ.")
    row = Redemption.objects.select_related("profile").get(pk=row_id)
    if row.status != "ISSUED":
        return "skipped"
    if status == "EXPIRED" and (not row.code_expires_at or row.code_expires_at + timedelta(minutes=60) > now):
        return "skipped"
    if pending_paid_code(row):
        return "deferred"
    deactivate_discount(row.shopify_discount_id)
    with transaction.atomic():
        profile = CustomerProfile.objects.select_for_update().get(pk=row.profile_id)
        row = Redemption.objects.select_for_update().get(pk=row_id)
        if row.status != "ISSUED":
            return "skipped"
        if pending_paid_code(row):
            return "deferred"
        config = ProgramConfig.get_effective(profile.shop, at=now)
        save_redemption(row, now, status=status, last_error="")
        if status == "CANCELLED" or (config and config.refund_points_on_expired_code):
            _refund_locked(profile, row, "CODE_EXPIRED" if status == "EXPIRED" else "CANCELLED", now)
        return status.lower()


@dataclass
class RedemptionBatchResult:
    processed: int = 0
    issued: int = 0
    pending: int = 0
    failed: int = 0
    expired: int = 0
    skipped: int = 0
    deferred: int = 0
    errors: int = 0


def redemption_plan(now=None, limit=200):
    now = resolve_now(now)
    validate_limit(limit)
    from django.db.models import Q
    terminal = Q(attempts__gte=5) | Q(created_at__lt=now - timedelta(hours=6))
    retry = (Q(attempts__lte=1, updated_at__lte=now - timedelta(minutes=1))
             | Q(attempts=2, updated_at__lte=now - timedelta(minutes=5))
             | Q(attempts=3, updated_at__lte=now - timedelta(minutes=15))
             | Q(attempts=4, updated_at__lte=now - timedelta(minutes=60)))
    due = (Q(status="PENDING_CODE") & ((terminal & Q(updated_at__lte=now - timedelta(minutes=1)))
           | (~terminal & retry))) | Q(status="ISSUED", code_expires_at__lte=now - timedelta(minutes=60))
    rows = Redemption.objects.filter(due).filter(Q(issue_claim_until__isnull=True) | Q(issue_claim_until__lte=now)
        ).order_by("updated_at", "pk")[:min(limit, 200)]
    plan = []
    for row in rows.iterator():
        if len(plan) >= limit:
            break
        if row.status == "PENDING_CODE":
            terminal = row.attempts >= 5 or row.created_at < now - timedelta(hours=6)
            # Even exhausted attempts wait a minute, so an in-flight response can finalize.
            if (terminal and row.updated_at <= now - timedelta(minutes=1)) or (not terminal and pending_due(row, now)):
                plan.append((row.pk, "reconcile" if terminal else "retry"))
        elif row.code_expires_at and row.code_expires_at + timedelta(minutes=60) <= now:
            plan.append((row.pk, "expire"))
    return plan


def reconcile_pending(row_id, now):
    if connection.in_atomic_block:
        transaction.on_commit(lambda: reconcile_pending(row_id, now), robust=True)
        return "deferred"
    row, claim = claim_issuance(row_id, now, reconcile=True)
    if not claim:
        return "skipped"
    try:
        found = find_discount_by_code(row.discount_code)
    except ShopifyError:
        _transient(row.pk, now, claim)
        raise
    if discount_matches(found, expected_discount(row)):
        return accept_discount(row.pk, row.discount_code, found["id"], now, claim).status.lower()
    return fail_redemption(row.pk, now, claim).reason_code.lower()


def process_redemptions_services(now=None, limit=200):
    now = resolve_now(now)
    result = RedemptionBatchResult()
    for row_id, action in redemption_plan(now, limit):
        result.processed += 1
        try:
            if action == "expire":
                state = close_issued(row_id, "EXPIRED", now)
            elif action == "reconcile":
                state = reconcile_pending(row_id, now)
            else:
                state = issue_redemption(row_id, now).status.lower()
            field = "failed" if state in ("issue_failed", "rejected") else state
            if field not in asdict(result) or field in ("processed", "errors"):
                field = "skipped"
            setattr(result, field, getattr(result, field) + 1)
        except ShopifyTransientError:
            result.errors += 1
            logger.warning("redemption_id=%s code=SHOPIFY_TRANSIENT", row_id)
        except ShopifyError:
            # Failure to inspect/deactivate a code cannot establish that it is safe to refund.
            result.errors += 1
            logger.warning("redemption_id=%s code=SHOPIFY_UNCONFIRMED", row_id)
        except Exception:
            result.errors += 1
            logger.error("redemption_id=%s code=PROCESSING_ERROR", row_id)
    return result


def redemption_summary(result):
    return (f"REDEMPTIONS_PROCESSED: Đổi thưởng: đã xét {result.processed}; phát hành {result.issued}; chờ {result.pending}; "
            f"thất bại {result.failed}; hết hạn {result.expired}; hoãn {result.deferred}; "
            f"bỏ qua {result.skipped}; lỗi {result.errors}.")
