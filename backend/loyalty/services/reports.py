"""SQL aggregates and bounded CSV projections. All timestamps are UTC."""
import csv
from tempfile import SpooledTemporaryFile
from datetime import date, datetime, time, timedelta, timezone as utc

from django.conf import settings
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import TruncDay, TruncWeek, TruncMonth
from django.http import FileResponse
from django.utils import timezone

from loyalty.models import AuditLog, CustomerProfile, PointsLot, PointsTransaction, Redemption, Tier
from .errors import LoyaltyError


def date_range(params):
    try:
        end = date.fromisoformat(params.get("to") or timezone.now().date().isoformat())
        start = date.fromisoformat(params.get("from") or (end - timedelta(days=29)).isoformat())
        if start > end or (end - start).days + 1 > settings.LOYALTY_REPORT_MAX_DAYS:
            raise ValueError()
        return datetime.combine(start, time.min, utc.utc), datetime.combine(end + timedelta(days=1), time.min, utc.utc)
    except (ValueError, TypeError, OverflowError):
        raise LoyaltyError("INVALID_DATE_RANGE", f"Use YYYY-MM-DD, from <= to, maximum {settings.LOYALTY_REPORT_MAX_DAYS} days.", 422) from None


def ranged(query, bounds):
    return query.filter(created_at__gte=bounds[0], created_at__lt=bounds[1])


def point_sums():
    return {name: Sum("delta", filter=Q(type__in=types), default=0) for name, types in {
        "earned": ["EARN"], "revoked": ["REVOKE"], "redeemed": ["REDEEM", "REDEEM_REFUND"],
        "expired": ["EXPIRE"], "adjusted": ["ADJUST"]}.items()} | {"net": Sum("delta", default=0)}


def display_sums(row):
    return {key: -value if key in ("revoked", "redeemed", "expired") else value for key, value in row.items()}


def report(shop, kind, params):
    bounds = date_range(params)
    ledger = PointsTransaction.objects.filter(profile__shop=shop)
    period = ranged(ledger, bounds)
    if kind == "points-summary":
        group = params.get("group_by", "day")
        trunc = {"day": TruncDay, "week": TruncWeek, "month": TruncMonth}.get(group)
        if not trunc:
            raise LoyaltyError("INVALID_GROUP", "group_by must be day, week or month.", 422)
        rows = period.annotate(period=trunc("created_at", tzinfo=utc.utc)).values("period").annotate(**point_sums()).order_by("period")
        return {"totals": display_sums(period.aggregate(**point_sums())), "rows": [display_sums(row) for row in rows]}
    if kind == "liability":
        # Current liability intentionally ignores the activity date filter.
        profiles = CustomerProfile.objects.filter(shop=shop)
        totals = profiles.aggregate(available=Sum("available_balance", filter=Q(available_balance__gt=0), default=0),
            pending=Sum("pending_balance", default=0), debt=Sum("available_balance", filter=Q(available_balance__lt=0), default=0))
        totals["debt"] = -totals["debt"]
        totals["outstanding"] = totals["available"] + totals["pending"]
        now = timezone.now()
        lots = PointsLot.objects.filter(profile__shop=shop, status__in=["AVAILABLE", "PENDING"], expires_at__gt=now)
        totals.update(lots.aggregate(**{f"expiring_{days}": Sum("points_remaining", filter=Q(expires_at__lte=now+timedelta(days=days)), default=0) for days in (7, 30, 60)}))
        return totals
    if kind == "redemptions":
        query = ranged(Redemption.objects.filter(profile__shop=shop), bounds)
        counts = query.aggregate(issued=Count("pk", filter=~Q(shopify_discount_id="") | Q(status__in=["ISSUED", "USED"])), used=Count("pk", filter=Q(status="USED")))
        counts["conversion_rate"] = counts["used"] / counts["issued"] if counts["issued"] else 0
        return counts | {"rows": list(query.values("reward_id", "reward__name", "status").annotate(count=Count("pk")).order_by("reward_id", "status"))}
    if kind == "top-customers":
        def top(query):
            return list(query.filter(type="EARN").values("profile_id", "profile__shopify_customer_id").annotate(points=Sum("delta")).order_by("-points", "profile_id")[:25])
        return {"lifetime": top(ledger), "period": top(period)}
    if kind == "tier-distribution":
        return {"exists": Tier.objects.filter(shop=shop).exists(), "rows": list(CustomerProfile.objects.filter(shop=shop).values("tier_id", "tier__name").annotate(count=Count("pk")).order_by("tier_id"))}
    raise LoyaltyError("NOT_FOUND", "Unknown report.", 404)


