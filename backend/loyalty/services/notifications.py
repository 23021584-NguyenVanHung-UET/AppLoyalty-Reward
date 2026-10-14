"""Durable event references, never rendered bodies, codes or recipient addresses."""
import logging
from datetime import timedelta
from email.utils import formataddr
from uuid import uuid4

from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django_q.tasks import async_task

from loyalty.models import (CustomerProfile, NotificationSettings, NotificationLog,
    NotificationExpiryLot, PointsLot, ProgramConfig)

logger = logging.getLogger(__name__)
TRANSACTIONAL = {"POINTS_EARNED", "REWARD_CODE_ISSUED"}
SALT = "loyalty.email.unsubscribe.v1"


def queue_task(log_id):
    try:
        # Save no task results/arguments containing rendered messages.
        async_task("loyalty.tasks.send_notification", log_id)
    except Exception:
        # Outbox remains QUEUED; the sweeper will recover it.
        logger.warning("notification_queue_failed log_id=%s", log_id)


def notify(profile, kind, event_id, **refs):
    entry, created = NotificationLog.objects.get_or_create(
        dedupe_key=f"profile:{profile.pk}:{kind}:{event_id}",
        defaults={"profile": profile, "type": kind, **refs})
    if created:
        transaction.on_commit(lambda: queue_task(entry.pk))
    return entry


def unsubscribe_token(profile_id):
    return signing.dumps({"profile": profile_id}, salt=SALT)


def unsubscribe(token):
    try:
        value = signing.loads(token, salt=SALT)
        if not isinstance(value, dict) or type(value.get("profile")) is not int:
            return False
    except (signing.BadSignature, ValueError, TypeError):
        return False
    # Persistent preference: syncing Shopify consent must never clear it.
    return bool(CustomerProfile.objects.filter(pk=value["profile"]).update(email_opt_out=True))


def skip_reason(profile, kind, config):
    if profile.is_anonymized:
        return "ANONYMIZED"
    if profile.is_blocked:
        return "BLOCKED"
    if not profile.email:
        return "NO_EMAIL"
    if not profile.shop.is_active:
        return "SHOP_INACTIVE"
    if kind not in config.enabled_types:
        return "DISABLED"
    if kind not in TRANSACTIONAL:
        if profile.email_opt_out:
            return "OPTED_OUT"
        if not profile.email_marketing_opt_in:
            return "NO_MARKETING_CONSENT"
    return ""


def context_for(entry, profile):
    context = {"points": 0, "code": "", "reward": "", "tier": "", "lots": [], "expires_at": None, "available_at": None}
    if entry.type in ("POINTS_EARNED", "POINTS_AVAILABLE", "WELCOME_POINTS", "BIRTHDAY_POINTS", "REFERRAL_REWARDED"):
        lot = entry.lot
        if lot is None:
            return None
        if entry.type == "POINTS_AVAILABLE" and (lot.status != "AVAILABLE" or lot.points_remaining == 0):
            return None
        context.update(points=lot.points_remaining if entry.type == "POINTS_AVAILABLE" else lot.points_total,
            available_at=lot.available_at, expires_at=lot.expires_at)
    elif entry.type == "REWARD_CODE_ISSUED":
        redemption = entry.redemption
        if not redemption or redemption.status != "ISSUED" or not redemption.discount_code or (redemption.code_expires_at and redemption.code_expires_at <= timezone.now()):
            return None
        saved = redemption.points_transactions.filter(type="REDEEM").values_list("rule_snapshot", flat=True).first() or {}
        context.update(code=redemption.discount_code, reward=saved.get("reward_name", redemption.reward.name),
            expires_at=redemption.code_expires_at)
    elif entry.type == "TIER_UPGRADED":
        if not entry.tier_history or not entry.tier_history.to_tier:
            return None
        context["tier"] = entry.tier_history.to_tier.name
    elif entry.type == "TIER_GRACE_REMINDER":
        from .tiers import tier_state
        state = tier_state(profile)
        snapshot = entry.tier_grace_snapshot
        if (not state["grace"]["active"] or snapshot.get("tier_id") != profile.tier_id
                or snapshot.get("drop_since") != profile.tier_drop_since.isoformat()):
            return None
        from .timing import parse_now
        context.update(tier=state["name"], points=state["grace"]["points_to_keep"],
                       grace_deadline=parse_now(state["grace"]["deadline"]))
    elif entry.type == "POINTS_EXPIRING":
        lots = list(PointsLot.objects.filter(notificationexpirylot__notification=entry,
            status__in=["AVAILABLE", "PENDING"], points_remaining__gt=0, expires_at__gt=timezone.now()).order_by("expires_at"))
        if not lots:
            return None
        context["lots"] = lots
        context["points"] = sum(lot.points_remaining for lot in lots)
    return context


