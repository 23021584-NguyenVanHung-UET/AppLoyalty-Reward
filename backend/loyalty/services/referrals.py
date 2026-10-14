"""Referral attribution is untrusted input; all eligibility is decided server-side."""
import logging
import secrets
import unicodedata
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum

from loyalty.models import (Referral, ReferralCode, CustomerProfile, PointsLot,
    PointsTransaction, ProgramConfig, AuditLog)
from shops.models import Shop
from shops.shopify import fetch_order, map_order_snapshot, ShopifyError
from .engagement import grant_bonus, shop_zone
from .errors import LoyaltyError
from .timing import resolve_now

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
logger = logging.getLogger(__name__)


def normalized_email(value):
    if not isinstance(value, str) or "@" not in value:
        return ""
    local, domain = unicodedata.normalize("NFKC", value).strip().casefold().rsplit("@", 1)
    if domain in ("gmail.com", "googlemail.com"):
        local, domain = local.split("+", 1)[0].replace(".", ""), "gmail.com"
    return f"{local}@{domain}" if local and domain else ""


def same_person(referrer, referee, order_email=None):
    email = normalized_email(order_email or referee.email)
    return referrer.shopify_customer_id == referee.shopify_customer_id or bool(email and email == normalized_email(referrer.email))


def require_profile(profile):
    if profile.is_blocked or profile.is_anonymized or not profile.shop.is_active:
        raise LoyaltyError("PROFILE_INELIGIBLE", "Profile is not eligible.", 409)


def create_code(profile):
    with transaction.atomic():
        profile = CustomerProfile.objects.select_for_update().get(pk=profile.pk)
        require_profile(profile)
        config = ProgramConfig.get_effective(profile.shop)
        if not config or not config.is_enabled or not config.referral_enabled:
            raise LoyaltyError("REFERRAL_DISABLED", "Referral program is disabled.", 409)
        existing = ReferralCode.objects.filter(profile=profile).first()
        if existing:
            if not existing.is_active:
                raise LoyaltyError("INACTIVE_CODE", "Your referral code is inactive.", 409)
            return existing
        for _ in range(10):
            try:
                with transaction.atomic():
                    return ReferralCode.objects.create(profile=profile, code="".join(secrets.choice(ALPHABET) for _ in range(8)))
            except IntegrityError:
                # Profile lock serializes create-or-return; only a random code collision remains.
                continue
        raise LoyaltyError("CODE_UNAVAILABLE", "Please retry creating your referral code.", 503)


def referral_summary(profile):
    require_profile(profile)
    code = ReferralCode.objects.filter(profile=profile, is_active=True).first()
    rows = Referral.objects.filter(referrer_profile=profile)
    counts = {row["status"]: row["count"] for row in rows.values("status").annotate(count=Count("pk"))}
    points = PointsTransaction.objects.filter(profile=profile, type="EARN", rule_snapshot__code="referral_referrer").aggregate(value=Sum("delta"))["value"] or 0
    return {"code": code.code if code else None, "share_link": f"https://{profile.shop.domain}/?ref={code.code}" if code else None,
        "counts": {status.lower(): counts.get(status, 0) for status in Referral.Status.values}, "points_earned": points}


def reject_locked(row, reason, now):
    if row.status != "PENDING":
        return row
    row.status, row.reject_reason, row.rejected_at, row.updated_at = "REJECTED", reason, now, now
    row.save(update_fields=["status", "reject_reason", "rejected_at", "updated_at"])
    return row


def known_adverse_event(shop_id, order_id):
    from webhooks.models import WebhookEvent
    identities = [order_id, str(order_id), f"gid://shopify/Order/{order_id}"]
    events = WebhookEvent.objects.filter(shop_id=shop_id)
    if events.filter(topic="orders/cancelled", payload__id__in=identities).exists():
        return "CANCELLED"
    if events.filter(topic="refunds/create", payload__order_id__in=identities).exists():
        return "REFUNDED"
    return ""


