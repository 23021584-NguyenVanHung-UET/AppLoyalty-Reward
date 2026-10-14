"""Persistent cancellation decisions, serialized with earnings per shop/order."""
from contextlib import contextmanager
from hashlib import blake2b

from django.db import connection, transaction

from loyalty.models import CustomerProfile, OrderCancellation, PointsTransaction


@contextmanager
def order_lock(shop_id, order_id):
    # Stable across processes, unlike Python's hash(). Transaction scoped; no remote I/O.
    key = int.from_bytes(blake2b(f"loyalty-order:{shop_id}:{order_id}".encode(), digest_size=8).digest(),
                         "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])
    yield


def record_cancellation(shop, order_id):
    with transaction.atomic(), order_lock(shop.pk, order_id):
        profile_id = PointsTransaction.objects.filter(profile__shop=shop, order_id=order_id,
            type="EARN").values_list("profile_id", flat=True).first()
        if profile_id:
            CustomerProfile.objects.select_for_update().get(pk=profile_id)
        OrderCancellation.objects.get_or_create(shop=shop, order_id=order_id)