def notify_tier_grace(profile, stage):
    """One email per tier/drop/stage. Snapshot is identifiers only, never PII."""
    if stage not in ("started", "seven_days") or not profile.tier_id or not profile.tier_drop_since:
        raise ValueError("Invalid tier grace event")
    drop = profile.tier_drop_since.isoformat()
    return notify(profile, "TIER_GRACE_REMINDER", f"{profile.tier_id}:{drop}:{stage}",
        tier_grace_snapshot={"tier_id": profile.tier_id, "drop_since": drop, "stage": stage})


def profile_id_batches(candidates, batch_size=200):
    """Finish each SELECT before enqueue: the ORM broker may close its connection."""
    after_id = 0
    while True:
        ids = list(candidates.filter(pk__gt=after_id).values_list("pk", flat=True)[:batch_size])
        if not ids:
            return
        yield from ids
        after_id = ids[-1]


def tier_grace_notifications(now=None, *, profile_ids=None):
    from .tiers import grace_state, qualifying_points
    from .timing import resolve_now
    now, queued = resolve_now(now), 0
    candidates = CustomerProfile.objects.filter(shop__is_active=True, tier__isnull=False,
        tier_drop_since__isnull=False, is_anonymized=False, is_blocked=False).order_by("pk")
    if profile_ids is not None:
        candidates = candidates.filter(pk__in=profile_ids)
    for profile_id in profile_id_batches(candidates):
        with transaction.atomic():
            profile = CustomerProfile.objects.select_for_update(of=("self",)).select_related("shop", "tier").get(pk=profile_id)
            config = ProgramConfig.get_effective(profile.shop, now)
            if not config or not config.tiers_enabled:
                continue
            metric = qualifying_points(profile, now, config.tier_window_months)
            state = grace_state(profile, config, metric, now)
            if not state["active"]:
                continue
            deadline = profile.tier_drop_since + timedelta(days=config.tier_downgrade_grace_days)
            stage = "seven_days" if config.tier_downgrade_grace_days > 7 and deadline - now <= timedelta(days=7) else "started"
            key = f"profile:{profile.pk}:TIER_GRACE_REMINDER:{profile.tier_id}:{profile.tier_drop_since.isoformat()}:{stage}"
            before = NotificationLog.objects.filter(dedupe_key=key).exists()
            notify_tier_grace(profile, stage)
            queued += int(not before)
    return queued


def message(kind, config, profile, context, recipient):
    lang = (profile.locale or config.default_language).lower().split("-")[0]
    if lang not in ("en", "vi"):
        lang = config.default_language
    display = config.sender_display_name or profile.shop.domain.split(".")[0]
    promotional = kind not in TRANSACTIONAL
    context = context | {"kind": kind, "shop_name": display, "language": lang,
        "unsubscribe_url": f"{settings.LOYALTY_PUBLIC_URL}/unsubscribe/{unsubscribe_token(profile.pk)}/" if promotional else ""}
    subject = render_to_string(f"emails/{lang}/subject.txt", context).strip().replace("\r", " ").replace("\n", " ")
    headers = {"List-Unsubscribe": f'<{context["unsubscribe_url"]}>', "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"} if promotional else {}
    mail = EmailMultiAlternatives(subject=subject,
        body=render_to_string(f"emails/{lang}/message.txt", context),
        from_email=formataddr((display, settings.DEFAULT_FROM_EMAIL)), to=[recipient],
        reply_to=[config.reply_to] if config.reply_to else [], headers=headers)
    mail.attach_alternative(render_to_string(f"emails/{lang}/message.html", context), "text/html")
    return mail


