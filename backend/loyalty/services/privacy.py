"""Scoped privacy operations. Never disable history triggers or log export bodies."""
import json
import re
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from loyalty.models import (AuditLog, CustomerProfile, CustomerDataExport, PointsLot,
    PointsTransaction, Redemption, TierHistory, NotificationLog, ReferralCode)
from shops.models import Shop
from shops.shopify import numeric_id
from webhooks.models import WebhookEvent
from .errors import LoyaltyError

MARKER = "[redacted]"
PII_KEYS = {"email", "firstname", "lastname", "name", "fullname", "phone", "address",
    "address1", "address2", "billingaddress", "shippingaddress", "defaultaddress", "addresses",
    "city", "zip", "postalcode", "ip", "ipaddress", "clientdetails", "customerlocale", "locale",
    "birthdate", "birthday", "birthdaymonth", "birthdayday", "birthdaysetat", "blockedreason",
    "note", "notes", "reason", "last_error", "lasterror", "emailmarketingconsent", "smsmarketingconsent"}


def scrub_json(value):
    if isinstance(value, dict):
        return {key: MARKER if key.replace("_", "").casefold() in PII_KEYS else scrub_json(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_json(item) for item in value]
    return value


@contextmanager
def local_guard(name, value):
    if not connection.in_atomic_block:
        raise RuntimeError("Privacy guard requires a transaction")
    if name not in ("loyalty.redact_profile", "loyalty.redact_audit_shop", "loyalty.purge_shop"):
        raise ValueError("Invalid privacy guard")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting(%s, true)", [name])
        previous = cursor.fetchone()[0] or ""
        cursor.execute("SELECT set_config(%s, %s, true)", [name, str(value)])
        try:
            yield cursor
        finally:
            # SET LOCAL lasts until commit; restore early so callers cannot reuse it.
            if not connection.needs_rollback:
                cursor.execute("SELECT set_config(%s, %s, true)", [name, previous])


def customer_id_in(payload, topic):
    if not isinstance(payload, dict):
        return None
    candidate = (payload.get("customer") or {}).get("id") if isinstance(payload.get("customer"), dict) else None
    candidate = candidate or payload.get("customer_id")
    if candidate is None and topic.startswith("customers/"):
        candidate = payload.get("id")
    try:
        return numeric_id(candidate)
    except ValueError:
        return None


def skeleton(payload, topic):
    result = {"redacted": True}
    if isinstance(payload, dict):
        domain = payload.get("shop_domain")
        if topic in ("customers/data_request", "customers/redact", "shop/redact") and isinstance(domain, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", domain):
            result["shop_domain"] = domain
        for key in ("id", "order_id", "shop_id", "orders_to_redact", "orders_requested"):
            if key in payload:
                try:
                    result[key] = [numeric_id(v) for v in payload[key]] if isinstance(payload[key], list) else numeric_id(payload[key])
                except ValueError:
                    pass
        customer = customer_id_in(payload, topic)
        if customer:
            result["customer"] = {"id": customer}
        if isinstance(payload.get("data_request"), dict) and payload["data_request"].get("id"):
            try:
                result["data_request"] = {"id": numeric_id(payload["data_request"]["id"])}
            except ValueError:
                pass
    return result


def profile_audits(profile):
    # target_id is text, so the subqueries must explicitly cast their IDs.
    from django.db.models.functions import Cast
    from django.db.models import CharField
    targets = Q(target_type="CustomerProfile", target_id=str(profile.pk))
    for model in (PointsLot, PointsTransaction, Redemption, TierHistory):
        targets |= Q(target_type=model.__name__, target_id__in=model.objects.filter(profile=profile)
                     .annotate(text_id=Cast("pk", CharField())).values("text_id"))
    return AuditLog.objects.filter(shop_id=profile.shop_id).filter(targets)


def anonymize_customer(profile, actor, reason="privacy_request"):
    if reason not in ("privacy_request", "merchant_request"):
        raise LoyaltyError("INVALID_REASON", "Use a privacy reason code, not free text.", 422)
    with transaction.atomic():
        row = CustomerProfile.objects.select_for_update().get(pk=profile.pk)
        if row.redemptions.filter(status="PENDING_CODE").exists():
            raise LoyaltyError("PENDING_REDEMPTIONS", "Finish or fail pending reward-code issuance before anonymizing.", 409)
        changed = not row.is_anonymized
        CustomerProfile.objects.filter(pk=row.pk).update(email="", first_name="", last_name="",
            birthday_month=None, birthday_day=None, birthday_set_at=None, blocked_reason="",
            locale="", shopify_created_at=None, email_marketing_opt_in=False, email_opt_out=True,
            is_anonymized=True, updated_at=timezone.now())
        # Scope includes orphaned historic events for the same authenticated shop domain.
        events = WebhookEvent.objects.filter(Q(shop_id=row.shop_id) | Q(shop__isnull=True, shop_domain=row.shop.domain))
        order_ids = set(row.points_transactions.exclude(order_id=None).values_list("order_id", flat=True))
        for event in events.iterator(chunk_size=200):
            payload = event.payload if isinstance(event.payload, dict) else {}
            raw_order_id = payload.get("order_id") or (payload.get("id") if event.topic.startswith("orders/") else None)
            try:
                order_matches = numeric_id(raw_order_id) in order_ids
            except ValueError:
                order_matches = False
            if customer_id_in(event.payload, event.topic) == row.shopify_customer_id or order_matches:
                WebhookEvent.objects.filter(pk=event.pk).update(payload=skeleton(event.payload, event.topic), error="", skip_reason="")
        row.redemptions.update(last_error="")
        row.data_exports.update(payload={"redacted": True, "profile_id": row.pk})
        ReferralCode.objects.filter(profile=row).update(is_active=False)
        NotificationLog.objects.filter(profile=row, status__in=["QUEUED", "FAILED"]).update(
            status="SKIPPED", skip_reason="ANONYMIZED", last_error="")
        with local_guard("loyalty.redact_profile", row.pk) as cursor:
            cursor.execute("UPDATE loyalty_pointstransaction SET note = %s WHERE profile_id = %s AND note <> %s",
                           [MARKER, row.pk, MARKER])
        related_ids = set(profile_audits(row).values_list("pk", flat=True))
        # Do not scrub another customer's records merely because they share a shop.
        with local_guard("loyalty.redact_audit_shop", row.shop_id) as cursor:
            for audit in AuditLog.objects.filter(pk__in=related_ids, shop_id=row.shop_id).iterator(chunk_size=200):
                before, after = scrub_json(audit.before), scrub_json(audit.after)
                if (before, after) != (audit.before, audit.after):
                    cursor.execute('UPDATE loyalty_auditlog SET "before" = %s::jsonb, "after" = %s::jsonb WHERE id = %s',
                                   [json.dumps(before), json.dumps(after), audit.pk])
        if changed:
            AuditLog.objects.create(shop_id=row.shop_id, actor=actor, action="customer.anonymize",
                target_type="CustomerProfile", target_id=str(row.pk), after={"is_anonymized": True, "reason_code": reason})
        row.refresh_from_db()
        return row


def customer_export(profile, actor, request_key=None):
    with transaction.atomic():
        row = CustomerProfile.objects.select_for_update().get(pk=profile.pk)
        key = request_key or f"merchant-export:{uuid4()}"
        previous = CustomerDataExport.objects.filter(request_key=key, profile=row).first()
        if previous:
            return previous.payload
        payload = {"profile": CustomerProfile.objects.filter(pk=row.pk).values().get(), "lots": list(row.points_lots.values()),
            "ledger": list(row.points_transactions.values()), "redemptions": list(row.redemptions.values()),
            "tier_history": list(row.tier_history.values()), "audit_entries": list(profile_audits(row).values())}
        # Keep a JSON-safe copy for the merchant; never send it by email or put it in task args.
        payload = json.loads(json.dumps(payload, cls=DjangoJSONEncoder))
        export = CustomerDataExport.objects.create(profile=row, request_key=key, payload=payload)
        AuditLog.objects.create(shop_id=row.shop_id, actor=actor, action="customer.data_export",
            target_type="CustomerProfile", target_id=str(row.pk), after={"export_id": export.pk})
        return payload


def scrub_webhook_payloads(days=None, now=None, limit=200, after_id=0):
    days = settings.WEBHOOK_PAYLOAD_RETENTION_DAYS if days is None else days
    if type(days) is not int or not 1 <= days <= 3650 or not 1 <= limit <= 1000:
        raise ValueError("Invalid retention or batch size")
    terminal = Q(status__in=["PROCESSED", "SKIPPED"]) | Q(status="FAILED", attempts__gte=getattr(settings,"WEBHOOK_MAX_ATTEMPTS",5))
    query = WebhookEvent.objects.filter(terminal, pk__gt=after_id,
        received_at__lt=(now or timezone.now())-timedelta(days=days)).order_by("pk")
    ids = list(query.values_list("pk", flat=True)[:limit])
    for pk in ids:
        with transaction.atomic():
            event = WebhookEvent.objects.select_for_update().get(pk=pk)
            if event.status not in ("PROCESSED", "SKIPPED", "FAILED") or (
                event.status == "FAILED" and event.attempts < getattr(settings,"WEBHOOK_MAX_ATTEMPTS",5)):
                continue
            WebhookEvent.objects.filter(pk=pk).update(payload=skeleton(event.payload,event.topic), error="", skip_reason="")
    return {"processed": len(ids), "next_after_id": ids[-1] if ids else after_id, "more": len(ids) == limit}


def purge_shop_data(domain, confirmation, *, batch_size=200, max_batches=None):
    if not domain or confirmation != domain or not 1 <= batch_size <= 1000 or (max_batches is not None and not 1 <= max_batches <= 100):
        raise LoyaltyError("PURGE_CONFIRMATION_REQUIRED", "Type the exact shop domain to confirm purge.", 422)
    shop = Shop.objects.filter(domain=domain).first()
    if not shop:
        return 0
    # Hold an exclusive shop lock until all batches finish. No other shop can match the guard.
    with transaction.atomic():
        shop = Shop.objects.select_for_update().get(pk=shop.pk)
        Shop.objects.filter(pk=shop.pk).update(is_active=False)
        profile_scope = "profile_id IN (SELECT id FROM loyalty_customerprofile WHERE shop_id = %s)"
        statements = [
            ("loyalty_notificationexpirylot", "notification_id IN (SELECT id FROM loyalty_notificationlog WHERE " + profile_scope + ")"),
            ("loyalty_notificationlog", profile_scope), ("loyalty_customerdataexport", profile_scope),
            ("loyalty_referral", "shop_id = %s"), ("loyalty_referralcode", profile_scope),
            ("loyalty_pointstransaction", profile_scope), ("loyalty_redemption", profile_scope),
            ("loyalty_pointslot", profile_scope), ("loyalty_tierhistory", profile_scope),
            ("loyalty_customerprofile", "shop_id = %s"),
            ("loyalty_earningrevision", "rule_id IN (SELECT id FROM loyalty_earnrule WHERE shop_id = %s) OR campaign_id IN (SELECT id FROM loyalty_campaign WHERE shop_id = %s)"),
            ("loyalty_earnrule", "shop_id = %s"), ("loyalty_campaign", "shop_id = %s"),
            ("loyalty_tier", "shop_id = %s"), ("loyalty_notificationsettings", "shop_id = %s"),
            ("loyalty_programconfigdeletion", "config_id IN (SELECT id FROM loyalty_programconfig WHERE shop_id = %s)"),
            ("loyalty_programconfig", "shop_id = %s"), ("loyalty_reward", "shop_id = %s"),
            ("loyalty_importrun", "shop_id = %s"), ("loyalty_auditlog", "shop_id = %s"),
            ("loyalty_ordercancellation", "shop_id = %s"),
            ("webhooks_webhookevent", "shop_id = %s"),
        ]
        total, batches = 0, 0
        with local_guard("loyalty.purge_shop", shop.pk) as cursor:
            for table, where in statements:
                while True:
                    cursor.execute(f'DELETE FROM "{table}" WHERE id IN (SELECT id FROM "{table}" WHERE {where} ORDER BY id LIMIT %s)',
                        [shop.pk] * where.count("%s") + [batch_size])
                    total += cursor.rowcount
                    batches += int(cursor.rowcount > 0)
                    if max_batches is not None and batches >= max_batches:
                        return total
                    if cursor.rowcount < batch_size:
                        break
            cursor.execute("DELETE FROM webhooks_webhookevent WHERE shop_id IS NULL AND shop_domain = %s", [domain])
            total += cursor.rowcount
            cursor.execute("DELETE FROM shops_shop WHERE id = %s", [shop.pk])
        return total + 1