class Echo:
    def write(self, value):
        return value


def safe_cell(value):
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def export_csv(shop, kind, params, actor):
    if params.get("include_pii", "0") not in ("0", "1"):
        raise LoyaltyError("INVALID_INPUT", "include_pii must be 0 or 1.", 422)
    pii = params.get("include_pii") == "1"
    projections = {
        "customers": (CustomerProfile.objects.filter(shop=shop), ["id", "shopify_customer_id", "available_balance", "pending_balance", "lifetime_earned", "tier_id", "created_at"]),
        "ledger": (PointsTransaction.objects.filter(profile__shop=shop), ["id", "profile_id", "type", "delta", "order_id", "created_at"]),
        "lots": (PointsLot.objects.filter(profile__shop=shop), ["id", "profile_id", "source_type", "points_total", "points_remaining", "status", "available_at", "expires_at", "created_at"]),
        "redemptions": (Redemption.objects.filter(profile__shop=shop), ["id", "profile_id", "reward_id", "points_spent", "status", "created_at"]),
    }
    if kind not in projections:
        raise LoyaltyError("NOT_FOUND", "Unknown export.", 404)
    query, fields = projections[kind]
    filters = {key: params[key] for key in ("from", "to", "profile_id", "status") if key in params}
    if kind == "ledger" or "from" in params or "to" in params:
        bounds = date_range(params)
        query = ranged(query, bounds)
        filters.update({"from": bounds[0].date().isoformat(), "to": (bounds[1]-timedelta(days=1)).date().isoformat()})
    if "profile_id" in params:
        try:
            profile_id = int(params["profile_id"])
            if profile_id <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            raise LoyaltyError("INVALID_INPUT", "profile_id must be positive.", 422) from None
        query = query.filter(**{"pk" if kind == "customers" else "profile_id": profile_id})
    if "status" in params:
        model_choices = {"lots": PointsLot.Status.values, "redemptions": Redemption.Status.values}
        if params["status"] not in model_choices.get(kind, []):
            raise LoyaltyError("INVALID_INPUT", "Invalid status filter.", 422)
        query = query.filter(status=params["status"])
    # Freeze the insertion boundary; newly inserted rows cannot overflow the approved cap.
    cutoff = query.aggregate(last=Max("pk"))["last"] or 0
    query = query.filter(pk__lte=cutoff)
    cap = settings.LOYALTY_EXPORT_MAX_ROWS
    count = query.order_by().values("pk")[:cap+1].count()
    if count > cap:
        raise LoyaltyError("EXPORT_TOO_LARGE", f"Export exceeds {cap} rows. Narrow the date range or filters.", 422)
    if pii:
        fields += [("" if kind == "customers" else "profile__") + column for column in ("email", "first_name", "last_name")]
    # Materialize one cursor's snapshot to a bounded-memory spool before headers.
    # This keeps the audit count exact even when mutable filters change during export,
    # and lets a cap violation return JSON instead of a truncated CSV with HTTP 200.
    spool = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    try:
        writer = csv.writer(Echo())
        spool.write(b"\xef\xbb\xbf")
        spool.write(writer.writerow(fields).encode("utf-8"))
        count = 0
        for count, row in enumerate(query.order_by("pk").values_list(*fields).iterator(chunk_size=2000), 1):
            if count > cap:
                raise LoyaltyError("EXPORT_TOO_LARGE", f"Export exceeds {cap} rows. Narrow the date range or filters.", 422)
            spool.write(writer.writerow([safe_cell(value) for value in row]).encode("utf-8"))
        AuditLog.objects.create(shop=shop, actor=actor, action="report.export", target_type="CSV", target_id=kind,
            after={"type": kind, "filters": filters, "row_count": count, "include_pii": pii})
        spool.seek(0)
        # FileResponse subclasses StreamingHttpResponse and registers file cleanup
        # even if the client disconnects before consuming its first chunk.
        response = FileResponse(spool, as_attachment=True, filename=f"loyalty-{kind}.csv", content_type="text/csv; charset=utf-8")
        response.block_size = 64 * 1024
    except Exception:
        spool.close()
        raise
    response["X-Export-Row-Count"] = str(count)
    response["X-Content-Type-Options"] = "nosniff"
    return response
