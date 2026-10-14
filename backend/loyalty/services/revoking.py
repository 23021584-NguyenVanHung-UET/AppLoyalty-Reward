"""Append-only cancellation/refund revocation using the original earning rules."""
import logging
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from loyalty.models import AuditLog, CustomerProfile, PointsLot, PointsTransaction, ProgramConfig
from shops.shopify import ShopifyError, ShopifyTransientError, fetch_order, map_order_snapshot, numeric_id
from webhooks.models import WebhookEvent
from .calculation import EarnRules, compute_earn, multiplier
from .earning_rules import enqueue_tier_evaluation
from .earning import json_values
from .revocation_allocation import RevokeLot, plan_revocation
from .order_state import record_cancellation

logger = logging.getLogger(__name__)
UNRECOVERED_ACTION = "revoke_unrecovered"
REASONS = {
    "not_earned": "Đơn chưa được tích điểm",
    "not_cancelled": "Đơn chưa bị hủy",
    "no_eligible_refund": "Hoàn tiền không làm giảm tiền hàng hợp lệ",
    "no_reduction": "Không có điểm cần thu hồi thêm",
    "unrecovered": "Không còn điểm để thu hồi; đã ghi nhận phần thiếu",
}


@dataclass
class RevokeOutcome:
    status: str
    points: int = 0
    reason: str | None = None
    code: str | None = None


def revoke_points_for_cancel(shop, order_id):
    return _revoke(shop, numeric_id(order_id))


def revoke_points_for_refund(shop, order_id, refund_id):
    return _revoke(shop, numeric_id(order_id), numeric_id(refund_id))


def rules_from_earn(earn):
    """Fail closed on incomplete history instead of applying today's earning config."""
    try:
        saved = earn.rule_snapshot["rules"]
        if isinstance(saved["spend_per_point"], (float, bool)):
            raise ValueError
        rules = EarnRules(
            spend_per_point=Decimal(saved["spend_per_point"]),
            include_tax=saved["include_tax"], include_shipping=saved["include_shipping"],
            excluded_product_ids=saved["excluded_product_ids"],
            excluded_collection_ids=saved["excluded_collection_ids"],
            excluded_channels=saved["excluded_channels"], tier_multiplier=multiplier(saved.get("tier_multiplier", "1"), Decimal("1")),
            allow_test_orders=saved.get("allow_test_orders", False),
            tier_id=saved.get("tier_id"), campaign_id=saved.get("campaign_id"),
            campaign_multiplier=multiplier(saved.get("campaign_multiplier", "1")),
            line_multipliers=saved.get("line_multipliers", {}), frozen=True,
        )
        if (not rules.spend_per_point.is_finite() or rules.spend_per_point <= 0
                or not isinstance(rules.include_tax, bool) or not isinstance(rules.include_shipping, bool)
                or any(not isinstance(value, list) for value in (rules.excluded_product_ids,
                    rules.excluded_collection_ids, rules.excluded_channels))
                or not isinstance(rules.line_multipliers, dict)
                or not isinstance(rules.allow_test_orders, bool)):
            raise ValueError
        for item in rules.line_multipliers.values():
            multiplier(item["multiplier"])
        return rules
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise ShopifyError("Bản chụp quy tắc tích điểm không hợp lệ.") from None


def find_earn(shop, order_id):
    return PointsTransaction.objects.filter(profile__shop=shop, type=PointsTransaction.Type.EARN,
        order_id=order_id, idempotency_key=f"earn:{order_id}").first()


def pending_paid_event(shop, order_id):
    # Resolve lazily: tasks imports handlers, which imports this service.
    from webhooks.tasks import MAX_ATTEMPTS
    return WebhookEvent.objects.filter(shop=shop, topic="orders/paid").filter(
        Q(payload__id=order_id) | Q(payload__id=str(order_id))
        | Q(payload__id=f"gid://shopify/Order/{order_id}")
    ).filter(Q(status__in=[WebhookEvent.Status.RECEIVED, WebhookEvent.Status.PROCESSING])
             | Q(status=WebhookEvent.Status.FAILED, attempts__lt=MAX_ATTEMPTS)).exists()


