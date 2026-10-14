"""Read-only order summary; ownership is established before any status is returned."""
from django.db.models import Q

from loyalty.models import AuditLog, PointsTransaction
from webhooks.models import WebhookEvent
from .errors import LoyaltyError


def order_points(shop, customer_id, order_id):
    if not 0 < order_id <= 2**63 - 1:
        raise LoyaltyError("NOT_FOUND", "Not found.", 404)
    entries = PointsTransaction.objects.filter(profile__shop=shop,
        profile__shopify_customer_id=customer_id, order_id=order_id)
    earns = list(entries.filter(type="EARN").select_related("lot"))
    if earns:
        revokes = list(entries.filter(type="REVOKE"))
        waived = sum(row.rule_snapshot.get("unrecovered_points", 0) for row in revokes)
        waived += sum(row.get("unrecovered_points", 0) for row in AuditLog.objects.filter(
            shop=shop, action="revoke_unrecovered", target_type="RevokeEvent",
            after__profile_id=earns[0].profile_id, after__order_id=order_id).values_list("after", flat=True))
        net = max(0, sum(row.delta for row in earns + revokes) - waived)
        lots = [row.lot for row in earns if row.lot is not None]
        pending = [lot for lot in lots if lot.status == "PENDING"]
        status = "REVOKED" if net == 0 else "EARNED_PENDING" if pending else "EARNED_AVAILABLE"
        return {"status": status, "points": net,
                "available_at": min((lot.available_at for lot in pending), default=None)}
    # Authenticated Shopify webhook payload is the only local ownership evidence
    # before an EARN/profile exists. Matching the order id alone is insufficient.
    events = WebhookEvent.objects.filter(shop=shop, topic__in=["orders/paid", "orders/create"]).filter(
        Q(payload__id=order_id) | Q(payload__id=str(order_id)) | Q(payload__id=f"gid://shopify/Order/{order_id}")
    ).filter(Q(payload__customer__id=customer_id) | Q(payload__customer__id=str(customer_id))
             | Q(payload__customer__id=f"gid://shopify/Customer/{customer_id}"))
    if not events.exists():
        raise LoyaltyError("NOT_FOUND", "Not found.", 404)
    from webhooks.tasks import MAX_ATTEMPTS
    pending = events.filter(Q(status__in=["RECEIVED", "PROCESSING"])
        | Q(status="FAILED", attempts__lt=MAX_ATTEMPTS)).exists()
    return {"status": "NOT_YET_PROCESSED" if pending else "NOT_ELIGIBLE", "points": 0, "available_at": None}
