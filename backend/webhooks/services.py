from django.db import transaction
from django.utils import timezone

from loyalty.services.errors import LoyaltyError
from webhooks.models import WebhookEvent


def reset_events(queryset, limit=100):
    """Share the command's reset semantics; leave delivery to the sweeper."""
    with transaction.atomic():
        ids = list(queryset.filter(status__in=["FAILED", "SKIPPED"])
            .order_by("received_at", "pk").select_for_update(skip_locked=True)
            .values_list("pk", flat=True)[:limit])
        WebhookEvent.objects.filter(pk__in=ids).update(status="RECEIVED", attempts=0,
            error="", skip_reason="", last_attempt_at=None, processed_at=None, updated_at=timezone.now())
        return ids


def retry_event(event, actor):
    from loyalty.models import AuditLog
    with transaction.atomic():
        event = WebhookEvent.objects.select_for_update().get(pk=event.pk)
        before = event.status
        if before not in ("FAILED", "SKIPPED"):
            raise LoyaltyError("INVALID_STATUS", "Chỉ có thể thử lại webhook lỗi hoặc đã bỏ qua.")
        reset_events(WebhookEvent.objects.filter(pk=event.pk), 1)
        AuditLog.objects.create(shop_id=event.shop_id, actor=actor, action="webhook.retry",
            target_type="WebhookEvent", target_id=str(event.pk),
            before={"status": before}, after={"status": "RECEIVED"})
        return {"id": event.pk, "status": "RECEIVED"}
