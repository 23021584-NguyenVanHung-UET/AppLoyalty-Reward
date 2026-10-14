"""Customer account/thank-you APIs reuse the storefront views and services."""
import uuid

from django.urls import re_path
from rest_framework import serializers
from rest_framework.response import Response

from loyalty.models import PointsTransaction
from loyalty.services.customer_reporting import order_points
from loyalty.services.reporting import customer_offers
from shops.customer_auth import CustomerAccountAuthentication, IsAccountCustomer
from . import proxy
from .common import AdminView
from .not_found import ProxyNotFound


class CustomerIdentity:
    authentication_classes = [CustomerAccountAuthentication]
    permission_classes = [IsAccountCustomer]


class Me(CustomerIdentity, proxy.Me):
    pass


class Rewards(CustomerIdentity, proxy.Rewards):
    pass


class Redeem(CustomerIdentity, proxy.Redeem):
    pass


class Redemptions(CustomerIdentity, proxy.Redemptions):
    pass


class Program(CustomerIdentity, proxy.Program):
    pass


class Offers(CustomerIdentity, AdminView):
    http_method_names = ["get"]

    def get(self, request):
        return Response({"results": customer_offers(request.shop, request.shopify_customer_id)})


class PublicLedgerSerializer(serializers.Serializer):
    def to_representation(self, row):
        return {"type": row.type, "delta": row.delta, "date": row.created_at,
            "reference": uuid.uuid5(uuid.NAMESPACE_URL,
                f"loyalty:ledger:{row.profile.shop_id}:{row.profile_id}:{row.pk}").hex}


class History(CustomerIdentity, AdminView):
    http_method_names = ["get"]

    def get(self, request):
        rows = PointsTransaction.objects.filter(profile__shop=request.shop,
            profile__shopify_customer_id=request.shopify_customer_id).select_related("profile").order_by("-created_at", "-pk")
        return self.page(rows, PublicLedgerSerializer)


class OrderPoints(CustomerIdentity, AdminView):
    http_method_names = ["get"]

    def get(self, request, order_id):
        return Response(order_points(request.shop, request.shopify_customer_id, int(order_id)))


class CustomerNotFound(CustomerIdentity, ProxyNotFound):
    pass


urlpatterns = [re_path(rf"^{name}/?$", view.as_view()) for name, view in
    [("me", Me), ("rewards", Rewards), ("redeem", Redeem), ("redemptions", Redemptions), ("program", Program), ("history", History), ("offers", Offers)]]
urlpatterns += [re_path(r"^orders/(?P<order_id>[0-9]{1,19})/points/?$", OrderPoints.as_view()),
                re_path(r"^.*$", CustomerNotFound.as_view())]
