from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from loyalty.models import CustomerProfile, Redemption
from loyalty.services.adjustments import adjust_points
from loyalty.services.redemption import MESSAGES, RedeemOutcome
from shops.shopify import ShopifyTransientError
from shops.tests.test_http_auth import AUTH_SETTINGS, signed_query
from .lifecycle_fixtures import PointsSetup


@override_settings(**AUTH_SETTINGS)
class ProxyAPITests(PointsSetup, APITransactionTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = APIClient(enforce_csrf_checks=True)

    def path(self, endpoint, customer="301", query=None):
        query = signed_query(customer) if query is None else query
        return f"/proxy/{endpoint}?{query.urlencode()}"

    def get(self, endpoint, customer="301", query=None):
        return self.client.get(self.path(endpoint, customer, query))

    def post(self, data=None, customer="301", query=None):
        return self.client.post(self.path("redeem", customer, query),
            data or {"reward_id": self.reward.pk, "client_key": "widget-key"}, format="json")

    def test_missing_signature_tamper_wrong_secret_stale_wrong_shop(self):
        queries = [signed_query(timestamp=1), signed_query(shop="other.myshopify.com")]
        missing = signed_query()
        del missing["signature"]
        queries.append(missing)
        tampered = signed_query()
        tampered["logged_in_customer_id"] = "999"
        queries.append(tampered)
        with override_settings(SHOPIFY_CLIENT_SECRET="wrong-secret"):
            queries.append(signed_query())
        for query in queries:
            response = self.post(query=query)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(set(response.data), {"code", "message"})
            self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(Redemption.objects.count(), 0)

    def test_public_program_signed_shape_no_customer_and_no_store(self):
        before = CustomerProfile.objects.count()
        response = self.get("program", customer="")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(enabled=True, currency="USD", spend_per_point="10.00",
            include_tax=False, include_shipping=False, pending_days=7, expiry_days=30,
            birthday_enabled=False,birthday_points=0,referral_enabled=False,referral_referrer_points=0,
            referral_referee_points=0,referral_min_order_amount=None,referral_max_per_referrer_per_month=10))
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(CustomerProfile.objects.count(), before)
        self.assertEqual(self.client.post(self.path("program", customer=""), {}).status_code, 405)

    def test_public_program_rejects_unsigned_tampered_stale_inactive_shop(self):
        self.assertEqual(self.client.get("/proxy/program").status_code, 403)
        for query in (signed_query(customer="", timestamp=1), signed_query(customer="", shop="other.myshopify.com")):
            self.assertEqual(self.get("program", query=query).status_code, 403)
        query = signed_query(customer="")
        query["shop"] = "other.myshopify.com"
        self.assertEqual(self.get("program", query=query).status_code, 403)
        self.shop.is_active = False
        self.shop.save(update_fields=["is_active"])
        self.assertEqual(self.get("program", customer="").status_code, 403)

    def test_public_program_disabled_and_no_effective_config(self):
        self.config_version(is_enabled=False)
        self.assertFalse(self.get("program", customer="").data["enabled"])
        with patch("loyalty.api.proxy.ProgramConfig.get_effective", return_value=None):
            response = self.get("program", customer="")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(enabled=False, currency="USD", spend_per_point=None,
            include_tax=False, include_shipping=False, pending_days=0, expiry_days=0,
            birthday_enabled=False,birthday_points=0,referral_enabled=False,referral_referrer_points=0,
            referral_referee_points=0,referral_min_order_amount=None,referral_max_per_referrer_per_month=0))

    def test_not_logged_in_empty_or_missing_customer(self):
        self.assertEqual(self.get("me", customer="").data["code"], "NOT_LOGGED_IN")
        query = signed_query(customer="")
        # Sign again without the optional logged-in identity parameter.
        import hashlib
        import hmac
        from django.conf import settings
        del query["logged_in_customer_id"]
        del query["signature"]
        message = "".join(f"{key}={','.join(query.getlist(key))}" for key in sorted(query))
        query["signature"] = hmac.new(settings.SHOPIFY_CLIENT_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
        response = self.get("me", query=query)
        self.assertEqual((response.status_code, response.data["code"]), (401, "NOT_LOGGED_IN"))

    def test_me_unknown_zeros_no_creation_json_and_no_store(self):
        before = CustomerProfile.objects.count()
        response = self.get("me", customer="999")
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.data["available"], response.data["pending"], response.data["debt"]), (0, 0, 0))
        self.assertEqual(response.data["ledger"], [])
        self.assertEqual(CustomerProfile.objects.count(), before)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertTrue(response["Content-Type"].startswith("application/json"))

    def test_me_balances_expiry_and_safe_ledger(self):
        self.lot(5, expires=self.now+timedelta(days=10))
        self.lot(3, expires=self.now+timedelta(days=40))
        self.lot(2, status="PENDING", expires=self.now+timedelta(days=20))
        response = self.get("me")
        self.assertEqual((response.data["available"], response.data["pending"], response.data["expiring_points"]), (8, 2, 7))
        self.assertEqual(len(response.data["ledger"]), 3)
        self.assertEqual(set(response.data["ledger"][0]), {"type", "delta", "date", "reference"})
        self.assertEqual(len(response.data["ledger"][0]["reference"]), 32)

    def test_debt_display_never_negative_available(self):
        self.debt(7)
        response = self.get("me")
        self.assertEqual((response.data["available"], response.data["debt"]), (0, 7))

    def test_customer_identity_body_ignored(self):
        with patch("loyalty.services.storefront.redeem_reward", return_value=RedeemOutcome("PENDING")) as redeem:
            response = self.post({"reward_id": self.reward.pk, "client_key": "body-test",
                                  "customer_id": 999, "shopify_customer_id": 999})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(redeem.call_args.args[1], 301)
        self.assertEqual(redeem.call_args.args[3], "301:body-test")

    def test_rewards_active_window_affordable_and_decimal(self):
        self.lot(10)
        self.assertTrue(self.get("rewards").data["results"][0]["affordable"])
        self.assertEqual(self.get("rewards").data["results"][0]["discount_value"], "5.00")
        self.assertFalse(self.get("rewards", customer="999").data["results"][0]["affordable"])
        self.reward.active_to = self.now-timedelta(seconds=1)
        self.reward.save()
        self.assertEqual(self.get("rewards").data["results"], [])

    def test_redeem_success_idempotent_replay(self):
        self.lot(20)
        with patch("loyalty.services.redemption.find_discount_by_code", return_value=None), \
             patch("loyalty.services.redemption.create_discount_code", return_value="gid://shopify/DiscountCodeNode/1") as create:
            first = self.post()
            second = self.post()
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertTrue(bool(first.data["code"]))
        self.assertTrue(first.data["code"] == second.data["code"])
        self.assertIsNotNone(first.data["expires_at"])
        self.assertEqual(create.call_count, 1)
        self.assertEqual(Redemption.objects.count(), 1)
        self.clean_ledger()

    def test_transient_pending_and_poll(self):
        self.lot(20)
        with patch("loyalty.services.redemption.find_discount_by_code", side_effect=ShopifyTransientError("transient")):
            response = self.post()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["poll"], "redemptions")
        self.assertNotIn("code", response.data)
        self.assertEqual(self.get("redemptions").data["results"][0]["status"], "PENDING_CODE")
        self.clean_ledger()

    def test_each_rejection_code_maps_to_public_json(self):
        for code, message in MESSAGES.items():
            if code in ("ISSUED", "PENDING"):
                continue
            with self.subTest(code=code), patch("loyalty.services.storefront.redeem_reward",
                    return_value=RedeemOutcome("REJECTED", reason_code=code, message=message)):
                cache.clear()
                response = self.post()
                self.assertIn(response.status_code, (409, 422))
                self.assertEqual(response.data["code"], code)
                self.assertEqual(set(response.data), {"code", "message"})

    def test_real_rejection_no_points_and_unknown_profile(self):
        self.assertEqual(self.post().data["code"], "INSUFFICIENT_POINTS")
        self.assertEqual(self.post(customer="999").data["code"], "CUSTOMER_NOT_FOUND")

    @override_settings(LOYALTY_PROXY_REDEEM_PER_MINUTE=2)
    def test_rate_limit_scoped_per_customer(self):
        with patch("loyalty.services.storefront.redeem_reward", return_value=RedeemOutcome("PENDING")) as redeem:
            self.assertEqual(self.post().status_code, 202)
            self.assertEqual(self.post().status_code, 202)
            response = self.post()
            self.assertEqual((response.status_code, response.data["code"]), (429, "RATE_LIMITED"))
            self.assertEqual(self.post(customer="302").status_code, 202)
        self.assertEqual(redeem.call_count, 3)

    def test_no_cross_customer_leak_codes_only_issued(self):
        other = CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=302)
        for index, status in enumerate(("ISSUED", "USED", "FAILED", "PENDING_CODE", "EXPIRED", "CANCELLED")):
            Redemption.objects.create(profile=self.profile, reward=self.reward, points_spent=10,
                status=status, idempotency_key=f"visibility:{index}", discount_code=f"FIXTURE-{index}")
        Redemption.objects.create(profile=other, reward=self.reward, points_spent=10,
            status="ISSUED", idempotency_key="visibility:other", discount_code="FIXTURE-OTHER")
        with self.assertNumQueries(2):
            response = self.get("redemptions")
        self.assertEqual(len(response.data["results"]), 6)
        self.assertEqual(sum("code" in row for row in response.data["results"]), 1)
        self.assertTrue(all("idempotency_key" not in row and "profile_id" not in row for row in response.data["results"]))
        self.assertEqual(len(self.get("redemptions", customer="302").data["results"]), 1)
        self.assertEqual(self.get("redemptions", customer="999").data["results"], [])

    def test_method_and_malformed_body_safe_errors(self):
        response = self.client.put(self.path("me"), {}, format="json")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(self.post({"reward_id": True, "client_key": "bad"}).status_code, 400)
        response = self.client.post(self.path("redeem"), "{broken", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.data), {"code", "message"})

    def test_unknown_route_json_and_no_store(self):
        response = self.get("unknown")
        self.assertEqual((response.status_code, response.data["code"]), (404, "NOT_FOUND"))
        self.assertEqual(response["Cache-Control"], "no-store")
