"""Shop-scoped administrative mutations and their audit trail."""
from datetime import timedelta
from decimal import Decimal
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Max, Q
from django.http import Http404

from loyalty.models import AuditLog, CustomerProfile, PointsTransaction, ProgramConfig, ProgramConfigDeletion, Redemption, Reward
from shops.models import Shop
from .errors import LoyaltyError
from .timing import resolve_now

CONFIG_FIELDS = (
    "is_enabled", "spend_per_point", "include_tax", "include_shipping",
    "excluded_product_ids", "excluded_collection_ids", "excluded_channels",
    "pending_days", "expiry_days", "expiry_reminder_days", "max_redemptions_per_day",
    "max_unused_codes_per_reward", "refund_points_on_expired_code",
    "refund_points_on_cancelled_order", "allow_negative_balance",
    "negative_balance_alert_threshold", "welcome_points",
    "welcome_trigger", "birthday_enabled", "birthday_points", "referral_enabled",
    "referral_referrer_points", "referral_referee_points", "referral_max_per_referrer_per_month", "referral_min_order_amount",
    "tiers_enabled", "tier_window_months", "tier_downgrade_grace_days",
)
REWARD_FIELDS = (
    "name", "description", "points_cost", "discount_type", "discount_value",
    "max_discount_amount", "min_order_amount", "code_valid_days", "stock_limit",
    "per_customer_limit", "is_active", "active_from", "active_to", "sort_order",
)


def snapshot(obj, fields):
    return json.loads(json.dumps({field: getattr(obj, field) for field in fields}, cls=DjangoJSONEncoder))


def audit(shop_id, actor, action, obj, before, after):
    AuditLog.objects.create(shop_id=shop_id, actor=actor, action=action,
        target_type=type(obj).__name__, target_id=str(obj.pk), before=before, after=after)


def set_blocked(profile, blocked, reason, actor):
    if blocked and (not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 255):
        raise LoyaltyError("REASON_REQUIRED", "Vui lòng nhập lý do chặn (tối đa 255 ký tự).", 422)
    with transaction.atomic():
        profile = CustomerProfile.objects.select_for_update().get(pk=profile.pk)
        before = snapshot(profile, ("is_blocked", "blocked_reason"))
        profile.is_blocked = blocked
        profile.blocked_reason = reason.strip() if blocked else ""
        profile.save(update_fields=["is_blocked", "blocked_reason", "updated_at"])
        audit(profile.shop_id, actor, "customer.block" if blocked else "customer.unblock", profile,
              before, snapshot(profile, ("is_blocked", "blocked_reason")))
    return profile


def save_reward(shop, data, actor, reward=None):
    with transaction.atomic():
        if reward:
            reward = Reward.objects.select_for_update().get(pk=reward.pk, shop=shop)
            before = snapshot(reward, REWARD_FIELDS)
        else:
            reward, before = Reward(shop=shop), None
        for key, value in data.items():
            setattr(reward, key, value)
        if reward.points_cost is None or reward.points_cost <= 0 or reward.discount_value is None or reward.discount_value <= 0:
            raise LoyaltyError("INVALID_REWARD", "Điểm đổi và giá trị giảm phải lớn hơn 0.", 422)
        if reward.discount_type == "PERCENTAGE":
            if reward.discount_value > Decimal("100"):
                raise LoyaltyError("INVALID_REWARD", "Tỷ lệ giảm không được vượt quá 100%.", 422)
            if reward.max_discount_amount is not None:
                raise LoyaltyError("UNSUPPORTED", "Chưa hỗ trợ phần thưởng phần trăm có trần giảm giá.", 422)
        if reward.active_from and reward.active_to and reward.active_from >= reward.active_to:
            raise LoyaltyError("INVALID_WINDOW", "Ngày kết thúc phải sau ngày bắt đầu.", 422)
        reward.full_clean()
        reward.save()
        audit(shop.pk, actor, "reward.update" if before else "reward.create", reward,
              before, snapshot(reward, REWARD_FIELDS))
    return reward