def record_referral(profile, order, order_lot, config, eligible_spend, now=None):
    """Called inside earn, after its Shop then profile locks. No imports call this."""
    now = resolve_now(now)
    raw_code = order.custom_attributes.get("loyalty_ref")
    if not raw_code or not config.referral_enabled:
        return None
    # Lock independently as well for callers/tests; no_key permits FK audit inserts.
    with transaction.atomic():
        Shop.objects.select_for_update(no_key=True).get(pk=profile.shop_id)
        existing = Referral.objects.filter(shop_id=profile.shop_id, referee_order_id=order.order_id).first()
        if existing:
            return existing
        code = ReferralCode.objects.select_related("profile").filter(code=str(raw_code).strip().upper(), profile__shop_id=profile.shop_id).first()
        referrer = code.profile if code else None
        reason = ""
        if not code:
            reason = "UNKNOWN_CODE"
        elif not code.is_active or referrer.is_blocked or referrer.is_anonymized:
            reason = "INACTIVE_CODE"
        elif same_person(referrer, profile, order.customer_email):
            reason = "SELF_REFERRAL"
        elif Referral.objects.filter(shop_id=profile.shop_id, referee_customer_id=profile.shopify_customer_id).exists():
            reason = "ALREADY_REFERRED"
        elif PointsTransaction.objects.filter(profile=profile, type="EARN", order_id__isnull=False).exclude(order_id=order.order_id).exists():
            reason = "EXISTING_CUSTOMER"
        elif min(value for value in (profile.created_at, profile.shopify_created_at, order.customer_created_at) if value is not None) < now-timedelta(days=30):
            reason = "PROFILE_TOO_OLD"
        elif config.referral_min_order_amount is not None and eligible_spend < config.referral_min_order_amount:
            reason = "BELOW_MINIMUM"
        # Use cached timezone resolved before earn acquired profile locks.
        month = now.astimezone(shop_zone(profile.shop)).date().replace(day=1)
        if not reason and Referral.objects.filter(referrer_profile=referrer, qualifying_month=month, status__in=["PENDING", "REWARDED"]).count() >= config.referral_max_per_referrer_per_month:
            reason = "MONTHLY_CAP"
        if not reason:
            reason = "CANCELLED" if order.cancelled else "REFUNDED" if order.refund_summaries else known_adverse_event(profile.shop_id, order.order_id)
        row = Referral.objects.create(shop_id=profile.shop_id, referrer_profile=referrer,
            referee_profile=profile, referee_customer_id=profile.shopify_customer_id, referee_order_id=order.order_id,
            source_code=code, order_lot=order_lot, qualifying_month=month, created_at=now, updated_at=now,
            status="REJECTED" if reason else "PENDING", reject_reason=reason, rejected_at=now if reason else None,
            rule_snapshot={"program_config_id": config.pk, "program_config_version": config.version,
                "referrer_points": config.referral_referrer_points, "referee_points": config.referral_referee_points,
                "expiry_days": config.expiry_days, "min_order_amount": str(config.referral_min_order_amount) if config.referral_min_order_amount is not None else None,
                "monthly_cap": config.referral_max_per_referrer_per_month, "eligible_spend": str(eligible_spend),
                "timezone": profile.shop.iana_timezone})
        return row


def reject_for_order(shop, order_id, reason, now=None):
    now = resolve_now(now)
    with transaction.atomic():
        Shop.objects.select_for_update(no_key=True).get(pk=shop.pk)
        for row in Referral.objects.select_for_update().filter(shop=shop, referee_order_id=order_id, status="PENDING"):
            reject_locked(row, reason, now)


def manual_reject(shop, referral_id, actor, now=None):
    now = resolve_now(now)
    with transaction.atomic():
        Shop.objects.select_for_update(no_key=True).get(pk=shop.pk)
        row = Referral.objects.select_for_update().filter(pk=referral_id, shop=shop).first()
        if not row:
            raise LoyaltyError("NOT_FOUND", "Referral not found.", 404)
        if row.status != "PENDING":
            raise LoyaltyError("REFERRAL_NOT_PENDING", "Only pending referrals can be rejected.", 409)
        reject_locked(row, "MANUAL_REJECT", now)
        AuditLog.objects.create(shop=shop, actor=actor, action="referral.reject", target_type="Referral", target_id=str(row.pk),
            before={"status": "PENDING"}, after={"status": "REJECTED", "reject_reason": "MANUAL_REJECT"})
        return row


