from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from loyalty.models import CustomerProfile, OrderCancellation, PointsLot, PointsTransaction, ProgramConfig
from shops.models import Shop
from shops.shopify import ShopifyError, fetch_order, map_order_snapshot
from .calculation import EarnRules, OrderSnapshot, compute_earn
from .customers import upsert_customer_profile
from .earning_rules import revisions_at, enqueue_tier_evaluation
from .order_state import order_lock


@dataclass
class EarnOutcome:
    status: str
    points: int = 0
    reason: str | None = None
    code: str | None = None


def json_values(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: json_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_values(item) for item in value]
    return value


def earn_points_for_order(shop, order_id, *, historical=False):
    raw_order = fetch_order(order_id)
    if raw_order is None:
        raise ShopifyError("Không tìm thấy đơn hàng trên Shopify.")
    config = ProgramConfig.get_effective(shop)
    if config is None:
        return EarnOutcome("SKIPPED", reason="Chưa có cấu hình chương trình", code="no_config")
    order = map_order_snapshot(raw_order, include_tax=config.include_tax)
    if order.order_id != order_id:
        raise ShopifyError("Mã đơn hàng Shopify trả về không khớp.")
    return earn_points_from_snapshot(shop, order, historical=historical, config=config)


def earn_points_from_snapshot(shop, order: OrderSnapshot, *, historical=False, config=None,
                             now=None, activate_now=True, expiry_mode="from_now", dry_run=False, import_run_id=None):
    """Shared calculation/wallet path. A dry run performs SELECTs only."""
    if expiry_mode not in ("from_now", "none"):
        raise ValueError("Invalid expiry mode")
    config = config or ProgramConfig.get_effective(shop)
    order_id = order.order_id
    if config is None:
        return EarnOutcome("SKIPPED", reason="Chưa có cấu hình chương trình", code="no_config")
    if not config.is_enabled:
        return EarnOutcome("SKIPPED", reason="Chương trình đang tắt", code="program_disabled")
    if not Shop.objects.filter(pk=shop.pk, is_active=True).exists():
        return EarnOutcome("SKIPPED", reason="Shop inactive", code="shop_inactive")
    if config.shop_id != shop.pk:
        raise ValueError("Config/shop mismatch")
    if PointsTransaction.objects.filter(idempotency_key=f"earn:{order_id}").exists():
        return EarnOutcome("DUPLICATE", code="duplicate")
    if order.currency != shop.currency:
        raise ShopifyError("Tiền tệ đơn hàng không khớp tiền tệ cửa hàng.")
    rules = EarnRules(config.spend_per_point, config.include_tax, config.include_shipping,
                      config.excluded_product_ids, config.excluded_collection_ids,
                      config.excluded_channels,
                      allow_test_orders=bool(not historical and settings.DEBUG and settings.LOYALTY_ALLOW_TEST_ORDERS))
    rules.candidate_rules = revisions_at(shop, order.created_at, "rule")
    rules.candidate_campaigns = revisions_at(shop, order.created_at, "campaign")
    result = compute_earn(order, rules)
    if result.skipped_reason and result.breakdown["code"] != "zero_points":
        return EarnOutcome("SKIPPED", reason=result.skipped_reason, code=result.breakdown["code"])
    if dry_run:
        if OrderCancellation.objects.filter(shop=shop, order_id=order_id).exists():
            return EarnOutcome("SKIPPED", reason="Đơn đã hủy", code="cancelled_order")
        profile = CustomerProfile.objects.filter(shop=shop, shopify_customer_id=order.customer_id).select_related("tier").first()
        if profile and (profile.is_anonymized or profile.is_blocked):
            return EarnOutcome("SKIPPED", code="anonymized" if profile.is_anonymized else "blocked")
        if profile and config.tiers_enabled and profile.tier_id and profile.tier.is_active and profile.tier.shop_id == shop.pk:
            rules.tier_id, rules.tier_multiplier = profile.tier_id, profile.tier.multiplier
        result = compute_earn(order, rules)
        return EarnOutcome("SKIPPED" if result.skipped_reason else "EARNED", points=result.points,
                           reason=result.skipped_reason, code=result.breakdown["code"])
    if config.referral_enabled and order.custom_attributes.get("loyalty_ref") and not historical:
        from .engagement import shop_zone
        shop_zone(shop)  # Resolve/cache before wallet locks; never assume UTC for a cap.
    profile, _ = upsert_customer_profile(shop, order.customer_id, email=order.customer_email,
        shopify_created_at=order.customer_created_at)
    key = f"earn:{order_id}"
    try:
        with transaction.atomic(), order_lock(shop.pk, order_id):
            if config.referral_enabled and not historical:
                Shop.objects.select_for_update(no_key=True).get(pk=shop.pk)
            profile = CustomerProfile.objects.select_for_update().get(pk=profile.pk)
            if OrderCancellation.objects.filter(shop=shop, order_id=order_id).exists():
                return EarnOutcome("SKIPPED", reason="Đơn đã hủy", code="cancelled_order")
            if profile.is_blocked:
                return EarnOutcome("SKIPPED", reason="Khách bị chặn", code="blocked")
            if profile.is_anonymized:
                return EarnOutcome("SKIPPED", reason="Hồ sơ khách đã ẩn danh", code="anonymized")
            if PointsTransaction.objects.filter(idempotency_key=key).exists():
                return EarnOutcome("DUPLICATE")
            if config.tiers_enabled and profile.tier_id:
                tier = profile.tier
                if tier.shop_id == shop.pk and tier.is_active:
                    rules.tier_id, rules.tier_multiplier = tier.pk, tier.multiplier
            result = compute_earn(order, rules)
            if result.skipped_reason:
                return EarnOutcome("SKIPPED", reason=result.skipped_reason, code=result.breakdown["code"])
            rules.line_multipliers = result.breakdown["line_multipliers"]
            rules.campaign_id = result.breakdown["campaign_id"]
            rules.campaign_multiplier = Decimal(result.breakdown["campaign_multiplier"])
            rules.candidate_rules = []
            rules.candidate_campaigns = []
            rules.frozen = True
            earned_at = now or timezone.now()
            pending_days = (0 if activate_now else config.pending_days) if historical else config.pending_days
            expiry_days = (0 if expiry_mode == "none" else config.expiry_days) if historical else config.expiry_days
            points = result.points
            remaining = points
            status = PointsLot.Status.PENDING if pending_days else PointsLot.Status.AVAILABLE
            if pending_days:
                profile.pending_balance += points
                # Lifecycle activation offsets outstanding available debt when this lot becomes due.
            else:
                remaining -= min(max(-profile.available_balance, 0), points)
                profile.available_balance += points
                if remaining == 0:
                    status = PointsLot.Status.CONSUMED
            lot = PointsLot.objects.create(
                profile=profile, source_type=PointsLot.SourceType.ORDER, source_order_id=order_id,
                points_total=points, points_remaining=remaining, status=status,
                available_at=earned_at + timedelta(days=pending_days),
                expires_at=earned_at + timedelta(days=expiry_days) if expiry_days else None,
            )
            profile.lifetime_earned += points
            profile.save(update_fields=["available_balance", "pending_balance", "lifetime_earned", "updated_at"])
            order_entry = PointsTransaction.objects.create(
                profile=profile, type=PointsTransaction.Type.EARN, delta=points,
                balance_after=profile.available_balance, lot=lot, order_id=order_id,
                idempotency_key=key, actor="system", rule_snapshot=json_values({
                    "code": "earn", "program_config_id": config.pk, "program_config_version": config.version,
                    "rules": asdict(rules), "eligible_spend": result.eligible_spend, "points": points,
                    "pending_days": pending_days, "expiry_days": expiry_days,
                    "breakdown": result.breakdown, "shop_currency": shop.currency,
                    "earned_at": earned_at.isoformat(),
                    "order_created_at": order.created_at.isoformat() if order.created_at else None,
                    "historical": historical,
                    "import_run_id": import_run_id,
                }),
            )
            if not historical:
                enqueue_tier_evaluation(profile.pk)
            if not historical:
                from .notifications import notify
                notify(profile, "POINTS_EARNED", lot.pk, lot=lot)
                from .engagement import welcome_on_first_order
                from .referrals import record_referral
                welcome_on_first_order(profile, order_entry, config, earned_at)
                # Pass the cached shop instance to avoid resolving timezone under a wallet lock.
                profile.shop = shop
                record_referral(profile, order, lot, config, result.eligible_spend, earned_at)
    except IntegrityError:
        # Only the expected unique-key race is converted to a successful duplicate.
        if PointsTransaction.objects.filter(idempotency_key=key).exists():
            return EarnOutcome("DUPLICATE")
        raise
    return EarnOutcome("EARNED", points=points)
