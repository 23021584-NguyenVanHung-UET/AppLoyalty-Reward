"""UI-extension tokens, distinct from Admin sessions and App Proxy signatures."""
from dataclasses import dataclass
import math
import re

import jwt
from django.conf import settings
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission

from .auth import configured_domain
from .models import Shop


@dataclass(frozen=True)
class CustomerClaims:
    shop_domain: str
    customer_id: int


def verify_customer_account_token(token):
    """No database or remote calls. Shopify documents a bare-domain dest, no iss.

    The optional sub must exist for this private API. Anonymous checkout tokens
    are deliberately rejected. Never use request-supplied identity as fallback.
    """
    try:
        domain = configured_domain()
        if not settings.SHOPIFY_CLIENT_ID or not settings.SHOPIFY_CLIENT_SECRET:
            raise ValueError()
        claims = jwt.decode(token, settings.SHOPIFY_CLIENT_SECRET, algorithms=["HS256"],
            audience=settings.SHOPIFY_CLIENT_ID, leeway=10,
            options={"require": ["exp", "nbf", "iat", "dest", "sub"], "strict_aud": True})
        if any(type(claims[key]) not in (int, float) or not math.isfinite(claims[key])
               for key in ("exp", "nbf", "iat")):
            raise ValueError()
        if not isinstance(claims["dest"], str) or claims["dest"].lower() != domain:
            raise ValueError()
        subject = claims["sub"]
        if not isinstance(subject, str) or not re.fullmatch(r"gid://shopify/Customer/[1-9][0-9]{0,18}", subject):
            raise ValueError()
        customer_id = int(subject.rsplit("/", 1)[1])
        if customer_id > 2**63 - 1:
            raise ValueError()
        return CustomerClaims(domain, customer_id)
    except (jwt.PyJWTError, ValueError, TypeError, AttributeError, OverflowError):
        raise AuthenticationFailed("Customer session is invalid or expired.") from None


class CustomerAccountAuthentication(BaseAuthentication):
    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if len(header) != 2 or header[0].lower() != b"bearer":
            raise AuthenticationFailed("Customer sign-in is required.")
        try:
            claims = verify_customer_account_token(header[1].decode("ascii"))
        except UnicodeDecodeError:
            raise AuthenticationFailed("Invalid customer session.") from None
        shop = Shop.objects.filter(domain__iexact=claims.shop_domain, is_active=True).first()
        if shop is None:
            raise AuthenticationFailed("Shop is unavailable.")
        request.shop = shop
        request.shopify_customer_id = claims.customer_id
        return claims, claims

    def authenticate_header(self, request):
        return "Bearer"


class IsAccountCustomer(BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.auth, CustomerClaims) and hasattr(request, "shop")
