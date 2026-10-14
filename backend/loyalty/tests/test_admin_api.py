from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from loyalty.models import AuditLog, CustomerProfile, PointsTransaction, ProgramConfig, ProgramConfigDeletion, Redemption, Reward
from loyalty.services.adjustments import adjust_points
from loyalty.services.redemption import redeem_reward
from shops.models import Shop
from shops.tests.test_http_auth import AUTH_SETTINGS, session_token
from webhooks.models import WebhookEvent
from .lifecycle_fixtures import PointsSetup


@override_settings(**AUTH_SETTINGS)
class AdminAPITests(PointsSetup, APITransactionTestCase):
    def setUp(self):
        super().setUp()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {session_token()}")
        self.root = "/api/admin/"
        self.customer = f"customers/{self.profile.pk}"

    def get(self, path, **params):
        return self.client.get(self.root+path, params)

    def post(self, path, data=None):
        return self.client.post(self.root+path, data or {}, format="json")

    def adjustment(self, **changes):
        return {"delta": 10, "reason_code": "GIFT", "note": "Test adjustment", "client_key": "api-key", **changes}

    def test_me_and_decimal_strings(self):
        response = self.get("me")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["shop"], self.shop.domain)
        self.assertEqual(response.data["currency"], "USD")
        self.assertEqual(response.data["program_config"]["spend_per_point"], "10.00")
        self.assertEqual(response["Cache-Control"], "no-store")

    @override_settings(LOYALTY_MAX_ADJUST_POINTS=4321)
    def test_me_exposes_adjustment_limit_for_form_validation(self):
        self.assertEqual(self.get("me").data["limits"], {"max_adjust_points": 4321})

    def test_detail_diagnostics_never_echo_stored_provider_text(self):
        row = self.issued()
        private = "fixture-secret fixture@example.invalid full-code-must-not-leak"
        for stored, expected in (("SHOPIFY_TRANSIENT:" + private, "temporarily unavailable"),
                                 (private, "Issuance has not completed"), ("", "")):
            row.last_error = stored
            row.save(update_fields=["last_error"])
            response = self.get(f"redemptions/{row.pk}")
            self.assertIn(expected, response.data["last_error"])
            self.assertNotIn(private, response.data["last_error"])
            self.assertNotIn("last_error", self.get("redemptions").data["results"][0])
        event = WebhookEvent.objects.create(shop=self.shop, shop_domain=self.shop.domain,
            webhook_id="safe-diagnostic", topic="orders/paid", status="FAILED", error=private, payload={})
        for response in (self.get("webhook-events").data["results"][0],
                         self.get(f"webhook-events/{event.pk}").data):
            self.assertIn("Processing failed", response["error"])
            self.assertNotIn(private, str(response))
        event.status = "PROCESSED"
        event.save(update_fields=["status"])
        self.assertEqual(self.get(f"webhook-events/{event.pk}").data["error"], "")

    def test_dashboard_aggregates(self):
        self.lot(10)
        self.lot(4, status="PENDING")
        other = CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=302)
        adjust_points(other, -3, "CORRECTION", "Test correction", "actor", "debt", self.now)
        WebhookEvent.objects.create(shop=self.shop, shop_domain=self.shop.domain, webhook_id="dashboard",
            topic="orders/paid", payload={}, status="PROCESSED")
        with self.assertNumQueries(8):
            response = self.get("dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["period"]["earned"], {"count": 2, "points": 14})
        self.assertEqual(response.data["liability"], {"available": 10, "pending": 4})
        self.assertEqual(response.data["profiles_in_debt"], 1)
        self.assertEqual(response.data["profiles_with_balance"], 1)
        self.assertEqual(response.data["webhook_events"], {"PROCESSED": 1})
        self.assertEqual(self.get("dashboard", days=0).status_code, 400)

    def test_customers_pagination_search_filter_ordering_query_count(self):
        for index in range(30):
            CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=1000+index,
                email=f"dummy{index}@example.invalid", first_name=f"Fixture {index}", available_balance=index)
        with self.assertNumQueries(4):
            response = self.get("customers", ordering="-balance")
        self.assertEqual(response.data["count"], 31)
        self.assertEqual(len(response.data["results"]), 25)
        self.assertEqual(response.data["results"][0]["available_balance"], 29)
        self.assertEqual(len(self.get("customers", page_size=999).data["results"]), 31)
        self.assertEqual(self.get("customers", search="dummy29").data["count"], 1)
        self.assertEqual(self.get("customers", search="Fixture 29").data["count"], 1)
        self.assertEqual(self.get("customers", search="1029").data["count"], 1)
        self.assertEqual(self.get("customers", has_balance="true").data["count"], 29)
        self.assertEqual(self.get("customers", in_debt="true").data["count"], 0)
        self.assertEqual(self.get("customers", blocked="false").data["count"], 31)
        self.assertEqual(self.get("customers", ordering="email").status_code, 400)
        self.assertEqual(self.get("customers", blocked="bad").status_code, 400)
        self.assertEqual(self.get("customers", page=999).status_code, 404)

    def test_detail_lots_ledger_and_snapshot_opt_in(self):
        self.lot(5)
        self.lot(7, status="PENDING")
        detail = self.get(self.customer).data
        self.assertEqual(detail["lots_counts"], {"AVAILABLE": 1, "PENDING": 1})
        self.assertEqual(self.get(self.customer+"/lots", status="PENDING").data["count"], 1)
        with self.assertNumQueries(5):
            response = self.get(self.customer+"/ledger", page_size=1)
        self.assertNotIn("rule_snapshot", response.data["results"][0])
        self.assertIn("rule_snapshot", self.get(self.customer+"/ledger", detail=1).data["results"][0])
        self.assertEqual(self.get(self.customer+"/lots", status="INVALID").status_code, 400)

    def test_block_unblock_and_audit(self):
        self.assertEqual(self.post(self.customer+"/block").status_code, 400)
        response = self.post(self.customer+"/block", {"reason": "Manual review"})
        self.assertTrue(response.data["is_blocked"])
        self.assertEqual(self.get("customers", blocked="true").data["count"], 1)
        self.assertFalse(self.post(self.customer+"/unblock").data["is_blocked"])
        self.assertEqual(AuditLog.objects.filter(actor="shopify_user:123").count(), 2)
        self.assertEqual(self.get("audit-log", action="customer.block").data["count"], 1)

    def test_adjust_replay_key_reuse_and_validation(self):
        path = self.customer+"/adjust"
        first = self.post(path, self.adjustment())
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.data["duplicate"])
        self.assertTrue(self.post(path, self.adjustment()).data["duplicate"])
        response = self.post(path, self.adjustment(delta=11))
        self.assertEqual((response.status_code, response.data["code"]), (409, "KEY_REUSED"))
        for data in (self.adjustment(delta=0), self.adjustment(delta=100001), self.adjustment(note=""),
                     self.adjustment(delta=True), self.adjustment(delta=1.0), {"delta": 5}):
            self.assertIn(self.post(path, data).status_code, (400, 422))
        self.clean_ledger()

    def test_adjust_anonymized_and_insufficient(self):
        self.config_version(allow_negative_balance=False)
        response = self.post(self.customer+"/adjust", self.adjustment(delta=-1))
        self.assertEqual(response.data["code"], "INSUFFICIENT_POINTS")
        self.profile.is_anonymized = True
        self.profile.save()
        self.assertEqual(self.post(self.customer+"/adjust", self.adjustment()).data["code"], "CUSTOMER_ANONYMIZED")

    def test_reward_create_patch_activate_deactivate_validation(self):
        data = dict(name="API reward", points_cost=5, discount_type="FIXED_AMOUNT", discount_value="2.50")
        response = self.post("rewards", data)
        self.assertEqual(response.status_code, 201)
        pk = response.data["id"]
        path = f"rewards/{pk}"
        response = self.client.patch(self.root+path, {"points_cost": 7}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["points_cost"], 7)
        self.assertFalse(self.post(path+"/deactivate").data["is_active"])
        self.assertTrue(self.post(path+"/activate").data["is_active"])
        self.assertEqual(self.client.delete(self.root+path).status_code, 405)
        for changes in (dict(points_cost=0), dict(discount_value="0"),
                dict(discount_type="PERCENTAGE", discount_value="101"),
                dict(active_from=(self.now+timedelta(days=2)).isoformat(), active_to=self.now.isoformat())):
            self.assertIn(self.post("rewards", {**data, **changes}).status_code, (400, 422))
        response = self.post("rewards", {**data, "discount_type": "PERCENTAGE", "max_discount_amount": "10"})
        self.assertEqual((response.status_code, response.data["code"]), (422, "UNSUPPORTED"))
        self.assertEqual(AuditLog.objects.filter(action__startswith="reward.").count(), 4)
        with self.assertNumQueries(4):
            self.assertEqual(self.get("rewards").status_code, 200)

    def test_config_new_version_clone_ranges_and_immutability(self):
        before = self.config.spend_per_point
        response = self.post("program-config", {"spend_per_point": "20.00", "expiry_days": 60})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["version"], 2)
        self.assertEqual(response.data["created_by"], "shopify_user:123")
        self.assertEqual(response.data["pending_days"], 7)
        self.config.refresh_from_db()
        self.assertEqual(self.config.spend_per_point, before)
        with self.assertRaises(ValidationError):
            self.config.save()
        self.assertEqual(self.get("program-config").data["version"], 2)
        self.assertEqual(self.get("program-config/history").data["count"], 2)
        for data in (dict(pending_days=366), dict(expiry_days=3651), dict(spend_per_point="0"),
            dict(max_redemptions_per_day=-1), dict(negative_balance_alert_threshold=1),
            dict(effective_from=(timezone.now()-timedelta(seconds=61)).isoformat())):
            self.assertIn(self.post("program-config", data).status_code, (400, 422))
        self.assertEqual(ProgramConfig.objects.count(), 2)
        self.assertEqual(AuditLog.objects.filter(action="program_config.create").count(), 1)

    def scheduled_config(self):
        response = self.post("program-config", {
            "spend_per_point": "20.00",
            "effective_from": (timezone.now() + timedelta(days=4)).isoformat(),
        })
        self.assertEqual(response.status_code, 201)
        return ProgramConfig.objects.get(pk=response.data["id"])

    def test_delete_scheduled_config_preserves_history_and_version_numbers(self):
        scheduled = self.scheduled_config()
        self.assertTrue(self.get("program-config/history").data["results"][0]["can_delete"])
        path = self.root + f"program-config/{scheduled.pk}"
        self.assertEqual(self.client.delete(path).status_code, 204)
        self.assertEqual(self.client.delete(path).status_code, 204)
        self.assertEqual(ProgramConfigDeletion.objects.filter(config=scheduled).count(), 1)
        self.assertTrue(ProgramConfig.objects.filter(pk=scheduled.pk).exists())
        self.assertEqual(self.get("program-config/history").data["count"], 1)
        self.assertEqual(self.get("program-config").data["id"], self.config.pk)
        # A removed schedule never becomes effective, even after its former date.
        self.assertEqual(ProgramConfig.get_effective(self.shop, scheduled.effective_from + timedelta(days=1)).pk,
                         self.config.pk)
        log = AuditLog.objects.get(action="program_config.delete")
        self.assertEqual(log.actor, "shopify_user:123")
        self.assertEqual(log.before["version"], scheduled.version)
        self.assertEqual(log.after, {"deleted": True})
        response = self.post("program-config", {"spend_per_point": "15.00"})
        self.assertEqual(response.data["version"], scheduled.version + 1)
        self.assertEqual(self.get("me").data["program_config"]["spend_per_point"], "15.00")

    def test_delete_config_rejects_current_past_and_exact_effective_boundary(self):
        path = self.root + f"program-config/{self.config.pk}"
        response = self.client.delete(path)
        self.assertEqual((response.status_code, response.data["code"]), (409, "CONFIG_ALREADY_EFFECTIVE"))
        self.assertFalse(self.get("program-config/history").data["results"][0]["can_delete"])
        scheduled = self.scheduled_config()
        with patch("loyalty.services.administration.resolve_now", return_value=scheduled.effective_from):
            response = self.client.delete(self.root + f"program-config/{scheduled.pk}")
        self.assertEqual((response.status_code, response.data["code"]), (409, "CONFIG_ALREADY_EFFECTIVE"))
        self.assertFalse(ProgramConfigDeletion.objects.exists())
        self.assertFalse(AuditLog.objects.filter(action="program_config.delete").exists())

    def test_delete_config_is_shop_scoped_and_requires_session(self):
        other = Shop.objects.create(domain="other-config.myshopify.com", currency="USD")
        config = ProgramConfig.objects.create(shop=other, version=1,
                                             effective_from=timezone.now() + timedelta(days=1))
        for pk in (config.pk, config.pk + 1000):
            self.assertEqual(self.client.delete(self.root + f"program-config/{pk}").status_code, 404)
        self.client.credentials()
        self.assertEqual(self.client.delete(self.root + f"program-config/{config.pk}").status_code, 401)
        self.assertFalse(ProgramConfigDeletion.objects.exists())

    def test_delete_config_rejects_recorded_ledger_references(self):
        for key in ("program_config_id", "program_config_version", "config_version"):
            scheduled = self.scheduled_config()
            snapshot = {key: scheduled.pk if key == "program_config_id" else scheduled.version}
            PointsTransaction.objects.create(profile=self.profile, type="ADJUST", delta=1,
                balance_after=1, idempotency_key=str(snapshot), rule_snapshot=snapshot)
            response = self.client.delete(self.root + f"program-config/{scheduled.pk}")
            self.assertEqual((response.status_code, response.data["code"]), (409, "CONFIG_IN_USE"))
        self.assertFalse(ProgramConfigDeletion.objects.exists())

    def test_delete_config_tombstone_and_audit_are_atomic(self):
        scheduled = self.scheduled_config()
        with patch("loyalty.services.administration.audit", side_effect=RuntimeError("fixture failure")):
            response = self.client.delete(self.root + f"program-config/{scheduled.pk}")
        self.assertEqual(response.status_code, 500)
        self.assertFalse(ProgramConfigDeletion.objects.exists())
        self.assertEqual(self.get("program-config/history").data["count"], 2)

    def issued(self):
        self.lot(20)
        with patch("loyalty.services.redemption.find_discount_by_code", return_value=None), \
             patch("loyalty.services.redemption.create_discount_code", return_value="gid://shopify/DiscountCodeNode/1"):
            outcome = redeem_reward(self.shop, 301, self.reward.pk, "admin-test", self.now)
        self.assertEqual(outcome.status, "ISSUED")
        return Redemption.objects.get(pk=outcome.redemption_id)

    def test_redemption_list_masks_detail_and_cancel_refunds(self):
        row = self.issued()
        listing = self.get("redemptions", status="ISSUED").data["results"][0]
        self.assertNotIn("discount_code", listing)
        self.assertEqual(len(listing["code_last4"]), 4)
        self.assertIn("discount_code", self.get(f"redemptions/{row.pk}").data)
        with patch("loyalty.services.redemption_jobs.deactivate_discount"):
            response = self.post(f"redemptions/{row.pk}/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "CANCELLED")
        self.assertEqual(self.post(f"redemptions/{row.pk}/cancel").data["code"], "INVALID_STATUS")
        self.clean_ledger()

    def test_webhook_mask_retry_and_readonly_integrity(self):
        row = WebhookEvent.objects.create(shop=self.shop, shop_domain=self.shop.domain, webhook_id="http-retry",
            topic="customers/update", status="FAILED", attempts=5, error="provider details",
            payload={"email": "fixture@example.invalid", "customer": {"first_name": "Fixture", "phone": "000",
                "default_address": {"city": "Fixture"}}, "line_items": [{"name": "Fixture"}]})
        self.assertNotIn("payload", self.get("webhook-events").data["results"][0])
        payload = self.get(f"webhook-events/{row.pk}").data["payload"]
        self.assertEqual(payload["email"], "[MASKED]")
        self.assertEqual(payload["customer"]["default_address"], "[MASKED]")
        response = self.post(f"webhook-events/{row.pk}/retry")
        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        self.assertEqual((row.status, row.attempts, row.error), ("RECEIVED", 0, ""))
        self.assertEqual(self.post(f"webhook-events/{row.pk}/retry").status_code, 409)
        self.lot(3)
        before = (PointsTransaction.objects.count(), AuditLog.objects.count())
        response = self.get("integrity")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["problems"], [])
        self.assertEqual(before, (PointsTransaction.objects.count(), AuditLog.objects.count()))

    def test_integrity_limits_timeout_busy_and_warnings(self):
        self.lot(2, status="PENDING", available=self.now-timedelta(days=2))
        CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=999)
        response = self.get("integrity", limit=1)
        self.assertTrue(response.data["truncated"])
        self.assertEqual(response.data["scanned"], 1)
        self.assertIn("activation_overdue", response.data["warnings"][0]["codes"])
        with patch("loyalty.services.reporting.check_profile_integrity", side_effect=DatabaseError("private")):
            response = self.get("integrity")
        self.assertEqual((response.status_code, response.data["code"]), (503, "INTEGRITY_BUSY"))

    def test_shop_isolation(self):
        other_shop = Shop.objects.create(domain="other.myshopify.com", currency="USD")
        other = CustomerProfile.objects.create(shop=other_shop, shopify_customer_id=999)
        self.assertEqual(self.get(f"customers/{other.pk}").status_code, 404)
        self.assertEqual(self.get("customers").data["count"], 1)

    def test_json_errors_do_not_expose_provider_details(self):
        from shops.shopify import ShopifyError
        with patch("loyalty.services.reporting.dashboard", side_effect=ShopifyError("private")):
            response = self.get("dashboard")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(set(response.data), {"code", "message"})

    def test_unknown_route_json_and_integrity_total_timeout(self):
        self.assertEqual(self.get("unknown").data["code"], "NOT_FOUND")
        with patch("loyalty.services.reporting.time") as clock:
            clock.monotonic.side_effect = [0, 100]
            response = self.get("integrity")
        self.assertEqual((response.status_code, response.data["code"]), (503, "INTEGRITY_TIMEOUT"))
