"""Tier qualification uses net order points, independent of spend/redemption."""
from calendar import monthrange
from datetime import timedelta, timezone as utc_timezone

from django.db import transaction
from django.db.models import Sum

from loyalty.models import AuditLog, CustomerProfile, PointsTransaction, ProgramConfig, Tier, TierHistory
from .timing import resolve_now


def window_start(now, months):
    now = now.astimezone(utc_timezone.utc)
    total = now.year * 12 + now.month - 1 - months
    year, month = divmod(total, 12)
    month += 1
    return now.replace(year=year, month=month, day=min(now.day, monthrange(year, month)[1]))


def qualifying_points(profile, now, months=0):
    if profile.pk is None:
        return 0
    query = PointsTransaction.objects.filter(profile=profile, type__in=["EARN", "REVOKE"],
        order_id__isnull=False, created_at__lte=now)
    if months:
        query = query.filter(created_at__gte=window_start(now, months))
    return max(0, query.aggregate(total=Sum("delta"))["total"] or 0)


def tier_state(profile, now=None):
    now = resolve_now(now)
    config = ProgramConfig.get_effective(profile.shop, now)
    if config is None or not config.tiers_enabled:
        return {"enabled": False, "name": None, "multiplier": "1.00", "qualifying_points": 0,
                "points_to_next_tier": None, "next_tier": None, "progress_points": 0, "progress_target": 0,
                "grace": {"active": False, "deadline": None, "points_to_keep": 0}}
    metric = qualifying_points(profile, now, config.tier_window_months)
    current = profile.tier if profile.tier_id and profile.tier.shop_id == profile.shop_id else None
    next_tier = Tier.objects.filter(shop=profile.shop, is_active=True,
        rank__gt=current.rank if current else -1).order_by("rank").first()
    base = current.min_qualifying_points if current else 0
    return {"enabled": True, "name": current.name if current else None,
        "multiplier": str(current.multiplier) if current and current.is_active else "1.00",
        "qualifying_points": metric, "points_to_next_tier": max(0, next_tier.min_qualifying_points-metric) if next_tier else None,
        "next_tier": next_tier.name if next_tier else None, "progress_points": max(0, metric-base),
        "progress_target": max(0, next_tier.min_qualifying_points-base) if next_tier else 0,
        "grace": grace_state(profile, config, metric, now)}


def grace_state(profile, config, metric, now):
    """The persisted latest qualifying drop owns the grace clock; GET never resets it."""
    inactive = {"active": False, "deadline": None, "points_to_keep": 0}
    tier, drop = profile.tier, profile.tier_drop_since
    if (not config or not config.is_enabled or not config.tiers_enabled or not tier or not tier.is_active
            or tier.shop_id != profile.shop_id or not drop or profile.is_blocked or profile.is_anonymized
            or config.tier_downgrade_grace_days == 0 or metric >= tier.min_qualifying_points):
        return inactive
    deadline = drop + timedelta(days=config.tier_downgrade_grace_days)
    if not drop <= now < deadline:
        return inactive
    return {"active": True, "deadline": deadline.isoformat(),
            "points_to_keep": tier.min_qualifying_points - metric}


def evaluate_profile_tier(profile, now=None, *, allow_downgrade=False, dry_run=False, notify_upgrade=True):
    now = resolve_now(now)
    with transaction.atomic():
        profile = CustomerProfile.objects.select_for_update(of=("self",)).select_related("tier", "shop").get(pk=profile.pk)
        config = ProgramConfig.get_effective(profile.shop, now)
        if config is None or not config.tiers_enabled or profile.is_anonymized:
            return {"profile_id": profile.pk, "changed": False, "disabled": True}
        if profile.tier_evaluated_at and now < profile.tier_evaluated_at and not dry_run:
            raise ValueError("Cannot evaluate tiers before the last committed evaluation. Use --dry-run for historical previews.")
        metric = qualifying_points(profile, now, config.tier_window_months)
        target = Tier.objects.filter(shop=profile.shop, is_active=True, min_qualifying_points__lte=metric
            ).order_by("-rank").first()
        current = profile.tier
        if current and current.shop_id != profile.shop_id:
            raise ValueError("Customer tier belongs to a different shop.")
        def rank(tier):
            return tier.rank if tier else -1
        down = rank(target) < rank(current) or bool(current and not current.is_active and rank(target) <= rank(current))
        previous_drop = drop = profile.tier_drop_since
        if down:
            if drop is None or metric < profile.tier_last_qualifying_points:
                drop = now
        else:
            drop = None
        change = rank(target) > rank(current) or (down and allow_downgrade and drop is not None
            and now >= drop + timedelta(days=config.tier_downgrade_grace_days))
        change = change and profile.tier_id != (target.pk if target else None)
        result = {"profile_id": profile.pk, "changed": bool(change), "qualifying_points": metric,
            "from_tier": profile.tier_id, "to_tier": target.pk if target else None,
            "drop_since": drop.isoformat() if drop else None}
        if dry_run:
            return result
        if change:
            reason = "daily_downgrade" if down else "upgrade"
            history = TierHistory.objects.create(profile=profile, from_tier=current, to_tier=target,
                reason=reason, qualifying_points=metric, created_at=now)
            if not down and target and notify_upgrade:
                from .notifications import notify
                notify(profile, "TIER_UPGRADED", history.pk, tier_history=history)
            AuditLog.objects.create(shop=profile.shop, actor="system:tiers", action="tier.change",
                target_type="CustomerProfile", target_id=str(profile.pk),
                before={"tier_id": profile.tier_id}, after={"tier_id": result["to_tier"],
                    "reason": reason, "qualifying_points": metric, "as_of": now.isoformat()})
            profile.tier = target
            profile.tier_since = now
            drop = None
        profile.tier_drop_since = drop
        profile.tier_last_qualifying_points = metric
        profile.tier_evaluated_at = now
        profile.save(update_fields=["tier", "tier_since", "tier_drop_since", "tier_last_qualifying_points", "tier_evaluated_at", "updated_at"])
        if notify_upgrade and drop != previous_drop and grace_state(profile, config, metric, now)["active"]:
            from .notifications import notify_tier_grace
            notify_tier_grace(profile, "started")
        return result


def evaluate_all(now=None, limit=500, *, dry_run=False, after_id=0):
    now = resolve_now(now)
    if not 1 <= limit <= 10000:
        raise ValueError("limit must be between 1 and 10000")
    profiles = list(CustomerProfile.objects.filter(pk__gt=after_id, shop__is_active=True,
        is_anonymized=False).order_by("pk")[:limit+1])
    results = [evaluate_profile_tier(profile, now, allow_downgrade=True, dry_run=dry_run) for profile in profiles[:limit]]
    return {"scanned": len(results), "changed": sum(row["changed"] for row in results), "results": results,
        "truncated": len(profiles)>limit, "next_after_id": profiles[min(len(profiles),limit)-1].pk if profiles else after_id}
