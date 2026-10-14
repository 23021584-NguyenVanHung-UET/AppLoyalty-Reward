from django.db import IntegrityError, transaction

from loyalty.models import CustomerProfile


def customer_changes(profile, email=None, first_name=None, last_name=None, email_marketing_opt_in=None, locale=None, shopify_created_at=None):
    if profile.is_anonymized:
        return {}
    changes = {field: value for field, value in {
        "email": email, "first_name": first_name, "last_name": last_name,
        "locale": locale[:20] if isinstance(locale, str) else None,
    }.items() if value and getattr(profile, field) != value}
    if type(email_marketing_opt_in) is bool and profile.email_marketing_opt_in != email_marketing_opt_in:
        changes["email_marketing_opt_in"] = email_marketing_opt_in
    if shopify_created_at is not None:
        from .engagement import aware_date
        value = aware_date(shopify_created_at)
        if value and (profile.shopify_created_at is None or value < profile.shopify_created_at):
            changes["shopify_created_at"] = value
    return changes


def upsert_customer_profile(shop, shopify_customer_id, email=None, first_name=None, last_name=None, email_marketing_opt_in=None, locale=None, shopify_created_at=None):
    with transaction.atomic():
        try:
            # A savepoint keeps the enclosing transaction usable after a unique race.
            with transaction.atomic():
                profile, created = CustomerProfile.objects.get_or_create(
                    shop=shop, shopify_customer_id=shopify_customer_id)
        except IntegrityError:
            profile = CustomerProfile.objects.get(shop=shop, shopify_customer_id=shopify_customer_id)
            created = False
        profile = CustomerProfile.objects.select_for_update().get(pk=profile.pk)
        changes = customer_changes(profile, email, first_name, last_name, email_marketing_opt_in, locale, shopify_created_at)
        if changes:
            for field, value in changes.items():
                setattr(profile, field, value)
            # Never persist cached balances from a potentially stale profile instance.
            profile.save(update_fields=[*changes, "updated_at"])
        return profile, created
