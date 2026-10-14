"""Bonus grants never carry an order_id, never multiply points, and offset debt."""
from calendar import monthrange, isleap
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.utils.dateparse import parse_datetime

from loyalty.models import CustomerProfile, PointsLot, PointsTransaction, ProgramConfig, AuditLog
from shops.models import Shop
from shops.shopify import graphql, ShopifyError
from .errors import LoyaltyError
from .timing import resolve_now


def shop_zone(shop):
    if not shop.iana_timezone:
        # Cache once, including across concurrent workers. No UTC fallback on failure.
        with transaction.atomic():
            saved = Shop.objects.select_for_update(no_key=True).get(pk=shop.pk)
            if not saved.iana_timezone:
                value = graphql("{ shop { ianaTimezone } }")["shop"]["ianaTimezone"]
                try:
                    ZoneInfo(value)
                except (ZoneInfoNotFoundError, ValueError, TypeError):
                    raise ShopifyError("Invalid shop IANA timezone.") from None
                saved.iana_timezone = value
                saved.save(update_fields=["iana_timezone", "updated_at"])
            shop.iana_timezone = saved.iana_timezone
    return ZoneInfo(shop.iana_timezone)


def aware_date(value):
    if isinstance(value, str):
        try:
            value = parse_datetime(value)
        except ValueError:
            return None
    return value if isinstance(value, datetime) and value.utcoffset() is not None else None


def grant_bonus(profile, points, key, code, source, config, now=None, **details):
    now = resolve_now(now)
    if type(points) is not int or not 0 <= points <= 1000000:
        raise ValueError("Invalid bonus amount")
    if not points:
        return None
    if config.shop_id != profile.shop_id:
        raise ValueError("Bonus configuration belongs to a different shop")
    if not config.is_enabled:
        return None
    expected_source = {"welcome": "WELCOME", "birthday": "BIRTHDAY",
        "referral_referrer": "REFERRAL", "referral_referee": "REFERRAL"}
    if expected_source.get(code) != source:
        raise ValueError("Bonus reason and lot source must agree")
    # Task keys are namespaced so two shops cannot share a grant by accident.
    key = f"shop:{profile.shop_id}:{key}"
    with transaction.atomic():
        profile = CustomerProfile.objects.select_for_update().get(pk=profile.pk)
        existing = PointsTransaction.objects.filter(idempotency_key=key).first()
        if existing:
            return existing
        if profile.is_blocked or profile.is_anonymized or not profile.shop.is_active:
            return None
        offset = min(max(-profile.available_balance, 0), points)
        remaining = points-offset
        lot = PointsLot.objects.create(profile=profile, source_type=source,
            points_total=points, points_remaining=remaining,
            status="AVAILABLE" if remaining else "CONSUMED", available_at=now,
            expires_at=now+timedelta(days=config.expiry_days) if config.expiry_days else None)
        profile.available_balance += points
        profile.lifetime_earned += points
        profile.save(update_fields=["available_balance", "lifetime_earned", "updated_at"])
        entry = PointsTransaction.objects.create(profile=profile, type="EARN", delta=points,
            balance_after=profile.available_balance, lot=lot, idempotency_key=key,
            actor="system:engagement", rule_snapshot={"code": code, "points": points,
                "program_config_id": config.pk, "program_config_version": config.version,
                "expiry_days": config.expiry_days, "debt_offset": offset, **details})
        from .notifications import notify
        kind = {"welcome": "WELCOME_POINTS", "birthday": "BIRTHDAY_POINTS",
                "referral_referrer": "REFERRAL_REWARDED", "referral_referee": "REFERRAL_REWARDED"}[code]
        notify(profile, kind, entry.pk, lot=lot)
        return entry


def welcome_on_signup(profile, payload, now=None):
    now = resolve_now(now)
    created = aware_date(payload.get("created_at"))
    if payload.get("verified_email") is not True or created is None or not now-timedelta(hours=24) <= created <= now:
        return None
    config = ProgramConfig.get_effective(profile.shop, now)
    if not config or not config.is_enabled or config.welcome_trigger != "SIGNUP":
        return None
    return grant_bonus(profile, config.welcome_points, f"welcome:{profile.shopify_customer_id}",
        "welcome", "WELCOME", config, now, trigger="SIGNUP")


def welcome_on_first_order(profile, order_entry, config, now=None):
    # Called only in a successful non-historical earn's transaction, under profile lock.
    if config.welcome_trigger != "FIRST_ORDER" or not config.is_enabled:
        return None
    if PointsTransaction.objects.filter(profile=profile, type="EARN", order_id__isnull=False).exclude(pk=order_entry.pk).exists():
        return None
    return grant_bonus(profile, config.welcome_points, f"welcome:{profile.shopify_customer_id}",
        "welcome", "WELCOME", config, now, trigger="FIRST_ORDER")


