"""Bounded, shop-scoped read models for HTTP clients."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import time
import uuid

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.db.models import Count, Min, Prefetch, Q, Sum
from django.utils import timezone

from loyalty.models import AuditLog, CustomerProfile, PointsLot, PointsTransaction, Redemption, Reward
from webhooks.models import WebhookEvent
from .errors import LoyaltyError
from .integrity import check_profile_integrity, check_profile_warnings


def dashboard(shop, days=30):
    now = timezone.now()
    rows = PointsTransaction.objects.filter(profile__shop=shop,
        created_at__gte=now-timedelta(days=days), created_at__lte=now).values("type").annotate(
            count=Count("pk"), delta=Sum("delta"))
    totals = {row["type"]: {"count": row["count"], "points": abs(row["delta"])} for row in rows}
    profiles = CustomerProfile.objects.filter(shop=shop).aggregate(
        with_balance=Count("pk", filter=Q(available_balance__gt=0) | Q(pending_balance__gt=0)),
        in_debt=Count("pk", filter=Q(available_balance__lt=0)),
        debt=Sum("available_balance", filter=Q(available_balance__lt=0)))
    lots = {row["status"]: row["total"] for row in PointsLot.objects.filter(profile__shop=shop,
        status__in=["AVAILABLE", "PENDING"]).values("status").annotate(total=Sum("points_remaining"))}

    def statuses(query):
        return {row["status"]: row["count"] for row in query.values("status").annotate(count=Count("pk"))}

    return dict(days=days, period={name: totals.get(kind, {"count": 0, "points": 0})
        for name, kind in (("earned", "EARN"), ("revoked", "REVOKE"), ("redeemed", "REDEEM"), ("expired", "EXPIRE"))},
        liability={"available": lots.get("AVAILABLE", 0), "pending": lots.get("PENDING", 0)},
        profiles_with_balance=profiles["with_balance"], profiles_in_debt=profiles["in_debt"],
        debt_points=abs(profiles["debt"] or 0), redemptions=statuses(Redemption.objects.filter(profile__shop=shop)),
        webhook_events=statuses(WebhookEvent.objects.filter(shop=shop)),
        tier_distribution=list(CustomerProfile.objects.filter(shop=shop).values("tier_id", "tier__name")
            .annotate(count=Count("pk")).order_by("tier_id")))


def customer_query(shop, search="", has_balance=None, in_debt=None, blocked=None):
    query = CustomerProfile.objects.filter(shop=shop).select_related("tier")
    if search:
        query = query.filter(Q(email__icontains=search) | Q(first_name__icontains=search)
            | Q(last_name__icontains=search) | Q(shopify_customer_id__icontains=search))
    for enabled, condition in ((has_balance, Q(available_balance__gt=0) | Q(pending_balance__gt=0)),
                               (in_debt, Q(available_balance__lt=0)), (blocked, Q(is_blocked=True))):
        if enabled is not None:
            query = query.filter(condition if enabled else ~condition)
    return query


def lot_counts(profile):
    return {row["status"]: row["count"] for row in profile.points_lots.values("status").annotate(count=Count("pk"))}


def mask_payload(value):
    if isinstance(value, list):
        return [mask_payload(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            name = key.lower()
            sensitive = (name in {"name", "first_name", "last_name", "customer_name", "full_name",
                                 "discount_codes", "discount_code", "code", "authorization", "signature"}
                or any(part in name for part in ("email", "phone", "address", "token", "secret", "password")))
            result[key] = "[MASKED]" if sensitive else mask_payload(item)
        return result
    return value


def integrity_report(shop, limit=None, after_id=0):
    maximum = min(max(settings.LOYALTY_INTEGRITY_MAX_PROFILES, 1), 100)
    limit = min(limit or maximum, maximum)
    budget = min(max(settings.LOYALTY_INTEGRITY_TIMEOUT_MS, 100), 10000)
    deadline = time.monotonic() + budget / 1000
    problems, warnings, scanned = [], [], 0

    def bounded_execute(execute, sql, params, many, context):
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise LoyaltyError("INTEGRITY_TIMEOUT", "Kiểm tra đã hết thời gian; hãy giảm số hồ sơ.", 503)
        if connection.vendor == "postgresql":
            execute("SELECT set_config('statement_timeout', %s, true)", [f"{remaining}ms"], False, context)
            execute("SELECT set_config('lock_timeout', '100ms', true)", None, False, context)
        return execute(sql, params, many, context)

    try:
        with transaction.atomic():
            with connection.execute_wrapper(bounded_execute):
                profiles = list(CustomerProfile.objects.filter(shop=shop, pk__gt=after_id).order_by("pk")[:limit+1])
        for profile in profiles[:limit]:
            with transaction.atomic():
                with connection.execute_wrapper(bounded_execute):
                    issues = check_profile_integrity(profile)
                    cautions = check_profile_warnings(profile)
            scanned += 1
            if issues:
                problems.append({"profile_id": profile.pk, "codes": issues})
            if cautions:
                warnings.append({"profile_id": profile.pk, "codes": cautions})
        return dict(scanned=scanned, problems=problems, warnings=warnings,
                    truncated=len(profiles) > limit, next_after_id=profiles[scanned-1].pk if scanned else after_id)
    except DatabaseError:
        raise LoyaltyError("INTEGRITY_BUSY", "Dữ liệu đang bận hoặc kiểm tra đã hết thời gian. Hãy thử lại.", 503) from None


def proxy_profile(shop, customer_id):
    return CustomerProfile.objects.filter(shop=shop, shopify_customer_id=customer_id).first()


def proxy_me(shop, customer_id):
    profile = proxy_profile(shop, customer_id)
    empty = dict(available=0, pending=0, debt=0, expiring_points=0, earliest_expiry=None,
                 ledger=[], unused_issued_codes=0)
    if profile is None:
        from .tiers import tier_state
        from .engagement import birthday_state
        empty["birthday"] = birthday_state(None)
        empty["tier"] = tier_state(CustomerProfile(shop=shop), timezone.now())
        return empty
    from .tiers import tier_state
    now = timezone.now()
    expires = profile.points_lots.filter(status__in=["AVAILABLE", "PENDING"], points_remaining__gt=0,
        expires_at__gt=now, expires_at__lte=now+timedelta(days=30)).aggregate(
            points=Sum("points_remaining"), earliest=Min("expires_at"))
    entries = profile.points_transactions.order_by("-created_at", "-pk")[:20]
    from .engagement import birthday_state
    return dict(birthday=birthday_state(profile, now), tier=tier_state(profile, now), available=max(0, profile.available_balance), pending=profile.pending_balance,
        debt=max(0, -profile.available_balance), expiring_points=expires["points"] or 0,
        earliest_expiry=expires["earliest"], unused_issued_codes=profile.redemptions.filter(status="ISSUED").count(),
        ledger=[dict(type=row.type, delta=row.delta, date=row.created_at,
            reference=uuid.uuid5(uuid.NAMESPACE_URL, f"loyalty:ledger:{shop.pk}:{profile.pk}:{row.pk}").hex)
            for row in entries])


def proxy_rewards(shop, customer_id):
    profile = proxy_profile(shop, customer_id)
    balance = max(0, profile.available_balance) if profile else 0
    now = timezone.now()
    rewards = Reward.objects.filter(shop=shop, is_active=True).filter(
        Q(active_from__isnull=True) | Q(active_from__lte=now),
        Q(active_to__isnull=True) | Q(active_to__gt=now)).order_by("sort_order", "pk")
    return [dict(id=row.pk, name=row.name, description=row.description, points_cost=row.points_cost,
        discount_type=row.discount_type, discount_value=str(row.discount_value),
        min_order_amount=str(row.min_order_amount) if row.min_order_amount is not None else None,
        affordable=balance >= row.points_cost) for row in rewards]


def proxy_redemptions(shop, customer_id):
    rows = Redemption.objects.filter(profile__shop=shop, profile__shopify_customer_id=customer_id
        ).order_by("-created_at", "-pk")[:20]
    return [dict(reference=uuid.uuid5(uuid.NAMESPACE_URL, f"loyalty:redemption:{row.pk}").hex,
        reward_id=row.reward_id, points_spent=row.points_spent, status=row.status,
        created_at=row.created_at, expires_at=row.code_expires_at,
        **({"code": row.discount_code} if row.status == "ISSUED" else {})) for row in rows]


def customer_offers(shop, customer_id):
    now = timezone.now()
    rows = Redemption.objects.filter(profile__shop=shop, profile__shopify_customer_id=customer_id,
        status="ISSUED", discount_code__isnull=False).exclude(discount_code="").filter(
        Q(code_expires_at__isnull=True) | Q(code_expires_at__gt=now)
    ).order_by("code_expires_at", "-created_at", "-pk").prefetch_related(Prefetch(
        "points_transactions", queryset=PointsTransaction.objects.filter(type="REDEEM",
            profile__shop=shop, profile__shopify_customer_id=customer_id).only("redemption_id", "rule_snapshot"),
        to_attr="offer_entries"))[:20]
    result = []
    for row in rows:
        if len(row.offer_entries) != 1:
            continue
        snapshot = row.offer_entries[0].rule_snapshot
        if not isinstance(snapshot, dict):
            continue
        saved = snapshot.get("discount_settings", {})
        if not isinstance(saved, dict):
            continue
        try:
            kind, currency = saved["discount_type"], saved["currency"]
            value = Decimal(str(saved["discount_value"]))
            minimum = Decimal(str(saved["min_order_amount"])) if saved.get("min_order_amount") is not None else None
            if (kind not in ("FIXED_AMOUNT", "PERCENTAGE") or not value.is_finite() or value <= 0
                or (kind == "PERCENTAGE" and value > 100)
                or (minimum is not None and (not minimum.is_finite() or minimum < 0))
                or not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha()):
                continue
        except (KeyError, TypeError, InvalidOperation):
            continue
        result.append(dict(reference=uuid.uuid5(uuid.NAMESPACE_URL, f"loyalty:redemption:{row.pk}").hex,
            code=row.discount_code, name=str(snapshot.get("reward_name", ""))[:255],
            description=str(snapshot.get("reward_description", ""))[:2000], discount_type=kind,
            discount_value=str(value), min_order_amount=str(minimum) if minimum is not None else None,
            currency=currency.upper(), expires_at=row.code_expires_at))
    return result