def reward_referral(referral_id, now=None):
    now = resolve_now(now)
    initial = Referral.objects.select_related("order_lot").filter(pk=referral_id, status="PENDING").first()
    if not initial:
        return "NOOP"
    raw = None
    if initial.created_at >= now-timedelta(days=90) and initial.order_lot.status == "AVAILABLE":
        raw = fetch_order(initial.referee_order_id)
        if raw is None:
            raise ShopifyError("Cannot verify referral order.")
    with transaction.atomic():
        shop = Shop.objects.select_for_update(no_key=True).get(pk=initial.shop_id)
        # Lock wallets in a stable order before the referral row.
        profiles = {row.pk: row for row in CustomerProfile.objects.select_for_update().filter(
            pk__in=[initial.referrer_profile_id, initial.referee_profile_id]).order_by("pk")}
        row = Referral.objects.select_for_update().get(pk=referral_id)
        if row.status != "PENDING":
            return "NOOP"
        def reject(reason):
            reject_locked(row, reason, now)
            return "REJECTED"
        if row.created_at < now-timedelta(days=90):
            return reject("EXPIRED_PENDING")
        referrer, referee = profiles.get(row.referrer_profile_id), profiles.get(row.referee_profile_id)
        if not shop.is_active or not referrer or not referee or any(p.is_blocked or p.is_anonymized for p in (referrer, referee)):
            return reject("PROFILE_INELIGIBLE")
        if not row.source_code_id or not ReferralCode.objects.filter(pk=row.source_code_id, is_active=True).exists():
            return reject("INACTIVE_CODE")
        adverse = known_adverse_event(row.shop_id, row.referee_order_id)
        if adverse:
            return reject(adverse)
        lot = PointsLot.objects.select_for_update().get(pk=row.order_lot_id)
        held = PointsTransaction.objects.filter(profile=referee, order_id=row.referee_order_id, type__in=["EARN", "REVOKE"]).aggregate(points=Sum("delta"))["points"] or 0
        if held <= 0 or lot.status in ("VOIDED", "EXPIRED", "CONSUMED") or not lot.points_remaining:
            return reject("NO_POINTS_HELD")
        if lot.status != "AVAILABLE" or raw is None:
            return "PENDING"
        order = map_order_snapshot(raw)
        if order.order_id != row.referee_order_id or order.customer_id != row.referee_customer_id:
            return reject("ORDER_MISMATCH")
        if order.cancelled:
            return reject("CANCELLED")
        # Conservative rule: any known refund before grant rejects, including partial refunds.
        if order.refund_summaries or order.financial_status == "PARTIALLY_REFUNDED":
            return reject("REFUNDED")
        if order.financial_status not in ("PAID", "PARTIALLY_REFUNDED"):
            return reject("NO_POINTS_HELD")
        if same_person(referrer, referee, order.customer_email):
            return reject("SELF_REFERRAL")
        effective = ProgramConfig.get_effective(shop, now)
        if not effective or not effective.is_enabled:
            return "PENDING"  # Program-wide pause; frozen referral entitlements remain.
        config = ProgramConfig.objects.get(pk=row.rule_snapshot["program_config_id"], shop=shop)
        grant_bonus(referrer, row.rule_snapshot["referrer_points"], f"referral:{row.pk}:referrer",
            "referral_referrer", "REFERRAL", config, now, referral_id=row.pk)
        grant_bonus(referee, row.rule_snapshot["referee_points"], f"referral:{row.pk}:referee",
            "referral_referee", "REFERRAL", config, now, referral_id=row.pk)
        row.status, row.rewarded_at, row.updated_at = "REWARDED", now, now
        row.save(update_fields=["status", "rewarded_at", "updated_at"])
        return "REWARDED"


def referral_batch(now=None, limit=100, after_id=0):
    now = resolve_now(now)
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    ids = list(Referral.objects.filter(pk__gt=after_id, status="PENDING").order_by("pk").values_list("pk", flat=True)[:limit+1])
    counts = {"REWARDED": 0, "REJECTED": 0, "PENDING": 0, "NOOP": 0, "FAILED": 0}
    for referral_id in ids[:limit]:
        try:
            counts[reward_referral(referral_id, now)] += 1
        except ShopifyError as exc:
            # Provider data, emails and codes must not appear in logs.
            logger.warning("referral_verify_failed referral_id=%s error_class=%s", referral_id, type(exc).__name__)
            counts["FAILED"] += 1
    return {"scanned": min(len(ids), limit), "counts": counts, "has_more": len(ids)>limit,
        "next_after_id": ids[min(len(ids), limit)-1] if ids else after_id}
