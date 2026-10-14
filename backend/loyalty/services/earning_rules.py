"""Select historical rule revisions using the order's creation time."""
from loyalty.models import EarningRevision


def revisions_at(shop, at, kind):
    if at is None:
        # Old test/prototype orders carry no date. Never apply dated rules to them.
        if EarningRevision.objects.filter(**{f"{kind}__shop": shop}).exists():
            from shops.shopify import ShopifyError
            raise ShopifyError("Shopify order createdAt is required for earning rules.")
        return []
    rows = EarningRevision.objects.filter(**{f"{kind}__shop": shop}, effective_at__lte=at
        ).order_by(f"{kind}_id", "-effective_at", "-pk").distinct(f"{kind}_id")
    return [row.values for row in rows]


def enqueue_tier_evaluation(profile_id):
    from django.db import transaction
    from django_q.tasks import async_task
    transaction.on_commit(lambda: async_task("loyalty.tasks.evaluate_profile_tier", profile_id))
