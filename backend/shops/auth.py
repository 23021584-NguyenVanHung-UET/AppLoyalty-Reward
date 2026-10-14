"""Independent authentication for embedded admin and signed storefront requests."""
from dataclasses import dataclass
import hashlib
import hmac
import math
import re
import time

import jwt
from django.conf import settings
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.permissions import BasePermission

from .models import Shop


@dataclass(frozen=True)
class SessionClaims:
    shop_domain: str
    user_id: str
    email: str = ""


def configured_domain():
    domain = settings.SHOPIFY_SHOP_DOMAIN.lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", domain):
        raise ValueError("Invalid configured shop")
    return domain


def verify_session_token(token):
    try:
        domain = configured_domain()
        if not settings.SHOPIFY_CLIENT_ID or not settings.SHOPIFY_CLIENT_SECRET:
            raise ValueError("Missing configuration")
        claims = jwt.decode(token, settings.SHOPIFY_CLIENT_SECRET, algorithms=["HS256"],
            audience=settings.SHOPIFY_CLIENT_ID, leeway=10,
            options={"require": ["exp", "nbf", "iat"], "strict_aud": True})
        if any(type(claims[key]) not in (int, float) or not math.isfinite(claims[key])
               for key in ("exp", "nbf", "iat")):
            raise ValueError("Invalid numeric date")
        issuer, destination = claims.get("iss"), claims.get("dest")
        if (not isinstance(issuer, str) or not isinstance(destination, str)
                or not issuer.startswith("https://") or not issuer.endswith("/admin")
                or issuer[:-6].lower() != f"https://{domain}"
                or not destination.startswith("https://") or destination.lower() != f"https://{domain}"):
            raise ValueError("Invalid shop")
        user_id = claims.get("sub", "unknown")
        if user_id != "unknown" and (not isinstance(user_id, str)
                or not re.fullmatch(r"[0-9]{1,30}", user_id)):
            raise ValueError("Invalid subject")
        if not Shop.objects.filter(domain__iexact=domain, is_active=True).exists():
            raise ValueError("Inactive shop")
        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError
        email = claims.get("email", "")
        try:
            validate_email(email)
        except (ValidationError, TypeError):
            email = ""
        return SessionClaims(domain, user_id, email)
    except (jwt.PyJWTError, ValueError, TypeError, AttributeError, OverflowError):
        raise AuthenticationFailed("Phiên Shopify không hợp lệ hoặc đã hết hạn.") from None


class ShopifySessionAuthentication(BaseAuthentication):
    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if len(header) != 2 or header[0].lower() != b"bearer":
            raise AuthenticationFailed("Cần phiên đăng nhập Shopify.")
        try:
            token = header[1].decode("ascii")
        except UnicodeDecodeError:
            raise AuthenticationFailed("Phiên Shopify không hợp lệ.") from None
        claims = verify_session_token(token)
        shop = Shop.objects.filter(domain__iexact=claims.shop_domain, is_active=True).first()
        if shop is None:
            raise AuthenticationFailed("Cửa hàng không hoạt động.")
        request.shop = shop
        request.shopify_user_id = claims.user_id
        return claims, claims

    def authenticate_header(self, request):
        return "Bearer"


class IsShopifyAdmin(BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.auth, SessionClaims) and hasattr(request, "shop")


def verify_app_proxy_signature(query_params):
    """Use decoded QueryDict values, including unknown and repeated parameters."""
    try:
        domain = configured_domain()
        if not settings.SHOPIFY_CLIENT_SECRET:
            raise ValueError("Missing configuration")
        for key in ("signature", "timestamp", "shop", "logged_in_customer_id"):
            if len(query_params.getlist(key)) > 1:
                raise ValueError("Ambiguous identity")
        signature = query_params.get("signature", "")
        message = "".join(f"{key}={','.join(query_params.getlist(key))}"
                          for key in sorted(query_params) if key != "signature")
        expected = hmac.new(settings.SHOPIFY_CLIENT_SECRET.encode(), message.encode(),
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError("Invalid signature")
        if abs(time.time() - int(query_params["timestamp"])) > 300:
            raise ValueError("Stale timestamp")
        if query_params.get("shop", "").lower() != domain:
            raise ValueError("Invalid shop")
        return domain
    except (ValueError, TypeError, KeyError, AttributeError):
        raise PermissionDenied("Chữ ký Shopify không hợp lệ hoặc đã hết hạn.") from None


@dataclass(frozen=True)
class ProxyClaims:
    customer_id: int


class ShopifyProxyAuthentication(BaseAuthentication):
    def authenticate(self, request):
        domain = verify_app_proxy_signature(request.query_params)
        shop = Shop.objects.filter(domain__iexact=domain, is_active=True).first()
        if shop is None:
            raise PermissionDenied("Cửa hàng không hoạt động.")
        customer_id = request.query_params.get("logged_in_customer_id", "")
        if not customer_id:
            from loyalty.services.errors import LoyaltyError
            raise LoyaltyError("NOT_LOGGED_IN", "Vui lòng đăng nhập tài khoản khách hàng.", 401)
        if not re.fullmatch(r"[1-9][0-9]{0,18}", customer_id) or int(customer_id) > 2**63 - 1:
            raise PermissionDenied("Thông tin khách hàng không hợp lệ.")
        request.shop = shop
        request.shopify_customer_id = int(customer_id)
        claims = ProxyClaims(int(customer_id))
        return claims, claims


class IsProxyCustomer(BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.auth, ProxyClaims)