def create_config(shop, data, actor, now=None):
    now = resolve_now(now)
    with transaction.atomic():
        Shop.objects.select_for_update().get(pk=shop.pk)
        effective = ProgramConfig.get_effective(shop, now)
        values = {key: getattr(effective, key) for key in CONFIG_FIELDS} if effective else {}
        values.update(data)
        effective_from = values.pop("effective_from", now)
        if effective_from < now - timedelta(seconds=60):
            raise LoyaltyError("INVALID_EFFECTIVE_FROM", "Thời điểm hiệu lực không được cũ quá 60 giây.", 422)
        version = (ProgramConfig.objects.filter(shop=shop).aggregate(v=Max("version"))["v"] or 0) + 1
        row = ProgramConfig(shop=shop, version=version, effective_from=effective_from, created_by=actor, **values)
        if any(getattr(row, name) > 1000000 for name in ("welcome_points", "birthday_points", "referral_referrer_points", "referral_referee_points", "referral_max_per_referrer_per_month")) or (row.referral_min_order_amount is not None and row.referral_min_order_amount < 0):
            raise LoyaltyError("INVALID_CONFIG", "Bonus points/cap must be 0..1000000 and minimum spend non-negative.", 422)
        if (row.pending_days > 365 or row.expiry_days > 3650 or row.expiry_reminder_days > 3650
                or row.negative_balance_alert_threshold > 0 or row.negative_balance_alert_threshold < -(10**12)
                or (row.expiry_days and row.expiry_reminder_days > row.expiry_days)):
            raise LoyaltyError("INVALID_CONFIG", "Khoảng thời gian hoặc ngưỡng cảnh báo không hợp lệ.", 422)
        row.full_clean()
        row.save()
        audit(shop.pk, actor, "program_config.create", row, None,
              snapshot(row, (*CONFIG_FIELDS, "version", "effective_from", "created_by")))
    return row


def delete_config(shop, config_id, actor, now=None):
    with transaction.atomic():
        # Serialize with version creation and other deletions for this shop.
        Shop.objects.select_for_update().get(pk=shop.pk)
        row = ProgramConfig.objects.filter(pk=config_id, shop=shop).first()
        if row is None:
            raise Http404
        if ProgramConfigDeletion.objects.filter(config=row).exists():
            return
        # The schedule may have become due while waiting for the shop lock.
        timestamp = resolve_now(now)
        if row.effective_from <= timestamp:
            raise LoyaltyError("CONFIG_ALREADY_EFFECTIVE", "Chỉ được xóa phiên bản chưa đến thời điểm hiệu lực.", 409)
        used = PointsTransaction.objects.filter(profile__shop=shop).filter(
            Q(rule_snapshot__program_config_id=row.pk)
            | Q(rule_snapshot__program_config_version=row.version)
            | Q(rule_snapshot__config_version=row.version)
        ).exists()
        if used:
            raise LoyaltyError("CONFIG_IN_USE", "Phiên bản đã được ghi nhận trong giao dịch điểm nên không thể xóa.", 409)
        ProgramConfigDeletion.objects.create(config=row, actor=actor)
        audit(shop.pk, actor, "program_config.delete", row,
              snapshot(row, (*CONFIG_FIELDS, "version", "effective_from", "created_by")),
              {"deleted": True})


def cancel_redemption(row, actor, now=None):
    from .redemption_jobs import close_issued
    if row.status != Redemption.Status.ISSUED:
        raise LoyaltyError("INVALID_STATUS", "Chỉ có thể hủy mã đang được phát hành.")
    outcome = close_issued(row.pk, status="CANCELLED", now=now)
    if outcome != "cancelled":
        raise LoyaltyError("PENDING_PAYMENT" if outcome == "deferred" else "STATE_CHANGED",
                           "Chưa thể hủy mã; hãy kiểm tra thanh toán và trạng thái mới nhất.")
    row.refresh_from_db()
    audit(row.profile.shop_id, actor, "redemption.cancel", row,
          {"status": "ISSUED"}, {"status": row.status})
    return row
