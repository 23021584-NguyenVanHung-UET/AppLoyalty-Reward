import json
import logging

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from shops.models import Shop
from .models import WebhookEvent
from .security import verify_signature
from .tasks import enqueue_webhook

logger = logging.getLogger(__name__)
WEBHOOK_MAX_BODY_BYTES = getattr(settings, "WEBHOOK_MAX_BODY_BYTES", 2_000_000)


def security_warning(request):
    logger.warning("Webhook bị từ chối: ip=%s topic=%s",
                   request.META.get("REMOTE_ADDR", ""),
                   request.headers.get("X-Shopify-Topic", "")[:100])


@csrf_exempt
@require_POST
def shopify_webhook(request):
    try:
        declared_size = int(request.META.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        return HttpResponse(status=400)
    if declared_size > WEBHOOK_MAX_BODY_BYTES:
        return HttpResponse(status=413)
    try:
        raw_body = request.body
    except RequestDataTooBig:
        return HttpResponse(status=413)
    if len(raw_body) > WEBHOOK_MAX_BODY_BYTES:
        return HttpResponse(status=413)
    if not verify_signature(raw_body, request.headers.get("X-Shopify-Hmac-Sha256", ""),
                            settings.SHOPIFY_CLIENT_SECRET):
        security_warning(request)
        return HttpResponse(status=401 if request.headers.get("X-Shopify-Topic") in
                            ("customers/data_request", "customers/redact", "shop/redact") else 403)

    webhook_id = request.headers.get("X-Shopify-Webhook-Id", "")
    event_id = request.headers.get("X-Shopify-Event-Id", "")
    topic = request.headers.get("X-Shopify-Topic", "")
    domain = request.headers.get("X-Shopify-Shop-Domain", "")
    if not webhook_id or not topic or not domain:
        return HttpResponse(status=400)
    if len(webhook_id) > 100 or len(event_id) > 100 or len(topic) > 100 or len(domain) > 255:
        return HttpResponse(status=400)
    if domain.casefold() != settings.SHOPIFY_SHOP_DOMAIN.casefold():
        security_warning(request)
        return HttpResponse(status=403)
    compliance = topic in ("customers/data_request", "customers/redact", "shop/redact")
    shop_query = Shop.objects.filter(domain__iexact=domain)
    shop = (shop_query if compliance else shop_query.filter(is_active=True)).first()
    if shop is None:
        if topic == "shop/redact":
            return HttpResponse(status=200)  # Already purged; do not recreate the shop.
        security_warning(request)
        return HttpResponse(status=403)
    try:
        payload = json.loads(raw_body, parse_constant=reject_json_constant)
        if payload is None:  # JSONField is NOT NULL.
            return HttpResponse(status=400)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return HttpResponse(status=400)
    try:
        with transaction.atomic():
            from loyalty.models import CustomerProfile
            from loyalty.services.privacy import customer_id_in, skeleton
            customer_id = customer_id_in(payload, topic)
            if customer_id and CustomerProfile.objects.filter(shop=shop, shopify_customer_id=customer_id, is_anonymized=True).exists():
                payload = skeleton(payload, topic)
                if compliance:
                    payload["shop_domain"] = shop.domain
            event = WebhookEvent.objects.create(
                shop=shop, shop_domain=shop.domain, webhook_id=webhook_id,
                event_id=event_id, topic=topic, payload=payload,
                status=WebhookEvent.Status.RECEIVED,
            )
            transaction.on_commit(lambda: enqueue_webhook(event.pk))
    except IntegrityError:
        if not WebhookEvent.objects.filter(webhook_id=webhook_id).exists():
            raise
    return HttpResponse(status=200)


def reject_json_constant(value):
    raise ValueError("Giá trị JSON không hợp lệ")
