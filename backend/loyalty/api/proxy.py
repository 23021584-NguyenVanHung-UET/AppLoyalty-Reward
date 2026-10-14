from rest_framework.response import Response
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from django.utils import timezone

from loyalty.models import ProgramConfig
from shops.models import Shop
from loyalty.services import reporting, storefront
from shops.auth import IsProxyCustomer, ShopifyProxyAuthentication, verify_app_proxy_signature
from .common import AdminView, validated
from .serializers import RedeemInput


class ProxyView(AdminView):
    authentication_classes = [ShopifyProxyAuthentication]
    permission_classes = [IsProxyCustomer]
    http_method_names = ["get", "post"]


class PublicProgramAuthentication(BaseAuthentication):
    """Authenticate Shopify's proxy signature without requiring a customer."""
    def authenticate(self, request):
        domain = verify_app_proxy_signature(request.query_params)
        request.shop = Shop.objects.filter(domain__iexact=domain, is_active=True).first()
        if request.shop is None:
            raise PermissionDenied()
        return request.shop, None


class Program(AdminView):
    authentication_classes = [PublicProgramAuthentication]
    permission_classes = [AllowAny]
    http_method_names = ["get"]

    def get(self, request):
        config = ProgramConfig.get_effective(request.shop, timezone.now())
        return Response(dict(enabled=bool(config and config.is_enabled), currency=request.shop.currency,
            spend_per_point=str(config.spend_per_point) if config else None,
            include_tax=config.include_tax if config else False,
            include_shipping=config.include_shipping if config else False,
            pending_days=config.pending_days if config else 0,
            expiry_days=config.expiry_days if config else 0,
            birthday_enabled=bool(config and config.birthday_enabled),
            birthday_points=config.birthday_points if config else 0,
            referral_enabled=bool(config and config.referral_enabled),
            referral_referrer_points=config.referral_referrer_points if config else 0,
            referral_referee_points=config.referral_referee_points if config else 0,
            referral_min_order_amount=str(config.referral_min_order_amount) if config and config.referral_min_order_amount is not None else None,
            referral_max_per_referrer_per_month=config.referral_max_per_referrer_per_month if config else 0))


class Me(ProxyView):
    def get(self, request):
        return Response(reporting.proxy_me(request.shop, request.shopify_customer_id))


class Rewards(ProxyView):
    def get(self, request):
        return Response({"results": reporting.proxy_rewards(request.shop, request.shopify_customer_id)})


class Redeem(ProxyView):
    def post(self, request):
        data = validated(RedeemInput, request.data)
        result, status = storefront.redeem(request.shop, request.shopify_customer_id, **data)
        return Response(result, status=status)


class Redemptions(ProxyView):
    def get(self, request):
        return Response({"results": reporting.proxy_redemptions(request.shop, request.shopify_customer_id)})
