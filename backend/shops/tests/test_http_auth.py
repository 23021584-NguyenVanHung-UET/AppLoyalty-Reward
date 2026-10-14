from datetime import timedelta
import hashlib
import hmac
import time

import jwt
from django.conf import settings
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.test import APIClient

from shops.auth import verify_app_proxy_signature, verify_session_token
from shops.models import Shop

AUTH_SETTINGS = dict(SHOPIFY_CLIENT_ID="test-client", SHOPIFY_CLIENT_SECRET="test-secret-with-32-characters-minimum",
                     SHOPIFY_SHOP_DOMAIN="test.myshopify.com")


def session_token(**changes):
    now = int(time.time())
    payload = dict(iss="https://test.myshopify.com/admin", dest="https://test.myshopify.com",
                   aud=settings.SHOPIFY_CLIENT_ID, sub="123", iat=now, nbf=now-1, exp=now+60)
    payload.update(changes)
    return jwt.encode(payload, settings.SHOPIFY_CLIENT_SECRET, algorithm="HS256")


def signed_query(customer="301", **changes):
    query = QueryDict(mutable=True)
    query.update(dict(shop="test.myshopify.com", timestamp=str(int(time.time())),
                      logged_in_customer_id=str(customer), path_prefix="/apps/loyalty"))
    for key, value in changes.items():
        if isinstance(value, list):
            query.setlist(key, value)
        else:
            query[key] = str(value)
    message = "".join(f"{key}={','.join(query.getlist(key))}" for key in sorted(query))
    query["signature"] = hmac.new(settings.SHOPIFY_CLIENT_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
    return query


@override_settings(**AUTH_SETTINGS)
class SessionAuthTests(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(domain="test.myshopify.com", currency="USD")

    def test_valid_and_missing_subject(self):
        self.assertEqual(verify_session_token(session_token()).user_id, "123")
        payload = jwt.decode(session_token(), options={"verify_signature": False})
        del payload["sub"]
        token = jwt.encode(payload, settings.SHOPIFY_CLIENT_SECRET, algorithm="HS256")
        self.assertEqual(verify_session_token(token).user_id, "unknown")

    def test_claim_failures(self):
        now = int(time.time())
        for changes in (dict(exp=now-11), dict(nbf=now+11), dict(iat=now+11), dict(aud="other"),
                dict(aud=[settings.SHOPIFY_CLIENT_ID]), dict(dest="https://other.myshopify.com"),
                dict(iss="https://other.myshopify.com/admin"), dict(iss="https://test.myshopify.com/admin/"),
                dict(dest="https://test.myshopify.com?x=1"), dict(sub="invalid"),
                dict(iat=True), dict(exp=str(now+60)), dict(nbf="1"),
                dict(iss="https://test.myshopify.com/ADMIN"), dict(exp=float("inf"))):
            with self.subTest(fields=tuple(changes)), self.assertRaises(AuthenticationFailed):
                verify_session_token(session_token(**changes))

    def test_missing_required_claims(self):
        payload = jwt.decode(session_token(), options={"verify_signature": False})
        for name in ("exp", "nbf", "iat", "aud", "iss", "dest"):
            data = {key: value for key, value in payload.items() if key != name}
            with self.subTest(claim=name), self.assertRaises(AuthenticationFailed):
                verify_session_token(jwt.encode(data, settings.SHOPIFY_CLIENT_SECRET, algorithm="HS256"))

    def test_wrong_secret_none_algorithm_malformed(self):
        payload = jwt.decode(session_token(), options={"verify_signature": False})
        for token in (jwt.encode(payload, "wrong-secret-with-32-characters-minimum", algorithm="HS256"),
                      jwt.encode(payload, key=None, algorithm="none"), "broken", ""):
            with self.assertRaises(AuthenticationFailed):
                verify_session_token(token)

    def test_inactive_or_missing_shop(self):
        self.shop.is_active = False
        self.shop.save()
        with self.assertRaises(AuthenticationFailed):
            verify_session_token(session_token())
        self.shop.delete()
        with self.assertRaises(AuthenticationFailed):
            verify_session_token(session_token())

    def test_domain_case_insensitive(self):
        claims = verify_session_token(session_token(dest="https://TEST.myshopify.com", iss="https://TEST.myshopify.com/admin"))
        self.assertEqual(claims.shop_domain, "test.myshopify.com")

    def test_missing_invalid_and_cookie_only_api(self):
        client = APIClient()
        from django.contrib.auth import get_user_model
        client.force_login(get_user_model().objects.create_user(username="admin"))
        for header in (None, "Bearer broken", "Basic value", "Bearer one two"):
            headers = {"HTTP_AUTHORIZATION": header} if header else {}
            response = client.get("/api/admin/me", **headers)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response["X-Shopify-Retry-Invalid-Session-Request"], "1")
            self.assertEqual(set(response.json()), {"code", "message"})

    def test_every_admin_route_requires_token(self):
        paths = ("me", "dashboard", "customers", "customers/1", "customers/1/lots", "customers/1/ledger",
            "customers/1/block", "customers/1/unblock", "customers/1/adjust", "rewards", "rewards/1",
            "rewards/1/activate", "rewards/1/deactivate", "program-config", "program-config/history",
            "redemptions", "redemptions/1", "redemptions/1/cancel", "webhook-events", "webhook-events/1",
            "webhook-events/1/retry", "integrity", "audit-log")
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(f"/api/admin/{path}").status_code, 401)

    def test_frame_headers_and_public_page(self):
        expected = "frame-ancestors https://admin.shopify.com https://test.myshopify.com"
        for path in ("/app/", "/api/admin/me"):
            response = self.client.get(path)
            self.assertEqual(response["Content-Security-Policy"], expected)
            self.assertNotIn("X-Frame-Options", response)
        page = self.client.get("/app/diagnostic/")
        self.assertContains(page, '<meta name="shopify-api-key" content="test-client">', html=False)
        self.assertContains(page, "https://cdn.shopify.com/shopifycloud/app-bridge.js")
        self.assertContains(page, "window.shopify.idToken()")
        response = self.client.get("/admin/login/")
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertNotIn("Content-Security-Policy", response)

    def test_access_log_hides_all_signed_query_parameters(self):
        import logging
        from shops.logging import RedactRequestQuery
        record = logging.LogRecord("django.server", logging.INFO, "test", 1, '"%s" %s %s',
            ('GET /proxy/me?signature=fixture&logged_in_customer_id=301 HTTP/1.1', 200, 50), None)
        RedactRequestQuery().filter(record)
        self.assertEqual(record.getMessage(), '"GET /proxy/me HTTP/1.1" 200 50')