def uncollected_events(shop):
    return AuditLog.objects.filter(shop=shop, action=UNRECOVERED_ACTION, target_type="RevokeEvent")


def already_processed(shop, key):
    return (PointsTransaction.objects.filter(idempotency_key=key).exists()
            or uncollected_events(shop).filter(target_id=key).exists())


def _revoke(shop, order_id, refund_id=None):
    # All remote work precedes row locks; retries remain owned by the webhook worker.
    raw = fetch_order(order_id)
    if raw is None:
        raise ShopifyError("Không tìm thấy đơn hàng trên Shopify.")
    if refund_id is None and raw.get("cancelledAt"):
        if numeric_id(raw.get("id")) != order_id:
            raise ShopifyError("Mã đơn hàng Shopify trả về không khớp.")
        record_cancellation(shop, order_id)
    earn = find_earn(shop, order_id)
    if earn is None:
        pending = pending_paid_event(shop, order_id)
        # An earn may have committed between the first lookup and the event lookup.
        earn = find_earn(shop, order_id)
        if earn is None:
            if pending:
                raise ShopifyTransientError("Đang chờ xử lý tích điểm của đơn hàng; sẽ thử thu hồi lại.")
            return RevokeOutcome("SKIPPED", reason=REASONS["not_earned"], code="not_earned")
    rules = rules_from_earn(earn)
    order = map_order_snapshot(raw, include_tax=rules.include_tax)
    if order.order_id != order_id:
        raise ShopifyError("Mã đơn hàng Shopify trả về không khớp.")
    if order.currency != shop.currency:
        raise ShopifyError("Tiền tệ đơn hàng không khớp tiền tệ cửa hàng.")
    key = f"revoke:order:{order_id}" if refund_id is None else f"revoke:refund:{refund_id}"
    if refund_id is None and not order.cancelled:
        return RevokeOutcome("NOOP", reason=REASONS["not_cancelled"], code="not_cancelled")
    refund = None
    if refund_id is not None:
        refund = next((item for item in order.refund_summaries if item.refund_id == refund_id), None)
        if refund is None:
            raise ShopifyTransientError("Chưa thấy lần hoàn tiền trong đơn hàng; sẽ thử lại.")
    result = compute_earn(order, rules)
    target = 0 if refund_id is None or result.skipped_reason else result.points
    try:
        with transaction.atomic():
            profile = CustomerProfile.objects.select_for_update().get(pk=earn.profile_id)
            if already_processed(shop, key):
                return RevokeOutcome("DUPLICATE")
            earn = find_earn(shop, order_id)
            revokes = PointsTransaction.objects.filter(profile=profile, order_id=order_id,
                                                       type=PointsTransaction.Type.REVOKE)
            earned = earn.delta
            revoked = -(revokes.aggregate(total=Sum("delta"))["total"] or 0)
            net_held = earned - revoked
            if earned <= 0 or net_held < 0:
                raise ShopifyError("Sổ cái của đơn hàng không nhất quán; không thể thu hồi.")
            if refund is not None and not refund.has_line_items and not (rules.include_shipping and refund.has_shipping):
                return RevokeOutcome("NOOP", reason=REASONS["no_eligible_refund"], code="no_eligible_refund")
            waived = sum(row.get("unrecovered_points", 0) for row in revokes.values_list("rule_snapshot", flat=True))
            waived += sum(row.get("unrecovered_points", 0) for row in uncollected_events(shop)
                .filter(after__profile_id=profile.pk, after__order_id=order_id)
                .exclude(after__contains={"recorded_in_ledger": True}).values_list("after", flat=True))
            # Forgiven shortages are settled, not debt to collect on later earns/events.
            requested = max(0, net_held - target - waived)
            if not requested:
                return RevokeOutcome("NOOP", reason=REASONS["no_reduction"], code="no_reduction")
            config = ProgramConfig.get_effective(shop)
            allow_negative = config.allow_negative_balance if config else True
            threshold = config.negative_balance_alert_threshold if config else -500
            lots = list(PointsLot.objects.select_for_update().filter(profile=profile, points_remaining__gt=0)
                .filter(Q(status=PointsLot.Status.AVAILABLE) | Q(status=PointsLot.Status.PENDING,
                    source_type=PointsLot.SourceType.ORDER, source_order_id=order_id)).order_by("pk"))
            prior_allocations = {}
            for snapshot in PointsTransaction.objects.filter(profile=profile, type=PointsTransaction.Type.REVOKE
                    ).values_list("rule_snapshot", flat=True):
                for item in snapshot.get("lot_allocations", []):
                    lot_id = item["lot_id"]
                    prior_allocations[lot_id] = prior_allocations.get(lot_id, 0) + item["points"]
            states = [RevokeLot(lot.pk, lot.source_type, lot.source_order_id, lot.status, lot.points_remaining,
                lot.expires_at, lot.points_total - lot.points_remaining - prior_allocations.get(lot.pk, 0) > 0)
                for lot in lots]
            plan = plan_revocation(states, order_id, requested, allow_negative_balance=allow_negative,
                                   available_balance=profile.available_balance)
            taken = requested - plan.unrecovered_points
            by_id = {lot.pk: lot for lot in lots}
            own_taken = 0
            for item in plan.allocations:
                lot = by_id[item.lot_id]
                lot.points_remaining -= item.points
                lot.status = item.status_after
                lot.save(update_fields=["points_remaining", "status", "updated_at"])
                if item.lot_status_before == PointsLot.Status.PENDING:
                    profile.pending_balance -= item.points
                else:
                    profile.available_balance -= item.points
                if item.is_order_lot:
                    own_taken += item.points
            profile.available_balance -= plan.debt_created
            profile.lifetime_earned = max(0, profile.lifetime_earned - own_taken)
            snapshot = json_values({
                "code": "revoke_cancel" if refund_id is None else "revoke_refund",
                "program_config_id": config.pk if config else None,
                "program_config_version": config.version if config else None,
                "rules": asdict(rules), "earned_points": earned, "already_revoked": revoked,
                "net_held": net_held, "target": target, "requested_revoke_points": requested,
                "revoke_points": taken, "breakdown": result.breakdown,
                "skip_code": result.breakdown["code"] if result.skipped_reason else None,
                "lot_allocations": [{"lot_id": item.lot_id, "points": item.points,
                    "lot_status_before": item.lot_status_before} for item in plan.allocations],
                "debt_created": plan.debt_created, "unrecovered_points": plan.unrecovered_points,
                "previous_unrecovered_points": waived, "shop_currency": shop.currency,
                "processed_at": timezone.now().isoformat(),
            })
            if taken:
                profile.save(update_fields=["available_balance", "pending_balance", "lifetime_earned", "updated_at"])
                PointsTransaction.objects.create(profile=profile, type=PointsTransaction.Type.REVOKE,
                    delta=-taken, balance_after=profile.available_balance, lot=None, order_id=order_id,
                    refund_id=refund_id, idempotency_key=key, actor="system", rule_snapshot=snapshot)
            if plan.unrecovered_points:
                # Zero deltas are forbidden; preserve the shortage and event key in existing history.
                AuditLog.objects.create(shop=shop, actor="system", action=UNRECOVERED_ACTION,
                    target_type="RevokeEvent", target_id=key,
                    after={**snapshot, "profile_id": profile.pk, "order_id": order_id, "refund_id": refund_id,
                           "recorded_in_ledger": bool(taken)})
            if plan.unrecovered_points:
                logger.warning("Điểm chưa thu hồi: profile_id=%s order_id=%s unrecovered_points=%s",
                               profile.pk, order_id, plan.unrecovered_points)
            if profile.available_balance < threshold:
                logger.warning("Số dư dưới ngưỡng cảnh báo: profile_id=%s available_balance=%s",
                               profile.pk, profile.available_balance)
            enqueue_tier_evaluation(profile.pk)
    except IntegrityError:
        if already_processed(shop, key):
            return RevokeOutcome("DUPLICATE")
        raise
    if not taken:
        return RevokeOutcome("NOOP", reason=REASONS["unrecovered"], code="unrecovered")
    return RevokeOutcome("REVOKED", points=taken)
