from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from loyalty.models import CustomerProfile, PointsTransaction, Redemption
from loyalty.services.redemption import MESSAGES, RedeemOutcome
from shops.shopify import ShopifyTransientError
from shops.tests.test_customer_auth import customer_token
from shops.tests.test_http_auth import AUTH_SETTINGS, session_token
from webhooks.models import WebhookEvent
from .lifecycle_fixtures import PointsSetup


@override_settings(**AUTH_SETTINGS)
class CustomerAPITests(PointsSetup, APITransactionTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = APIClient(enforce_csrf_checks=True)
        self.login()

    def login(self, customer=301):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {customer_token(customer)}")

    def get(self, endpoint):
        return self.client.get(f"/api/customer/{endpoint}", HTTP_ORIGIN="null")

    def post(self, **data):
        return self.client.post("/api/customer/redeem", data or
            {"reward_id": self.reward.pk, "client_key": "account-key"}, format="json", HTTP_ORIGIN="null")

    def event(self, order=901, customer=301, status="RECEIVED", **extra):
        return WebhookEvent.objects.create(shop=self.shop, shop_domain=self.shop.domain,
            webhook_id=f"event-{WebhookEvent.objects.count()}", topic="orders/paid", status=status,
            payload={"id": order, "customer": {"id": customer}}, **extra)

    def test_read_endpoints_no_profile_creation_and_shapes(self):
        before = CustomerProfile.objects.count()
        self.login(999)
        for path in ("me", "rewards", "redemptions", "program", "history"):
            response = self.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertEqual(response["Access-Control-Allow-Origin"], "*")
            self.assertNotIn("Access-Control-Allow-Credentials", response)
        self.assertEqual(self.get("me").data["available"], 0)
        self.assertEqual(self.get("redemptions").data, {"results": []})
        self.assertEqual(self.get("history").data["results"], [])
        self.assertEqual(self.get("program").data["spend_per_point"], "10.00")
        self.assertEqual(self.get("rewards").data["results"][0]["discount_value"], "5.00")
        self.assertEqual(CustomerProfile.objects.count(), before)

    def test_missing_invalid_admin_guest_tokens_and_inactive_shop(self):
        for token in ("", "bad", session_token(), customer_token(sub=None)):
            self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            response = self.get("me")
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.data["code"], "INVALID_SESSION")
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertEqual(response["Access-Control-Allow-Origin"], "*")
        self.login()
        self.shop.is_active = False
        self.shop.save()
        self.assertEqual(self.get("program").status_code, 401)

    def test_program_disabled_and_missing_config(self):
        self.config_version(is_enabled=False)
        self.assertFalse(self.get("program").data["enabled"])
        with patch("loyalty.api.proxy.ProgramConfig.get_effective", return_value=None):
            result = self.get("program").data
        self.assertFalse(result["enabled"])
        self.assertIsNone(result["spend_per_point"])

    def offer(self, profile=None, status="ISSUED", expires=None, snapshot=None):
        profile = profile or self.profile
        index = Redemption.objects.count()
        row = Redemption.objects.create(profile=profile, reward=self.reward, points_spent=10,
            status=status, idempotency_key=f"offer:{index}", discount_code=f"LOCAL-OFFER-{index}",
            code_expires_at=expires)
        PointsTransaction.objects.create(profile=profile, type="REDEEM", delta=-10, balance_after=0,
            redemption=row, idempotency_key=f"offer-entry:{index}", rule_snapshot=snapshot if snapshot is not None else {
                "reward_name": "Saved name", "reward_description": "Saved description",
                "discount_settings": {"discount_type": "PERCENTAGE", "discount_value": "10.00",
                    "min_order_amount": "50.00", "currency": "USD"}})
        return row

    def test_offers_use_saved_terms_and_do_not_leak_ledger_details(self):
        row = self.offer(expires=self.now+timedelta(days=2))
        self.reward.name = "Changed name"
        self.reward.description = "Changed description"
        self.reward.discount_value = 99
        self.reward.save()
        with self.assertNumQueries(3):
            response = self.get("offers")
        self.assertEqual(response.status_code, 200)
        result = response.data["results"][0]
        self.assertEqual((result["code"], result["name"], result["description"]),
            (row.discount_code, "Saved name", "Saved description"))
        self.assertEqual((result["discount_type"], result["discount_value"], result["min_order_amount"]),
            ("PERCENTAGE", "10.00", "50.00"))
        self.assertEqual(set(result), {"reference", "code", "name", "description", "discount_type",
            "discount_value", "min_order_amount", "currency", "expires_at"})
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Access-Control-Allow-Origin"], "*")

    def test_offers_filter_status_expiry_and_customer_and_are_read_only(self):
        for status in ["USED", "FAILED", "PENDING_CODE", "CANCELLED", "EXPIRED"]:
            self.offer(status=status)
        self.offer(expires=self.now-timedelta(seconds=1))
        later = self.offer(expires=self.now+timedelta(days=3))
        soon = self.offer(expires=self.now+timedelta(days=1))
        other = CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=302)
        self.offer(profile=other)
        before = PointsTransaction.objects.count()
        rows = self.get("offers").data["results"]
        self.assertEqual([row["code"] for row in rows], [soon.discount_code, later.discount_code])
        self.assertEqual(PointsTransaction.objects.count(), before)
        self.assertEqual(self.client.post("/api/customer/offers", {}, format="json").status_code, 405)
        self.login(999)
        self.assertEqual(self.get("offers").data["results"], [])
        self.client.credentials()
        self.assertEqual(self.get("offers").status_code, 401)

    def test_offers_skip_missing_or_invalid_snapshots_and_legacy_description_is_optional(self):
        self.offer(snapshot={})
        self.offer(snapshot={"discount_settings": []})
        self.offer(snapshot={"discount_settings": {"discount_type": "PERCENTAGE", "discount_value": "NaN", "currency": "USD"}})
        legacy = self.offer(snapshot={"reward_name": "Legacy", "discount_settings": {
            "discount_type": "FIXED_AMOUNT", "discount_value": "5.00", "currency": "USD", "min_order_amount": None}})
        results = self.get("offers").data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual((results[0]["code"], results[0]["description"]), (legacy.discount_code, ""))

    def test_redeem_saves_description_for_checkout_offers(self):
        self.lot(20)
        self.reward.description = "An original description"
        self.reward.save()
        with patch("loyalty.services.redemption.find_discount_by_code", return_value=None), \
             patch("loyalty.services.redemption.create_discount_code", return_value="gid://shopify/DiscountCodeNode/123"):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get("offers").data["results"][0]["description"], "An original description")
        self.clean_ledger()

    def test_cors_preflight_is_unauthenticated_minimal_and_scoped(self):
        self.client.credentials()
        response = self.client.options("/api/customer/redeem", HTTP_ORIGIN="null",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST", HTTP_ACCESS_CONTROL_REQUEST_HEADERS="Authorization, Content-Type")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["Access-Control-Allow-Origin"], "*")
        self.assertEqual(response["Access-Control-Allow-Methods"], "GET, POST, OPTIONS")
        self.assertNotIn("Access-Control-Allow-Credentials", response)
        for params in ({"HTTP_ACCESS_CONTROL_REQUEST_METHOD": "DELETE"},
                       {"HTTP_ACCESS_CONTROL_REQUEST_HEADERS": "X-Customer-Id"}):
            self.assertEqual(self.client.options("/api/customer/me", **params).status_code, 403)
        self.assertNotIn("Access-Control-Allow-Origin", self.client.get("/api/admin/me"))
        self.assertNotIn("Access-Control-Allow-Origin", self.client.get("/proxy/me"))

    def test_identity_fields_never_change_verified_customer(self):
        with patch("loyalty.services.storefront.redeem_reward", return_value=RedeemOutcome("PENDING")) as redeem:
            response = self.post(reward_id=self.reward.pk, client_key="identity", customer_id=302)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(redeem.call_args.args[1], 301)
        self.assertEqual(redeem.call_args.args[3], "301:identity")
        self.lot(9)
        self.assertEqual(self.get("me?customer_id=302").data["available"], 9)

    def test_real_success_idempotent_replay(self):
        self.lot(20)
        with patch("loyalty.services.redemption.find_discount_by_code", return_value=None), \
             patch("loyalty.services.redemption.create_discount_code", return_value="gid://shopify/DiscountCodeNode/1") as create:
            first, second = self.post(), self.post()
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(first.data, second.data)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(Redemption.objects.count(), 1)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.available_balance, 10)
        self.clean_ledger()

    def test_pending_and_poll_omit_code(self):
        self.lot(20)
        with patch("loyalty.services.redemption.find_discount_by_code", side_effect=ShopifyTransientError("temporary")):
            response = self.post()
        self.assertEqual(response.status_code, 202)
        self.assertNotIn("code", response.data)
        row = self.get("redemptions").data["results"][0]
        self.assertEqual(row["status"], "PENDING_CODE")
        self.assertNotIn("code", row)

    def test_all_service_rejections_and_input_errors(self):
        for code, message in MESSAGES.items():
            if code in ("ISSUED", "PENDING"):
                continue
            with self.subTest(code=code), patch("loyalty.services.storefront.redeem_reward",
                    return_value=RedeemOutcome("REJECTED", reason_code=code, message=message)):
                cache.clear()
                response = self.post()
                self.assertIn(response.status_code, (409, 422))
                self.assertEqual(response.data["code"], code)
                self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(self.post(reward_id=True, client_key="invalid").status_code, 400)
        self.assertEqual(self.post().data["code"], "INSUFFICIENT_POINTS")
        self.assertEqual(self.get("unknown").status_code, 404)
        self.assertEqual(self.client.put("/api/customer/me", {}).status_code, 405)

    @override_settings(LOYALTY_PROXY_REDEEM_PER_MINUTE=2)
    def test_rate_limit_shared_with_proxy_and_scoped_per_customer(self):
        with patch("loyalty.services.storefront.redeem_reward", return_value=RedeemOutcome("PENDING")):
            self.assertEqual(self.post().status_code, 202)
            self.assertEqual(self.post().status_code, 202)
            self.assertEqual(self.post().status_code, 429)
            self.login(302)
            self.assertEqual(self.post().status_code, 202)

    def test_codes_and_history_isolation_and_pagination(self):
        other = CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=302)
        for index, status in enumerate(("ISSUED", "USED", "EXPIRED", "CANCELLED")):
            Redemption.objects.create(profile=self.profile, reward=self.reward, points_spent=10,
                status=status, idempotency_key=f"account:{index}", discount_code=f"LOCAL-{index}")
        Redemption.objects.create(profile=other, reward=self.reward, points_spent=10,
            status="ISSUED", idempotency_key="account:other", discount_code="OTHER-LOCAL")
        self.lot(11)
        self.lot(12)
        self.lot(88, profile=other)
        rows = self.get("redemptions").data["results"]
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum("code" in row for row in rows), 1)
        first = self.get("history?page_size=1").data
        second = self.get("history?page_size=1&page=2").data
        self.assertEqual((first["count"], first["pages"]), (2, 2))
        self.assertEqual(first["results"][0]["delta"], 12)
        self.assertEqual(second["results"][0]["delta"], 11)
        self.assertEqual(set(first["results"][0]), {"type", "delta", "date", "reference"})
        self.assertEqual(self.get("history?page=999").status_code, 404)
        self.login(302)
        self.assertEqual(len(self.get("redemptions").data["results"]), 1)

    def test_order_pending_available_revoked(self):
        self.lot(10, status="PENDING", available=self.now+timedelta(days=7), order=901)
        response = self.get("orders/901/points")
        self.assertEqual(response.data["status"], "EARNED_PENDING")
        self.assertIsNotNone(response.data["available_at"])
        self.lot(12, order=902)
        self.assertEqual(self.get("orders/902/points").data["status"], "EARNED_AVAILABLE")
        PointsTransaction.objects.create(profile=self.profile, type="REVOKE", delta=-12,
            balance_after=10, order_id=902, idempotency_key="account:revoke")
        self.assertEqual(self.get("orders/902/points").data, {"status": "REVOKED", "points": 0, "available_at": None})

    def test_order_waived_shortfall_is_not_claimed_as_earned(self):
        self.lot(10, order=903)
        PointsTransaction.objects.create(profile=self.profile, type="REVOKE", delta=-3,
            balance_after=0, order_id=903, idempotency_key="account:shortfall", rule_snapshot={"unrecovered_points": 7})
        self.assertEqual(self.get("orders/903/points").data["status"], "REVOKED")

    def test_order_unknown_and_other_customers_are_identical(self):
        self.lot(10, order=901)
        self.event(order=902)
        self.login(302)
        unknown = self.get("orders/999/points")
        for order in (901, 902, 0, 9223372036854775808):
            response = self.get(f"orders/{order}/points")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.data, unknown.data)

    def test_inflight_webhook_requires_ownership_and_does_not_create_profile(self):
        self.event(customer=999)
        self.login(999)
        before = CustomerProfile.objects.count()
        self.assertEqual(self.get("orders/901/points").data["status"], "NOT_YET_PROCESSED")
        self.assertEqual(CustomerProfile.objects.count(), before)
        self.login(301)
        self.assertEqual(self.get("orders/901/points").status_code, 404)

    def test_processed_ineligible_and_retryable_failed_events(self):
        self.event(status="SKIPPED")
        self.assertEqual(self.get("orders/901/points").data["status"], "NOT_ELIGIBLE")
        self.event(order=902, status="FAILED", attempts=1)
        self.assertEqual(self.get("orders/902/points").data["status"], "NOT_YET_PROCESSED")