@override_settings(**AUTH_SETTINGS)
class ProxySignatureTests(TestCase):
    def test_valid_repeated_keys_and_encoding(self):
        query = signed_query(extra=["first value", "x&y"])
        self.assertEqual(verify_app_proxy_signature(QueryDict(query.urlencode())), "test.myshopify.com")

    def test_official_documentation_vector(self):
        query = QueryDict("extra=1&extra=2&shop=shop-name.myshopify.com&logged_in_customer_id=1&"
            "path_prefix=%2Fapps%2Fawesome_reviews&timestamp=1317327555&"
            "signature=4c68c8624d737112c91818c11017d24d334b524cb5c2b8ba08daa056f7395ddb")
        with override_settings(SHOPIFY_CLIENT_SECRET="hush", SHOPIFY_SHOP_DOMAIN="shop-name.myshopify.com"):
            from unittest.mock import patch
            with patch("shops.auth.time.time", return_value=1317327555):
                self.assertEqual(verify_app_proxy_signature(query), "shop-name.myshopify.com")

    def test_rejects_tamper_missing_wrong_secret_old_wrong_shop(self):
        tampered = signed_query()
        tampered["logged_in_customer_id"] = "999"
        missing = signed_query()
        del missing["signature"]
        wrong = signed_query()
        wrong["signature"] = "0" * 64
        for query in (tampered, missing, wrong, signed_query(timestamp=1), signed_query(shop="other.myshopify.com")):
            with self.assertRaises(PermissionDenied):
                verify_app_proxy_signature(query)

    def test_ambiguous_critical_parameter(self):
        with self.assertRaises(PermissionDenied):
            verify_app_proxy_signature(signed_query(logged_in_customer_id=["301", "302"]))
