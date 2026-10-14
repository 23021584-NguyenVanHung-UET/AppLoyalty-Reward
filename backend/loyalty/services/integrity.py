from django.db import transaction
from django.db.models import F, Sum

from datetime import timedelta

from loyalty.models import CustomerProfile, PointsLot, PointsTransaction, Redemption
from .timing import resolve_now


def check_profile_integrity(profile):
    """Return machine-readable problems, using the same profile lock as writers."""
    with transaction.atomic():
        profile = CustomerProfile.objects.select_for_update().get(pk=profile.pk)
        totals = {row["status"]: row["total"] for row in PointsLot.objects.filter(profile=profile)
                  .values("status").annotate(total=Sum("points_remaining"))}
        ledger = PointsTransaction.objects.filter(profile=profile)
        delta = ledger.aggregate(total=Sum("delta"))["total"] or 0
        pending = totals.get(PointsLot.Status.PENDING, 0)
        available = totals.get(PointsLot.Status.AVAILABLE, 0)
        problems = []
        if delta != profile.available_balance + profile.pending_balance:
            problems.append("ledger_balance_mismatch")
        if pending != profile.pending_balance:
            problems.append("pending_lots_mismatch")
        if profile.pending_balance < 0:
            problems.append("negative_pending_balance")
        if available != max(0, profile.available_balance):
            problems.append("available_lots_mismatch")
        if profile.available_balance < 0 and available > 0:
            problems.append("debt_with_available_lots")
        if PointsLot.objects.filter(profile=profile, status="EXPIRED", points_remaining__gt=0).exists():
            problems.append("expired_lot_has_points")
        if PointsLot.objects.filter(profile=profile, points_remaining__gt=F("points_total")).exists():
            problems.append("lot_remaining_exceeds_total")
        order_totals = ledger.filter(order_id__isnull=False,
            type__in=[PointsTransaction.Type.EARN, PointsTransaction.Type.REVOKE]
        ).values("order_id").annotate(total=Sum("delta"))
        if order_totals.filter(total__lt=0).exists():
            problems.append("order_over_revoked")
        redemption_entries = {}
        for redemption_id, kind, value in ledger.filter(redemption__isnull=False,
                type__in=["REDEEM", "REDEEM_REFUND"]).values_list("redemption_id", "type", "delta"):
            redemption_entries.setdefault(redemption_id, {}).setdefault(kind, []).append(value)
        for row in Redemption.objects.filter(profile=profile).only(
                "pk", "points_spent", "status", "discount_code", "shopify_discount_id"):
            entries = redemption_entries.get(row.pk, {})
            spends = entries.get("REDEEM", [])
            refunds = entries.get("REDEEM_REFUND", [])
            if spends != [-row.points_spent] or len(refunds) > 1 or (refunds and refunds != [row.points_spent]):
                problems.append("redemption_entries_invalid")
            # D10 allows a late paid webhook to mark an already-refunded expired code USED.
            refunded_terminal = row.status in ("FAILED", "CANCELLED") or (
                row.status in ("EXPIRED", "USED") and bool(refunds))
            expected = 0 if refunded_terminal else -row.points_spent
            if sum(spends) + sum(refunds) != expected:
                problems.append("redemption_net_mismatch")
            if refunds and row.status in ("PENDING_CODE", "ISSUED"):
                problems.append("active_redemption_refunded")
            if row.status in ("ISSUED", "USED") and (not row.discount_code or not row.shopify_discount_id):
                problems.append("redemption_missing_code")
        return list(dict.fromkeys(problems))


def check_profile_warnings(profile, now=None):
    now = resolve_now(now)
    lots = PointsLot.objects.filter(profile=profile)
    warnings = []
    from .tiers import qualifying_points
    from loyalty.models import ProgramConfig, Tier
    config = ProgramConfig.get_effective(profile.shop, now)
    if config and config.tiers_enabled:
        metric = qualifying_points(profile, now, config.tier_window_months)
        expected = Tier.objects.filter(shop=profile.shop, is_active=True, min_qualifying_points__lte=metric).order_by("-rank").first()
        if profile.tier_id != (expected.pk if expected else None):
            warnings.append("tier_rank_mismatch")
    if lots.filter(status="PENDING", available_at__lte=now - timedelta(days=1)).exists():
        warnings.append("activation_overdue")
    if lots.filter(status__in=["AVAILABLE", "PENDING"], expires_at__lt=now - timedelta(days=1)).exists():
        warnings.append("expiry_overdue")
    redemptions = Redemption.objects.filter(profile=profile)
    if redemptions.filter(status="PENDING_CODE", created_at__lt=now - timedelta(hours=1)).exists():
        warnings.append("redemption_pending_overdue")
    if redemptions.filter(status="ISSUED", code_expires_at__lt=now - timedelta(hours=2)).exists():
        warnings.append("redemption_expiry_overdue")
    if redemptions.filter(status="USED", points_transactions__type="REDEEM_REFUND").exists():
        warnings.append("redemption_used_after_refund")
    return warnings
