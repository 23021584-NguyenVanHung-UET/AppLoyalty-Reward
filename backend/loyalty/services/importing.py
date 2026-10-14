import copy
from contextlib import contextmanager
from datetime import date, timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from loyalty.models import CustomerProfile, ImportRun, PointsTransaction, ProgramConfig
from shops import bulk_orders
from shops.shopify import ShopifyError, map_order_snapshot
from .earning import earn_points_from_snapshot
from .errors import LoyaltyError


@contextmanager
def import_lock(shop):
    # Session advisory lock spans network waits without holding a DB transaction.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [129704, shop.pk])
        acquired = cursor.fetchone()[0]
    if not acquired:
        raise LoyaltyError("IMPORT_BUSY", "An import for this shop is already running.", 409)
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [129704, shop.pk])


def validated_options(since, until, activate_now, expiry_mode, limit, yes, now):
    try:
        start, end = date.fromisoformat(since), date.fromisoformat(until) if until else now.date()
        if start > end or end > now.date() or expiry_mode not in ("from_now", "none") or not 1 <= limit <= 1_000_000:
            raise ValueError()
        if (end-start).days+1 > settings.LOYALTY_IMPORT_MAX_WINDOW_DAYS and not yes:
            raise LoyaltyError("IMPORT_CONFIRMATION_REQUIRED", "Huge date window; narrow it or pass --yes.", 422)
    except (ValueError, TypeError, OverflowError):
        raise LoyaltyError("INVALID_IMPORT_OPTIONS", "Use valid dates, expiry mode and limit.", 422) from None
    return {"since": start.isoformat(), "until": end.isoformat(), "activate_now": bool(activate_now), "expiry_mode": expiry_mode}