def send_notification(log_id):
    # Called only by django-q task. Lock covers send so duplicate workers cannot send concurrently.
    with transaction.atomic():
        profile_id = NotificationLog.objects.filter(pk=log_id).values_list("profile_id", flat=True).first()
        if profile_id is None:
            return "NOOP"
        profile = CustomerProfile.objects.select_for_update(of=("self",)).select_related("shop").get(pk=profile_id)
        entry = NotificationLog.objects.select_for_update().filter(pk=log_id).first()
        if not entry or entry.status not in ("QUEUED", "FAILED") or entry.attempts >= 5 or entry.next_attempt_at > timezone.now():
            return "NOOP"
        config, _ = NotificationSettings.objects.get_or_create(shop=profile.shop)
        reason = skip_reason(profile, entry.type, config)
        context = context_for(entry, profile) if not reason else None
        if not reason and context is None:
            reason = "EVENT_NO_LONGER_RELEVANT"
        if reason:
            entry.status, entry.skip_reason = "SKIPPED", reason
            entry.save(update_fields=["status", "skip_reason"])
            return "SKIPPED"
        entry.attempts += 1
        try:
            if message(entry.type, config, profile, context, profile.email).send() != 1:
                raise RuntimeError("Delivery not accepted")
        except Exception as exc:
            # Provider exception text can contain addresses/codes. Store class only.
            entry.status = "FAILED"
            entry.last_error = type(exc).__name__[:100]
            entry.next_attempt_at = timezone.now() + timedelta(minutes=2 ** entry.attempts)
            logger.warning("notification_failed log_id=%s attempt=%s error_class=%s", entry.pk, entry.attempts, entry.last_error)
        else:
            entry.status, entry.sent_at, entry.last_error = "SENT", timezone.now(), ""
            logger.info("notification_sent log_id=%s", entry.pk)
        entry.save(update_fields=["status", "attempts", "sent_at", "last_error", "next_attempt_at"])
        return entry.status


def sweep_notifications():
    now = timezone.now()
    ids = list(NotificationLog.objects.filter(status__in=["FAILED", "QUEUED"], attempts__lt=5,
        next_attempt_at__lte=now, queued_until__lte=now).order_by("next_attempt_at", "pk").values_list("pk", flat=True)[:500])
    for log_id in ids:
        # Lease prevents a busy sweeper from continuously filling the queue.
        if NotificationLog.objects.filter(pk=log_id, queued_until__lte=now).update(queued_until=now+timedelta(minutes=3)):
            queue_task(log_id)
    return len(ids)


def expiring_notifications(now=None, *, profile_ids=None):
    now = now or timezone.now()
    total = 0
    # Iterate profiles in chunks, then lock each profile. No body or points snapshot is persisted.
    candidates = CustomerProfile.objects.filter(shop__is_active=True, points_lots__status__in=["AVAILABLE", "PENDING"],
        points_lots__expires_at__gt=now, points_lots__points_remaining__gt=0).values_list("pk", flat=True).distinct().order_by("pk")
    if profile_ids is not None:
        candidates = candidates.filter(pk__in=profile_ids)
    for profile_id in profile_id_batches(candidates):
        with transaction.atomic():
            profile = CustomerProfile.objects.select_for_update(of=("self",)).select_related("shop").get(pk=profile_id)
            config = ProgramConfig.get_effective(profile.shop, at=now)
            if not config or not config.expiry_reminder_days:
                continue
            threshold = config.expiry_reminder_days
            claimed = NotificationExpiryLot.objects.filter(threshold_days=threshold).values("lot_id")
            lots = list(PointsLot.objects.filter(profile=profile, status__in=["AVAILABLE", "PENDING"], points_remaining__gt=0,
                expires_at__gt=now, expires_at__lte=now+timedelta(days=threshold)).exclude(pk__in=claimed).order_by("pk"))
            if not lots:
                continue
            entry = notify(profile, "POINTS_EXPIRING", uuid4().hex)
            NotificationExpiryLot.objects.bulk_create([NotificationExpiryLot(notification=entry, lot=lot, threshold_days=threshold) for lot in lots])
            total += 1
    return total


def send_test_email(shop_id, recipient):
    """Task-only test delivery; recipient is never returned or added to NotificationLog/AuditLog."""
    from shops.models import Shop
    shop = Shop.objects.get(pk=shop_id)
    config, _ = NotificationSettings.objects.get_or_create(shop=shop)
    profile = CustomerProfile(shop=shop, locale=config.default_language)
    try:
        mail = message("POINTS_EARNED", config, profile,
            {"points": 10, "code": "", "reward": "", "tier": "", "lots": [], "expires_at": None, "available_at": timezone.now()}, recipient)
        mail.subject = "[TEST] " + mail.subject
        return "SENT" if mail.send() == 1 else "FAILED"
    except Exception:
        logger.warning("notification_test_failed shop_id=%s", shop_id)
        return "FAILED"
