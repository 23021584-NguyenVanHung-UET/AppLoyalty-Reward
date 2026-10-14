import time

import jwt
from django.conf import settings
from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import AuthenticationFailed

from shops.customer_auth import verify_customer_account_token
from .test_http_auth import AUTH_SETTINGS


def customer_token(customer=301, secret=None, **changes):
    now = int(time.time())
    claims = dict(dest="test.myshopify.com", aud=settings.SHOPIFY_CLIENT_ID,
        exp=now+300, iat=now, nbf=now-1, sub=f"gid://shopify/Customer/{customer}")
    claims.update(changes)
    return jwt.encode(claims, secret or settings.SHOPIFY_CLIENT_SECRET, algorithm="HS256")


@override_settings(**AUTH_SETTINGS)
class CustomerTokenTests(SimpleTestCase):
    def test_valid_without_issuer_is_pure(self):
        claims = verify_customer_account_token(customer_token())
        self.assertEqual((claims.shop_domain, claims.customer_id), ("test.myshopify.com", 301))

    def test_invalid_claims(self):
        now = time.time()
        for changes in [dict(exp=now-11), dict(nbf=now+11), dict(iat=now+11), dict(aud="other"),
                dict(aud=[settings.SHOPIFY_CLIENT_ID]), dict(dest="other.myshopify.com"),
                dict(dest="https://test.myshopify.com"), dict(dest="test.myshopify.com.evil.example"),
                dict(dest="test.myshopify.com/admin"), dict(sub=None), dict(sub="301"),
                dict(sub="gid://shopify/Order/301"), dict(sub="gid://shopify/Customer/0"),
                dict(sub="gid://shopify/Customer/9223372036854775808"), dict(iat=True),
                dict(exp="9999999999"), dict(nbf=float("nan"))]:
            with self.subTest(changes=changes), self.assertRaises(AuthenticationFailed):
                verify_customer_account_token(customer_token(**changes))

    def test_leeway(self):
        now = int(time.time())
        self.assertEqual(verify_customer_account_token(customer_token(exp=now-5)).customer_id, 301)
        self.assertEqual(verify_customer_account_token(customer_token(nbf=now+5, iat=now+5)).customer_id, 301)

    def test_missing_required_claims(self):
        claims = jwt.decode(customer_token(), options={"verify_signature": False})
        for key in ("sub", "dest", "aud", "exp", "nbf", "iat"):
            missing = {k: v for k, v in claims.items() if k != key}
            with self.subTest(key=key), self.assertRaises(AuthenticationFailed):
                verify_customer_account_token(jwt.encode(missing, settings.SHOPIFY_CLIENT_SECRET, algorithm="HS256"))

    def test_wrong_secret_malformed_and_alg_none(self):
        claims = jwt.decode(customer_token(), options={"verify_signature": False})
        for token in [customer_token(secret="a-different-secret-of-sufficient-length"), "broken", "", None,
                jwt.encode(claims, key="", algorithm="none"),
                jwt.encode(claims, "a-wrong-algorithm-secret-that-is-longer-than-forty-eight-bytes", algorithm="HS384")]:
            with self.assertRaises(AuthenticationFailed):
                verify_customer_account_token(token)

    @override_settings(SHOPIFY_CLIENT_SECRET="")
    def test_missing_configuration(self):
        with self.assertRaises(AuthenticationFailed):
            verify_customer_account_token("broken")