def import_orders(shop, *, since, until=None, activate_now=True, expiry_mode="from_now", limit=10000,
                  dry_run=True, yes=False, resume=None, now=None, progress=None):
    now = now or timezone.now()
    options = validated_options(since, until, activate_now, expiry_mode, limit, yes, now)
    if not shop.is_active or shop.domain != settings.SHOPIFY_SHOP_DOMAIN:
        raise LoyaltyError("IMPORT_SHOP_MISMATCH", "Import uses the configured active Shopify shop only.", 422)
    if resume and dry_run:
        raise LoyaltyError("INVALID_IMPORT_OPTIONS", "Resume requires --yes and cannot be dry-run.", 422)
    config = ProgramConfig.get_effective(shop, now)
    if not config:
        raise LoyaltyError("NO_PROGRAM_CONFIG", "Configure the loyalty program first.", 422)
    counts = {"scanned": 0, "eligible": 0, "duplicates": 0, "skipped": {}, "points": 0, "customers": 0}
    run = None
    with import_lock(shop):
        bulk_orders.require_order_scopes(options["since"], now)
        if not dry_run:
            with transaction.atomic():
                if resume:
                    run = ImportRun.objects.select_for_update().filter(pk=resume, shop=shop).first()
                    if not run or {k:run.options.get(k) for k in options} != options or run.status == "COMPLETED":
                        raise LoyaltyError("INVALID_RESUME", "Use the original run options and an incomplete run from this shop.", 422)
                    run.status, run.last_error = "RUNNING", ""
                    run.save(update_fields=["status", "last_error"])
                    counts = copy.deepcopy(run.counts)
                    config = ProgramConfig.objects.get(pk=run.options["program_config_id"], shop=shop)
                else:
                    if ImportRun.objects.filter(shop=shop, status="RUNNING").exists():
                        raise LoyaltyError("IMPORT_BUSY", "Resume the existing import run instead.", 409)
                    run = ImportRun.objects.create(shop=shop, options={**options, "program_config_id": config.pk}, counts=counts)
        try:
            with bulk_orders.graph() as store:
                if run and run.last_order_id:
                    previous_customers = PointsTransaction.objects.filter(rule_snapshot__import_run_id=run.pk).values_list(
                        "profile__shopify_customer_id", flat=True).distinct().iterator(chunk_size=500)
                    store.db.executemany("INSERT OR IGNORE INTO affected(id) VALUES (?)", ((pk,) for pk in previous_customers))
                # Results expire after seven days. Resume can restart the two bulk
                # queries; ledger idempotency plus last_order_id preserve progress.
                for attempt in range(2):
                    try:
                        for stage in ("core", "shipping"):
                            operation_id = run.bulk_operation_ids.get(stage) if run else None
                            if not operation_id:
                                exclusive_end = (date.fromisoformat(options["until"])+timedelta(days=1)).isoformat()
                                operation_id = bulk_orders.start_operation(bulk_orders.bulk_query(options["since"], exclusive_end, stage))
                                if run:
                                    run.bulk_operation_ids[stage] = operation_id
                                    run.save(update_fields=["bulk_operation_ids"])
                            url = bulk_orders.wait_operation(operation_id)
                            store.ingest(bulk_orders.download_lines(url) if url else (), stage)
                        break
                    except bulk_orders.ResultExpired:
                        if attempt:
                            raise
                        store.db.execute("DELETE FROM nodes")
                        if run:
                            run.bulk_operation_ids = {}
                            run.save(update_fields=["bulk_operation_ids"])
                processed = 0
                for raw in store.orders(run.last_order_id if run else 0):
                    if processed >= limit:
                        break
                    order = map_order_snapshot(raw, include_tax=config.include_tax)
                    if order.created_at is None or not options["since"] <= order.created_at.date().isoformat() <= options["until"]:
                        raise ShopifyError("BULK_ORDER_OUTSIDE_WINDOW")
                    with transaction.atomic():
                        outcome = earn_points_from_snapshot(shop, order, historical=True, config=config, now=now,
                            activate_now=activate_now, expiry_mode=expiry_mode, dry_run=dry_run, import_run_id=run.pk if run else None)
                        counts["scanned"] += 1
                        if outcome.status == "EARNED":
                            counts["eligible"] += 1
                            counts["points"] += outcome.points
                            inserted = store.db.execute("INSERT OR IGNORE INTO affected(id) VALUES (?)", [order.customer_id]).rowcount
                            counts["customers"] += inserted
                        elif outcome.status == "DUPLICATE":
                            counts["duplicates"] += 1
                        else:
                            code = outcome.code or "ineligible"
                            counts["skipped"][code] = counts["skipped"].get(code, 0)+1
                        if run:
                            run.counts, run.last_order_id, run.updated_at = counts, order.order_id, timezone.now()
                            run.save(update_fields=["counts", "last_order_id", "updated_at"])
                    processed += 1
                    if progress and processed % 100 == 0:
                        progress(dict(counts))
                if run:
                    has_more = any(True for _ in store.orders(run.last_order_id))
                    run.status, run.finished_at = ("LIMITED" if has_more else "COMPLETED"), timezone.now()
                    run.save(update_fields=["status", "finished_at"])
                    from .tiers import evaluate_profile_tier
                    ids = PointsTransaction.objects.filter(rule_snapshot__import_run_id=run.pk).values("profile_id").distinct()
                    for profile in CustomerProfile.objects.filter(pk__in=ids).iterator(chunk_size=200):
                        evaluate_profile_tier(profile, max(now, timezone.now()), notify_upgrade=False)
                return {"dry_run": dry_run, "run_id": run.pk if run else None, **counts}
        except Exception as exc:
            if run:
                run.status, run.last_error = "FAILED", "IMPORT_FAILED"
                run.updated_at = timezone.now()
                if isinstance(exc, bulk_orders.OperationFailed) or str(exc) == "BULK_STAGES_INCONSISTENT":
                    run.bulk_operation_ids = {}
                run.save(update_fields=["status", "last_error", "updated_at", "bulk_operation_ids"])
            if isinstance(exc, (ShopifyError, LoyaltyError)):
                raise
            raise ShopifyError("IMPORT_FAILED") from None
