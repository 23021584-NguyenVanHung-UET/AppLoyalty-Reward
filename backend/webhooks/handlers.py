"""Thin dispatch adapters; services own all customer and loyalty changes."""
import logging

from .models import WebhookEvent
from loyalty.services.customers import upsert_customer_profile
from loyalty.services.earning import earn_points_for_order
from loyalty.services.revoking import revoke_points_for_cancel, revoke_points_for_refund
from loyalty.services.redemption_jobs import mark_codes_used
from shops.shopify import numeric_id

logger = logging.getLogger(__name__)
HANDLERS = {}
STUB_REASON = "Chưa triển khai handler cho topic này"
UNKNOWN_REASON = "Topic chưa được hỗ trợ"


def register(topic):
    def decorator(handler):
        HANDLERS[topic] = handler
        return handler
    return decorator


def mark_stub(event):
    event.status = WebhookEvent.Status.SKIPPED
    event.skip_reason = STUB_REASON
    logger.info("event_pk=%s webhook_id=%s topic=%s shop_domain=%s status=%s",
                event.pk, event.webhook_id, event.topic, event.shop_domain, event.status)


@register("orders/paid")
def orders_paid(event):
    mark_codes_used(event_shop(event), payload_id(event), event.payload)
    outcome = earn_points_for_order(event_shop(event), payload_id(event))
    if outcome.status == "SKIPPED":
        event.status = WebhookEvent.Status.SKIPPED
        event.skip_reason = outcome.reason


@register("orders/cancelled")
def orders_cancelled(event):
    from loyalty.services.order_state import record_cancellation
    record_cancellation(event_shop(event), payload_id(event))
    mark_revoke_outcome(event, revoke_points_for_cancel(event_shop(event), payload_id(event)))
    from loyalty.services.referrals import reject_for_order
    reject_for_order(event_shop(event), payload_id(event), "CANCELLED")


@register("refunds/create")
def refunds_create(event):
    order_id = payload_id(event, "order_id")
    refund_id = payload_id(event)
    mark_revoke_outcome(event, revoke_points_for_refund(event_shop(event), order_id, refund_id))
    from loyalty.services.referrals import reject_for_order
    reject_for_order(event_shop(event), order_id, "REFUNDED")


def mark_revoke_outcome(event, outcome):
    if outcome.status in ("NOOP", "SKIPPED"):
        event.status = WebhookEvent.Status.SKIPPED
        event.skip_reason = outcome.reason


@register("customers/create")
def customers_create(event):
    profile = sync_customer(event)
    from loyalty.services.engagement import welcome_on_signup
    welcome_on_signup(profile, event.payload)


@register("customers/update")
def customers_update(event):
    sync_customer(event)


@register("app/uninstalled")
def app_uninstalled(event):
    mark_stub(event)


def payload_id(event, field="id"):
    if not isinstance(event.payload, dict) or not event.payload.get(field):
        raise ValueError(f"Webhook thiếu mã đối tượng Shopify ({field}).")
    return numeric_id(event.payload[field])


def event_shop(event):
    if event.shop_id is None:
        raise ValueError("Webhook chưa được gắn với cửa hàng.")
    if getattr(event, "topic", None) not in ("customers/data_request", "customers/redact", "shop/redact"):
        from shops.models import Shop
        if not Shop.objects.filter(pk=event.shop_id, is_active=True).exists():
            raise ValueError("SHOP_INACTIVE")
    return event.shop


def sync_customer(event):
    customer_id = payload_id(event)
    consent = event.payload.get("email_marketing_consent")
    profile, _ = upsert_customer_profile(event_shop(event), customer_id, email=event.payload.get("email"),
        first_name=event.payload.get("first_name"), last_name=event.payload.get("last_name"),
        locale=event.payload.get("locale"),
        shopify_created_at=event.payload.get("created_at"),
        email_marketing_opt_in=(isinstance(consent, dict) and consent.get("state") == "subscribed") if "email_marketing_consent" in event.payload else None)
    return profile


def compliance_customer(event):
    shop = event_shop(event)
    if not isinstance(event.payload, dict) or event.payload.get("shop_domain") != shop.domain:
        raise ValueError("COMPLIANCE_SHOP_MISMATCH")
    from loyalty.services.privacy import customer_id_in
    customer_id = customer_id_in(event.payload, event.topic)
    if customer_id is None:
        from loyalty.models import CustomerProfile
        customer = event.payload.get("customer")
        email = customer.get("email") if isinstance(customer, dict) else None
        if isinstance(email, str) and email:
            matches = list(CustomerProfile.objects.filter(shop=shop, email__iexact=email)[:2])
            if len(matches) == 1:
                return matches[0]
        raise ValueError("COMPLIANCE_CUSTOMER_ID_REQUIRED")
    profile, _ = upsert_customer_profile(shop, customer_id)
    return profile


@register("customers/data_request")
def customers_data_request(event):
    from loyalty.services.privacy import customer_export
    profile = compliance_customer(event)
    customer_export(profile, "system:privacy", f"webhook:{event.webhook_id}")


@register("customers/redact")
def customers_redact(event):
    from loyalty.services.privacy import anonymize_customer
    anonymize_customer(compliance_customer(event), "system:privacy")


@register("shop/redact")
def shop_redact(event):
    from django.db import transaction
    from django_q.tasks import async_task
    shop = event_shop(event)
    if not isinstance(event.payload, dict) or event.payload.get("shop_domain") != shop.domain:
        raise ValueError("COMPLIANCE_SHOP_MISMATCH")
    # Queue only the authenticated event's shop, never an arbitrary payload domain.
    transaction.on_commit(lambda: async_task("loyalty.tasks.purge_shop", shop.domain))
