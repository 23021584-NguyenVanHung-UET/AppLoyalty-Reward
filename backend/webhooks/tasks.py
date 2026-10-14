import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django_q.tasks import async_task

from .handlers import HANDLERS, UNKNOWN_REASON
from .models import WebhookEvent

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = getattr(settings, "WEBHOOK_MAX_ATTEMPTS", 5)
BACKOFF_MINUTES = getattr(settings, "WEBHOOK_BACKOFF_MINUTES", [1, 5, 15, 60])
STUCK_RECEIVED_AFTER = getattr(settings, "WEBHOOK_STUCK_RECEIVED_AFTER", timedelta(minutes=2))
STUCK_PROCESSING_AFTER = getattr(settings, "WEBHOOK_STUCK_PROCESSING_AFTER", timedelta(minutes=10))
SWEEP_LIMIT = 200
Status = WebhookEvent.Status


def enqueue_webhook(event_pk):
    try:
        async_task("webhooks.tasks.process_webhook", event_pk)
    except Exception:
        logger.error("Không thể đưa webhook vào hàng đợi: event_pk=%s", event_pk)


def claim_webhook(event_pk):
    with transaction.atomic():
        event = WebhookEvent.objects.select_for_update().filter(pk=event_pk).first()
        if event is None or event.status not in (Status.RECEIVED, Status.FAILED):
            return None
        if event.status == Status.FAILED and event.attempts >= MAX_ATTEMPTS:
            return None
        event.status = Status.PROCESSING
        event.attempts += 1
        event.last_attempt_at = timezone.now()
        event.processed_at = None
        event.skip_reason = ""
        event.error = ""
        event.save(update_fields=["status", "attempts", "last_attempt_at", "processed_at",
                                  "skip_reason", "error", "updated_at"])
    return event


def safe_error(exception, payload):
    """Handlers must not embed request data in errors; redact accidental leaks."""
    message = str(exception)
    sensitive = [settings.SHOPIFY_CLIENT_SECRET,
                 json.dumps(payload, ensure_ascii=False), json.dumps(payload), str(payload)]
    pending = [payload]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str):
            sensitive.append(value)
    for value in sorted(set(filter(None, sensitive)), key=len, reverse=True):
        message = message.replace(value, "[đã ẩn]")
    return message[:2000]


def process_webhook(event_pk):
    event = claim_webhook(event_pk)
    if event is None:
        return
    claim_time, attempt = event.last_attempt_at, event.attempts
    final_status = Status.FAILED
    error = "Tiến trình xử lý bị gián đoạn"
    skip_reason = ""
    processed_at = None
    try:
        handler = HANDLERS.get(event.topic)
        if handler is None:
            event.status = Status.SKIPPED
            event.skip_reason = UNKNOWN_REASON
        else:
            handler(event)
        final_status = Status.SKIPPED if event.status == Status.SKIPPED else Status.PROCESSED
        skip_reason = event.skip_reason if final_status == Status.SKIPPED else ""
        processed_at = timezone.now()
        error = ""
    except Exception as exception:
        error = safe_error(exception, event.payload)
    finally:
        # A stale worker cannot overwrite a reclaimed attempt's result.
        updated = WebhookEvent.objects.filter(
            pk=event_pk, status=Status.PROCESSING,
            last_attempt_at=claim_time, attempts=attempt,
        ).update(status=final_status, error=error, skip_reason=skip_reason,
                 processed_at=processed_at, updated_at=timezone.now())
        if updated:
            logger.log(logging.WARNING if final_status == Status.FAILED else logging.INFO,
                       "event_pk=%s webhook_id=%s topic=%s shop_domain=%s status=%s",
                       event_pk, event.webhook_id, event.topic, event.shop_domain, final_status)


def failed_is_due(event, now):
    wait = BACKOFF_MINUTES[min(max(event.attempts - 1, 0), len(BACKOFF_MINUTES) - 1)]
    return (event.attempts < MAX_ATTEMPTS
            and (event.last_attempt_at or event.received_at) <= now - timedelta(minutes=wait))


def sweep_webhook_events():
    now = timezone.now()
    received = Q(status=Status.RECEIVED, received_at__lte=now - STUCK_RECEIVED_AFTER)
    stuck_time = now - STUCK_PROCESSING_AFTER
    stuck = Q(status=Status.PROCESSING) & (
        Q(last_attempt_at__lte=stuck_time)
        | Q(last_attempt_at__isnull=True, received_at__lte=stuck_time)
    )
    failed = Q(pk__in=[])
    for attempt in range(MAX_ATTEMPTS):
        wait = BACKOFF_MINUTES[min(max(attempt - 1, 0), len(BACKOFF_MINUTES) - 1)]
        cutoff = now - timedelta(minutes=wait)
        failed |= Q(status=Status.FAILED, attempts=attempt) & (
            Q(last_attempt_at__lte=cutoff)
            | Q(last_attempt_at__isnull=True, received_at__lte=cutoff)
        )
    with transaction.atomic():
        events = list(WebhookEvent.objects.select_for_update(skip_locked=True)
                      .filter(received | stuck | failed)
                      .order_by("received_at", "pk")[:SWEEP_LIMIT])
        for event in events:
            if event.status == Status.PROCESSING:
                event.status = Status.FAILED
                event.error = "Tiến trình xử lý bị gián đoạn"
                event.save(update_fields=["status", "error", "updated_at"])
            if event.status == Status.RECEIVED or failed_is_due(event, now):
                transaction.on_commit(lambda pk=event.pk: enqueue_webhook(pk))
    exhausted = WebhookEvent.objects.filter(status=Status.FAILED, attempts__gte=MAX_ATTEMPTS).count()
    if exhausted:
        logger.warning("Số webhook FAILED đã hết lượt thử: %s", exhausted)
