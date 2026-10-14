def hello(name):
    print(f"Xin chào {name}")
    return f"done {name}"


def send_notification(log_id):
    from .services.notifications import send_notification as send
    return send(log_id)


def grant_birthdays(now_iso=None, after_id=0):
    from django_q.tasks import async_task
    from .services.timing import parse_now, resolve_now
    from .services.engagement import birthday_batch
    now = resolve_now(parse_now(now_iso))
    result = birthday_batch(now, after_id=after_id)
    if result["has_more"]:
        async_task("loyalty.tasks.grant_birthdays", now.isoformat(), result["next_after_id"])
    return result


def reward_referral(referral_id, now_iso=None):
    import logging
    from shops.shopify import ShopifyError
    from .services.timing import parse_now
    from .services.referrals import reward_referral as reward
    try:
        return reward(referral_id, parse_now(now_iso))
    except ShopifyError as exc:
        logging.getLogger(__name__).warning("referral_verify_failed referral_id=%s error_class=%s", referral_id, type(exc).__name__)
        return "FAILED"  # Still PENDING; hourly sweep retries verification.


def sweep_referrals(now_iso=None, after_id=0):
    from django_q.tasks import async_task
    from loyalty.models import Referral
    from .services.timing import parse_now, resolve_now
    now = resolve_now(parse_now(now_iso))
    ids = list(Referral.objects.filter(pk__gt=after_id, status="PENDING").order_by("pk").values_list("pk", flat=True)[:201])
    for referral_id in ids[:200]:
        async_task("loyalty.tasks.reward_referral", referral_id, now.isoformat())
    if len(ids)>200:
        async_task("loyalty.tasks.sweep_referrals", now.isoformat(), ids[199])
    return {"queued": min(len(ids), 200), "has_more": len(ids)>200}


def sweep_notifications():
    from .services.notifications import sweep_notifications as sweep
    return sweep()


def expiring_notifications(now_iso=None, after_id=0, batch_size=200):
    from django_q.tasks import async_task
    from loyalty.models import CustomerProfile
    from .services.timing import parse_now, resolve_now
    from .services.notifications import expiring_notifications as remind
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    now = resolve_now(parse_now(now_iso))
    ids = list(CustomerProfile.objects.filter(pk__gt=after_id, shop__is_active=True,
        points_lots__status__in=["AVAILABLE", "PENDING"], points_lots__expires_at__gt=now,
        points_lots__points_remaining__gt=0).order_by("pk").values_list("pk", flat=True).distinct()[:batch_size+1])
    queued = remind(now, profile_ids=ids[:batch_size])
    if len(ids) > batch_size:
        # Bounded tasks fit the cluster timeout; preserve the run's time across batches.
        async_task("loyalty.tasks.expiring_notifications", now.isoformat(), ids[batch_size-1], batch_size)
    return {"profiles": min(len(ids), batch_size), "queued": queued, "has_more": len(ids) > batch_size}


def send_test_email(shop_id, recipient):
    from .services.notifications import send_test_email as send
    return send(shop_id, recipient)


def tier_grace_notifications(now_iso=None, after_id=0):
    from django_q.tasks import async_task
    from loyalty.models import CustomerProfile
    from .services.timing import parse_now
    from .services.notifications import tier_grace_notifications as remind
    now = parse_now(now_iso)
    ids = list(CustomerProfile.objects.filter(pk__gt=after_id, shop__is_active=True,
        tier__isnull=False, tier_drop_since__isnull=False, is_blocked=False, is_anonymized=False)
        .order_by("pk").values_list("pk", flat=True)[:201])
    queued = remind(now, profile_ids=ids[:200])
    if len(ids) > 200:
        async_task("loyalty.tasks.tier_grace_notifications", now.isoformat(), ids[199])
    return {"queued": queued, "profiles": min(len(ids), 200), "has_more": len(ids) > 200}


def run_lifecycle(now_iso=None):
    import logging
    from loyalty.services.lifecycle import run_lifecycle_services, lifecycle_summary
    from loyalty.services.timing import parse_now
    result = run_lifecycle_services(now=parse_now(now_iso))
    logging.getLogger(__name__).info(lifecycle_summary(result))
    return result.__dict__


def process_redemptions(now_iso=None):
    import logging
    from .services.redemption_jobs import process_redemptions_services, redemption_summary
    from .services.timing import parse_now
    result = process_redemptions_services(parse_now(now_iso))
    logging.getLogger(__name__).info(redemption_summary(result))
    return result.__dict__


def evaluate_profile_tier(profile_id):
    from loyalty.models import CustomerProfile
    from .services.tiers import evaluate_profile_tier as evaluate
    profile = CustomerProfile.objects.filter(pk=profile_id).first()
    return evaluate(profile) if profile else None


def evaluate_tiers(now_iso=None):
    from .services.timing import parse_now, resolve_now
    from .services.tiers import evaluate_all
    now, cursor, scanned, changed = resolve_now(parse_now(now_iso)), 0, 0, 0
    while True:
        batch = evaluate_all(now, 500, after_id=cursor)
        scanned += batch["scanned"]
        changed += batch["changed"]
        if not batch["truncated"]:
            return {"scanned": scanned, "changed": changed}
        cursor = batch["next_after_id"]
def scrub_webhook_payloads(after_id=0):
    from loyalty.services.privacy import scrub_webhook_payloads as scrub
    from django_q.tasks import async_task
    result = scrub(after_id=after_id)
    if result["more"]:
        async_task("loyalty.tasks.scrub_webhook_payloads", result["next_after_id"])
    return result


def purge_shop(domain):
    from loyalty.services.privacy import purge_shop_data
    from shops.models import Shop
    from django_q.tasks import async_task
    deleted = purge_shop_data(domain, domain, max_batches=10)
    if Shop.objects.filter(domain=domain).exists():
        async_task("loyalty.tasks.purge_shop", domain)
    return deleted