def birthday_editable_at(profile):
    value = profile.birthday_set_at
    if value is None:
        return None
    # Twelve calendar months, including leap-day clamping.
    return value.replace(year=value.year+1, day=min(value.day, monthrange(value.year+1, value.month)[1]))


def birthday_state(profile, now=None):
    now = resolve_now(now)
    unlock = birthday_editable_at(profile) if profile else None
    return {"month": profile.birthday_month if profile else None,
        "day": profile.birthday_day if profile else None,
        "editable": unlock is None or now >= unlock, "editable_at": unlock}


def set_birthday(profile, month, day, *, actor, now=None, admin=False):
    now = resolve_now(now)
    try:
        if type(month) is not int or type(day) is not int:
            raise ValueError()
        date(2000, month, day)  # Permit Feb 29; never store the year.
    except (ValueError, TypeError):
        raise LoyaltyError("INVALID_BIRTHDAY", "Enter a valid month/day, including February 29.", 422) from None
    with transaction.atomic():
        profile = CustomerProfile.objects.select_for_update().get(pk=profile.pk)
        if profile.is_blocked or profile.is_anonymized:
            raise LoyaltyError("PROFILE_INELIGIBLE", "Profile is not eligible.", 409)
        if not birthday_state(profile, now)["editable"]:
            # Admin uses the same lock; no untracked override to farm birthday points.
            raise LoyaltyError("BIRTHDAY_LOCKED", "Birthday can be changed only once every 12 months.", 409)
        before = {"month": profile.birthday_month, "day": profile.birthday_day}
        profile.birthday_month, profile.birthday_day, profile.birthday_set_at = month, day, now
        profile.save(update_fields=["birthday_month", "birthday_day", "birthday_set_at", "updated_at"])
        if admin:
            AuditLog.objects.create(shop=profile.shop, actor=actor, action="customer.birthday",
                target_type="CustomerProfile", target_id=str(profile.pk), before=before,
                after={"month": month, "day": day})
        return birthday_state(profile, now)


def grant_birthday(profile, now=None):
    now = resolve_now(now)
    config = ProgramConfig.get_effective(profile.shop, now)
    if not config or not config.is_enabled or not config.birthday_enabled or not config.birthday_points:
        return None
    zone = shop_zone(profile.shop)  # Resolve before taking the profile lock.
    today = now.astimezone(zone).date()
    with transaction.atomic():
        profile = CustomerProfile.objects.select_for_update().get(pk=profile.pk)
        if not profile.birthday_month or not profile.birthday_day or not profile.birthday_set_at or profile.is_blocked or profile.is_anonymized:
            return None
        for age in range(8):  # Today plus a missed day up to 7 days ago, inclusive.
            anniversary = today-timedelta(days=age)
            day = 28 if profile.birthday_month == 2 and profile.birthday_day == 29 and not isleap(anniversary.year) else profile.birthday_day
            if (anniversary.month, anniversary.day) != (profile.birthday_month, day):
                continue
            # No retroactive farming: birthday must have been set by that local date.
            if profile.birthday_set_at.astimezone(zone).date() > anniversary or profile.created_at.astimezone(zone).date() > anniversary:
                return None
            end = datetime.combine(anniversary+timedelta(days=1), time.min, zone)
            if not PointsTransaction.objects.filter(profile=profile, type="EARN", order_id__isnull=False, created_at__lt=end).exists():
                return None
            return grant_bonus(profile, config.birthday_points,
                f"birthday:{profile.shopify_customer_id}:{anniversary.year}", "birthday", "BIRTHDAY", config, now,
                birthday_year=anniversary.year)
    return None


def birthday_batch(now=None, limit=200, after_id=0):
    now = resolve_now(now)
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    profiles = list(CustomerProfile.objects.filter(pk__gt=after_id, shop__is_active=True,
        birthday_month__isnull=False, is_blocked=False, is_anonymized=False).select_related("shop").order_by("pk")[:limit+1])
    granted = 0
    for profile in profiles[:limit]:
        key_before = PointsTransaction.objects.filter(profile=profile, rule_snapshot__code="birthday").count()
        entry = grant_birthday(profile, now)
        granted += bool(entry and PointsTransaction.objects.filter(profile=profile, rule_snapshot__code="birthday").count() > key_before)
    return {"profiles": min(len(profiles), limit), "granted": granted, "has_more": len(profiles)>limit,
        "next_after_id": profiles[min(len(profiles), limit)-1].pk if profiles else after_id}
