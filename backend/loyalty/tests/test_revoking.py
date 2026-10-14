import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from io import StringIO
from threading import Barrier
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.db.models import Sum
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from loyalty.models import AuditLog, CustomerProfile, PointsLot, PointsTransaction, ProgramConfig
from loyalty.services.earning import earn_points_for_order
from loyalty.services.integrity import check_profile_integrity
from loyalty.services.revoking import (UNRECOVERED_ACTION, revoke_points_for_cancel,
                                      revoke_points_for_refund)
from shops.models import Shop
from shops.shopify import ShopifyError, ShopifyTransientError
from shops.tests.fixtures import connection as page, money, order_line, raw_order, raw_refund
from webhooks.models import WebhookEvent
from webhooks.tasks import process_webhook


class RevokeTestSetup:
    def setUp(self):
        self.shop = Shop.objects.create(domain="test.myshopify.com", currency="VND")
        self.now = timezone.now()
        self.config = ProgramConfig.objects.create(shop=self.shop, version=1,
            effective_from=self.now - timedelta(days=1))
        self.raw = raw_order()
        self.fetch = patch("loyalty.services.revoking.fetch_order", side_effect=lambda _: deepcopy(self.raw)).start()
        self.addCleanup(patch.stopall)

    def config_version(self, **changes):
        current = ProgramConfig.get_effective(self.shop)
        fields = {field.attname: getattr(current, field.attname) for field in ProgramConfig._meta.concrete_fields
                  if field.name not in ("id", "created_at", "updated_at", "version", "effective_from")}
        version = ProgramConfig.objects.filter(shop=self.shop).count() + 1
        fields.update(changes, version=version, effective_from=self.now - timedelta(minutes=5) + timedelta(seconds=version))
        return ProgramConfig.objects.create(**fields)

    def earn(self, number=1001, raw=None):
        snapshot = deepcopy(raw if raw is not None else raw_order(number))
        with patch("loyalty.services.earning.fetch_order", return_value=snapshot):
            outcome = earn_points_for_order(self.shop, number)
        self.assertEqual(outcome.status, "EARNED")
        self.profile = CustomerProfile.objects.get(shop=self.shop, shopify_customer_id=301)
        return PointsLot.objects.get(profile=self.profile, source_order_id=number)

    def cancel(self):
        self.raw["cancelledAt"] = self.now.isoformat()

    def refund(self, *refunds, status="PARTIALLY_REFUNDED"):
        self.raw["displayFinancialStatus"] = status
        self.raw["refunds"] = list(refunds)

    def event(self, topic="orders/cancelled", payload=None, **changes):
        values = dict(shop=self.shop, shop_domain=self.shop.domain, topic=topic,
                      webhook_id=f"delivery-{WebhookEvent.objects.count()}",
                      payload={"id": 1001} if payload is None else payload)
        values.update(changes)
        return WebhookEvent.objects.create(**values)

    def assert_integrity(self):
        for profile in CustomerProfile.objects.all():
            self.assertEqual(check_profile_integrity(profile), [])
        call_command("check_ledger", stdout=StringIO())

    def spend(self, lot, points, expired=False):
        # Fixture history only: no redemption or expiry service is implemented here.
        self.profile.refresh_from_db()
        lot.points_remaining -= points
        if lot.points_remaining == 0:
            lot.status = "EXPIRED" if expired else "CONSUMED"
        lot.save(update_fields=["points_remaining", "status"])
        self.profile.available_balance -= points
        self.profile.save(update_fields=["available_balance"])
        PointsTransaction.objects.create(profile=self.profile, type="EXPIRE" if expired else "REDEEM",
            delta=-points, balance_after=self.profile.available_balance, lot=lot,
            idempotency_key=f"fixture-spent:{lot.pk}:{points}")
        self.assert_integrity()


@override_settings(SHOPIFY_CLIENT_SECRET="test-secret")
class RevokingTests(RevokeTestSetup, TestCase):
    def test_cancel_pending_voids_lot_and_records_snapshot(self):
        lot = self.earn()
        self.cancel()
        event = self.event(payload={"id": 1001, "total_price": "999999999", "customer": {"id": 999}})
        process_webhook(event.pk)
        event.refresh_from_db()
        lot.refresh_from_db()
        self.profile.refresh_from_db()
        entry = PointsTransaction.objects.get(type="REVOKE")
        self.assertEqual(event.status, "PROCESSED")
        self.assertEqual((lot.status, lot.points_remaining), ("VOIDED", 0))
        self.assertEqual((self.profile.available_balance, self.profile.pending_balance, self.profile.lifetime_earned), (0, 0, 0))
        self.assertEqual((entry.delta, entry.balance_after, entry.lot_id, entry.actor, entry.order_id), (-10, 0, None, "system", 1001))
        self.assertEqual(entry.idempotency_key, "revoke:order:1001")
        self.assertIsNone(entry.refund_id)
        snapshot = entry.rule_snapshot
        self.assertTrue({"code", "program_config_id", "program_config_version", "rules", "earned_points",
            "already_revoked", "target", "revoke_points", "breakdown", "lot_allocations", "debt_created",
            "unrecovered_points", "shop_currency", "processed_at"} <= snapshot.keys())
        self.assertEqual(snapshot["code"], "revoke_cancel")
        self.assertEqual(snapshot["skip_code"], "cancelled_order")
        self.assertEqual(snapshot["lot_allocations"], [{"lot_id": lot.pk, "points": 10, "lot_status_before": "PENDING"}])
        for field in ("customer_id", "email", "first_name", "last_name", "total_price"):
            self.assertNotIn(field, json.dumps(snapshot))
        self.assert_integrity()

    def test_cancel_available_and_duplicate_deliveries(self):
        self.config_version(pending_days=0)
        lot = self.earn()
        self.cancel()
        event = self.event()
        process_webhook(event.pk)
        process_webhook(event.pk)
        second = self.event()
        process_webhook(second.pk)
        second.refresh_from_db()
        self.assertEqual(second.status, "PROCESSED")
        self.assertEqual(revoke_points_for_cancel(self.shop, 1001).status, "DUPLICATE")
        self.assertEqual(PointsTransaction.objects.filter(type="REVOKE").count(), 1)
        lot.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual((lot.status, lot.points_remaining, self.profile.available_balance), ("VOIDED", 0, 0))
        self.assert_integrity()

    def test_not_cancelled_is_skipped(self):
        self.earn()
        event = self.event()
        process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual((event.status, event.skip_reason), ("SKIPPED", "Đơn chưa bị hủy"))
        self.assertFalse(PointsTransaction.objects.filter(type="REVOKE").exists())
        self.assert_integrity()

    def test_partial_refund_floor_recomputed_and_duplicate(self):
        self.earn()
        self.refund(raw_refund())
        event = self.event("refunds/create", {"id": 501, "order_id": 1001, "amount": "999999999"})
        process_webhook(event.pk)
        entry = PointsTransaction.objects.get(type="REVOKE")
        self.assertEqual((entry.delta, entry.refund_id, entry.idempotency_key), (-4, 501, "revoke:refund:501"))
        self.assertEqual(entry.rule_snapshot["target"], 6)
        self.assertEqual(entry.rule_snapshot["breakdown"]["eligible_spend"], "67000")
        self.assertEqual(revoke_points_for_refund(self.shop, 1001, 501).status, "DUPLICATE")
        second = self.event("refunds/create", {"id": 501, "order_id": 1001})
        process_webhook(second.pk)
        second.refresh_from_db()
        self.assertEqual(second.status, "PROCESSED")
        self.assertEqual(PointsTransaction.objects.filter(type="REVOKE").count(), 1)
        self.assert_integrity()

    def test_two_refunds_in_either_delivery_order_converge(self):
        for number, deliveries in [(1001, (501, 502)), (1002, (504, 503))]:
            self.earn(number)
            self.raw = raw_order(number)
            first_id = 501 if number == 1001 else 503
            self.refund(raw_refund(first_id, "33000"), raw_refund(first_id + 1, "12000"))
            outcomes = [revoke_points_for_refund(self.shop, number, refund_id) for refund_id in deliveries]
            self.assertEqual(sum(outcome.points for outcome in outcomes), 5)
            total = PointsTransaction.objects.filter(order_id=number, type__in=["EARN", "REVOKE"]).aggregate(n=Sum("delta"))["n"]
            self.assertEqual(total, 5)
            self.assert_integrity()

    def test_sequential_refunds_and_cancel_only_revoke_remaining(self):
        lot = self.earn()
        self.refund(raw_refund(501, "33000"))
        self.assertEqual(revoke_points_for_refund(self.shop, 1001, 501).points, 4)
        self.refund(raw_refund(501, "33000"), raw_refund(502, "12000"))
        self.assertEqual(revoke_points_for_refund(self.shop, 1001, 502).points, 1)
        self.cancel()
        self.assertEqual(revoke_points_for_cancel(self.shop, 1001).points, 5)
        lot.refresh_from_db()
        self.assertEqual((lot.status, lot.points_remaining), ("VOIDED", 0))
        self.assertEqual(PointsTransaction.objects.filter(type="REVOKE").aggregate(n=Sum("delta"))["n"], -10)
        self.assert_integrity()

    def test_full_refund_target_zero_with_skip_code(self):
        self.earn()
        self.refund(raw_refund(subtotal="100000"), status="REFUNDED")
        self.assertEqual(revoke_points_for_refund(self.shop, 1001, 501).points, 10)
        snapshot = PointsTransaction.objects.get(type="REVOKE").rule_snapshot
        self.assertEqual((snapshot["target"], snapshot["skip_code"]), (0, "financial_status_ineligible"))
        self.assert_integrity()

    def test_any_compute_skip_zeroes_target(self):
        self.earn()
        self.refund(raw_refund(subtotal="100000"))
        self.assertEqual(revoke_points_for_refund(self.shop, 1001, 501).points, 10)
        self.assertEqual(PointsTransaction.objects.get(type="REVOKE").rule_snapshot["skip_code"], "zero_points")
        self.assert_integrity()

    def test_manual_adjustment_is_noop_even_when_payment_fully_refunded(self):
        self.earn()
        self.refund(raw_refund(subtotal=None), status="REFUNDED")
        outcome = revoke_points_for_refund(self.shop, 1001, 501)
        self.assertEqual((outcome.status, outcome.code), ("NOOP", "no_eligible_refund"))
        event = self.event("refunds/create", {"id": 501, "order_id": 1001})
        process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual(event.status, "SKIPPED")
        self.assertEqual(event.skip_reason, "Hoàn tiền không làm giảm tiền hàng hợp lệ")
        self.assertFalse(PointsTransaction.objects.filter(type="REVOKE").exists())
        self.assert_integrity()

    def test_shipping_only_noop_when_excluded(self):
        self.earn()
        self.refund(raw_refund(subtotal=None, shipping="20000"))
        self.assertEqual(revoke_points_for_refund(self.shop, 1001, 501).code, "no_eligible_refund")
        self.assertFalse(PointsTransaction.objects.filter(type="REVOKE").exists())
        self.assert_integrity()

    def test_shipping_uses_earn_rules_and_is_capped(self):
        self.config_version(include_shipping=True)
        self.earn()
        self.config_version(include_shipping=False)
        self.refund(raw_refund(subtotal=None, shipping="9999999"))
        outcome = revoke_points_for_refund(self.shop, 1001, 501)
        self.assertEqual(outcome.points, 2)
        snapshot = PointsTransaction.objects.get(type="REVOKE").rule_snapshot
        self.assertEqual(snapshot["earned_points"], 12)
        self.assertEqual(snapshot["target"], 10)
        self.assertEqual(snapshot["breakdown"]["shipping_amount"], "0")
        self.assertTrue(snapshot["rules"]["include_shipping"])
        self.assert_integrity()

    def test_tax_inclusive_refund_uses_normalized_subtotal(self):
        self.raw["taxesIncluded"] = True
        self.raw["lineItems"] = page([order_line(total="110000", tax="10000")])
        self.earn(raw=self.raw)
        self.refund(raw_refund(subtotal="36300", tax="3300"))
        self.assertEqual(revoke_points_for_refund(self.shop, 1001, 501).points, 4)
        snapshot = PointsTransaction.objects.get(type="REVOKE").rule_snapshot
        self.assertEqual((snapshot["breakdown"]["refund_deduction"], snapshot["target"]), ("33000", 6))
        self.assert_integrity()

    def test_new_config_cannot_cause_phantom_revoke(self):
        self.earn()
        current = self.config_version(spend_per_point="20000", is_enabled=False)
        self.refund(raw_refund(subtotal="0"))
        self.assertEqual(revoke_points_for_refund(self.shop, 1001, 501).status, "NOOP")
        self.refund(raw_refund(subtotal="33000"))
        self.assertEqual(revoke_points_for_refund(self.shop, 1001, 501).points, 4)
        snapshot = PointsTransaction.objects.get(type="REVOKE").rule_snapshot
        self.assertEqual(snapshot["program_config_id"], current.pk)
        self.assertEqual(snapshot["rules"]["spend_per_point"], "10000.00")
        self.assert_integrity()

    def test_reversed_refund_does_not_recredit(self):
        self.earn()
        self.refund(raw_refund(subtotal="33000"))
        revoke_points_for_refund(self.shop, 1001, 501)
        self.refund(raw_refund(502, "10000"))
        self.assertEqual(revoke_points_for_refund(self.shop, 1001, 502).status, "NOOP")
        self.assertEqual(PointsTransaction.objects.filter(type="REVOKE").count(), 1)
        self.assert_integrity()

    def test_missing_ids_fail_without_calling_api(self):
        for topic, payload in [("orders/cancelled", {}), ("refunds/create", {"id": 501}),
                               ("refunds/create", {"order_id": 1001})]:
            with self.subTest(topic=topic, payload=payload):
                event = self.event(topic, payload)
                process_webhook(event.pk)
                event.refresh_from_db()
                self.assertEqual(event.status, "FAILED")
                self.assertIn("Webhook thiếu mã", event.error)
        self.fetch.assert_not_called()

    def test_no_earn_pending_paid_states_fail_retryably(self):
        self.cancel()
        for status, attempts in [("RECEIVED", 0), ("PROCESSING", 1), ("FAILED", 4)]:
            with self.subTest(status=status):
                paid = self.event("orders/paid", {"id": "1001"}, status=status, attempts=attempts)
                event = self.event()
                process_webhook(event.pk)
                event.refresh_from_db()
                self.assertEqual(event.status, "FAILED")
                self.assertIn("Đang chờ", event.error)
                paid.status = "SKIPPED"
                paid.save(update_fields=["status"])
        self.assertFalse(PointsTransaction.objects.exists())
        self.assert_integrity()

    def test_no_earn_terminal_or_exhausted_paid_or_different_order_skips(self):
        self.cancel()
        for status, attempts, order in [("PROCESSED", 1, 1001), ("SKIPPED", 1, 1001),
                                       ("FAILED", 5, 1001), ("RECEIVED", 0, 999)]:
            with self.subTest(status=status, order=order):
                paid = self.event("orders/paid", {"id": order}, status=status, attempts=attempts)
                outcome = revoke_points_for_cancel(self.shop, 1001)
                self.assertEqual((outcome.status, outcome.reason), ("SKIPPED", "Đơn chưa được tích điểm"))
                paid.status = "SKIPPED"
                paid.save(update_fields=["status"])
        self.assert_integrity()

    def test_no_earn_no_paid_event_skips(self):
        self.cancel()
        event = self.event()
        process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual((event.status, event.skip_reason), ("SKIPPED", "Đơn chưa được tích điểm"))
        self.assert_integrity()

    def test_retry_after_inflight_earn_commits_revokes_once(self):
        paid = self.event("orders/paid", {"id": 1001}, status="PROCESSING")
        self.refund(raw_refund())
        event = self.event("refunds/create", {"id": 501, "order_id": 1001})
        process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual(event.status, "FAILED")
        # The paid worker fetched before the refund and commits its earn afterwards.
        self.earn()
        paid.status = "PROCESSED"
        paid.save(update_fields=["status"])
        process_webhook(event.pk)
        process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual(event.status, "PROCESSED")
        self.assertEqual(PointsTransaction.objects.get(type="REVOKE").delta, -4)
        self.assert_integrity()

    def test_used_points_take_other_available_fefo_then_debt_and_later_earn_offsets(self):
        self.config_version(pending_days=0)
        own = self.earn()
        self.spend(own, 10)
        other_lots = []
        for number, total, expiry in [(2001, "20000", None), (2002, "10000", 3), (2003, "20000", 1)]:
            raw = raw_order(number)
            raw["lineItems"] = page([order_line(total=total)])
            lot = self.earn(number, raw)
            lot.expires_at = self.now + timedelta(days=expiry) if expiry else None
            lot.save(update_fields=["expires_at"])
            other_lots.append(lot)
        self.cancel()
        outcome = revoke_points_for_cancel(self.shop, 1001)
        self.assertEqual(outcome.points, 10)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.available_balance, -5)
        self.assertEqual(self.profile.lifetime_earned, 15)
        snapshot = PointsTransaction.objects.get(type="REVOKE").rule_snapshot
        self.assertEqual([a["lot_id"] for a in snapshot["lot_allocations"]],
                         [other_lots[2].pk, other_lots[1].pk, other_lots[0].pk])
        self.assertEqual(snapshot["debt_created"], 5)
        self.assertFalse(PointsLot.objects.filter(status="AVAILABLE", points_remaining__gt=0).exists())
        self.assert_integrity()
        later = self.earn(3001)
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.available_balance, later.points_remaining), (5, 5))
        self.assert_integrity()

    def test_expired_points_create_debt(self):
        self.config_version(pending_days=0)
        lot = self.earn()
        self.spend(lot, 10, expired=True)
        self.cancel()
        self.assertEqual(revoke_points_for_cancel(self.shop, 1001).points, 10)
        lot.refresh_from_db()
        self.assertEqual((lot.status, lot.points_remaining), ("EXPIRED", 0))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.available_balance, -10)
        self.assert_integrity()

    def test_partly_consumed_own_lot_ends_consumed(self):
        self.config_version(pending_days=0)
        lot = self.earn()
        self.spend(lot, 6)
        self.cancel()
        revoke_points_for_cancel(self.shop, 1001)
        lot.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual((lot.status, self.profile.available_balance, self.profile.lifetime_earned), ("CONSUMED", -6, 6))
        self.assert_integrity()

    def test_no_negative_recovers_three_waives_seven_and_never_autocollects(self):
        self.config_version(pending_days=0, allow_negative_balance=False)
        lot = self.earn()
        self.spend(lot, 7)
        self.cancel()
        with self.assertLogs("loyalty.services.revoking", level="WARNING") as logs:
            outcome = revoke_points_for_cancel(self.shop, 1001)
        self.assertEqual(outcome.points, 3)
        entry = PointsTransaction.objects.get(type="REVOKE")
        self.assertEqual((entry.delta, entry.rule_snapshot["unrecovered_points"]), (-3, 7))
        self.assertIn("unrecovered_points=7", " ".join(logs.output))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.available_balance, 0)
        self.assert_integrity()
        self.earn(2001)
        self.refund(raw_refund(subtotal="100000"), status="REFUNDED")
        self.assertEqual(revoke_points_for_refund(self.shop, 1001, 501).status, "NOOP")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.available_balance, 10)
        self.assert_integrity()

    def test_zero_recovery_has_audit_history_no_zero_ledger_and_duplicate(self):
        self.config_version(pending_days=0, allow_negative_balance=False)
        lot = self.earn()
        self.spend(lot, 10)
        self.cancel()
        outcome = revoke_points_for_cancel(self.shop, 1001)
        self.assertEqual((outcome.status, outcome.code), ("NOOP", "unrecovered"))
        self.assertFalse(PointsTransaction.objects.filter(type="REVOKE").exists())
        audit = AuditLog.objects.get(action=UNRECOVERED_ACTION)
        self.assertEqual(audit.after["unrecovered_points"], 10)
        self.assertEqual(revoke_points_for_cancel(self.shop, 1001).status, "DUPLICATE")
        self.earn(2001)
        self.refund(raw_refund(subtotal="100000"), status="REFUNDED")
        self.assertEqual(revoke_points_for_refund(self.shop, 1001, 501).status, "NOOP")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.available_balance, 10)
        self.assert_integrity()

    def test_blocked_anonymized_profile_still_revoked(self):
        self.earn()
        CustomerProfile.objects.filter(pk=self.profile.pk).update(is_blocked=True, is_anonymized=True)
        self.cancel()
        self.assertEqual(revoke_points_for_cancel(self.shop, 1001).points, 10)
        self.assert_integrity()

    def test_debt_warning_contains_only_ids_and_balance(self):
        self.config_version(pending_days=0, negative_balance_alert_threshold=-5)
        lot = self.earn()
        self.profile.email = "private@example.com"
        self.profile.save(update_fields=["email"])
        self.spend(lot, 10)
        self.cancel()
        with self.assertLogs("loyalty.services.revoking", level="WARNING") as logs:
            revoke_points_for_cancel(self.shop, 1001)
        output = " ".join(logs.output)
        self.assertIn(f"profile_id={self.profile.pk}", output)
        self.assertIn("available_balance=-10", output)
        self.assertNotIn("private@example.com", output)
        self.assert_integrity()

    def test_missing_and_transient_api_fail_then_successful_retry(self):
        self.earn()
        self.cancel()
        for response in (None, ShopifyTransientError("Shopify tạm thời bị lỗi")):
            with self.subTest(response=type(response).__name__):
                self.fetch.side_effect = (lambda _: None) if response is None else response
                event = self.event()
                process_webhook(event.pk)
                event.refresh_from_db()
                self.assertEqual(event.status, "FAILED")
                self.assertFalse(PointsTransaction.objects.filter(type="REVOKE").exists())
        self.fetch.side_effect = lambda _: deepcopy(self.raw)
        process_webhook(event.pk)
        process_webhook(event.pk)
        self.assertEqual(PointsTransaction.objects.filter(type="REVOKE").count(), 1)
        self.assert_integrity()

    def test_currency_mismatch_and_wrong_order_rejected(self):
        self.earn()
        self.cancel()
        self.shop.currency = "USD"
        with self.assertRaisesMessage(ShopifyError, "Tiền tệ đơn hàng"):
            revoke_points_for_cancel(self.shop, 1001)
        self.shop.currency = "VND"
        self.raw["id"] = "gid://shopify/Order/2001"
        with self.assertRaisesMessage(ShopifyError, "Mã đơn hàng"):
            revoke_points_for_cancel(self.shop, 1001)
        self.assertFalse(PointsTransaction.objects.filter(type="REVOKE").exists())
        self.assert_integrity()

    def test_refund_not_visible_yet_retries(self):
        self.earn()
        event = self.event("refunds/create", {"id": 501, "order_id": 1001})
        process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual(event.status, "FAILED")
        self.refund(raw_refund())
        process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual(event.status, "PROCESSED")
        self.assert_integrity()

    def test_original_exclusions_and_multiplier_are_rebuilt(self):
        from types import SimpleNamespace
        from loyalty.services.revoking import rules_from_earn
        saved = {"spend_per_point": "10.00", "include_tax": True, "include_shipping": True,
                 "excluded_product_ids": [201], "excluded_collection_ids": [301],
                 "excluded_channels": ["pos"], "tier_multiplier": 2}
        rules = rules_from_earn(SimpleNamespace(rule_snapshot={"rules": saved}))
        self.assertEqual(str(rules.spend_per_point), "10.00")
        self.assertEqual((rules.include_tax, rules.include_shipping, rules.tier_multiplier), (True, True, 2))
        self.assertEqual((rules.excluded_product_ids, rules.excluded_collection_ids, rules.excluded_channels),
                         ([201], [301], ["pos"]))
        with self.assertRaisesMessage(ShopifyError, "Bản chụp quy tắc"):
            rules_from_earn(SimpleNamespace(rule_snapshot={"rules": {}}))

    @patch("webhooks.tasks.MAX_ATTEMPTS", 2)
    def test_paid_retry_guard_uses_configured_limit_and_shop(self):
        self.cancel()
        self.event("orders/paid", {"id": 1001}, status="FAILED", attempts=2)
        other = Shop.objects.create(domain="other.myshopify.com", currency="VND")
        self.event("orders/paid", {"id": 1001}, shop=other)
        self.assertEqual(revoke_points_for_cancel(self.shop, 1001).status, "SKIPPED")
        self.event("orders/paid", {"id": "gid://shopify/Order/1001"}, status="FAILED", attempts=1)
        with self.assertRaises(ShopifyTransientError):
            revoke_points_for_cancel(self.shop, 1001)
        self.assert_integrity()

    def test_write_failure_rolls_back_balances_and_lots(self):
        lot = self.earn()
        self.cancel()
        with patch("loyalty.services.revoking.PointsTransaction.objects.create", side_effect=IntegrityError):
            with self.assertRaises(IntegrityError):
                revoke_points_for_cancel(self.shop, 1001)
        lot.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual((lot.points_remaining, self.profile.pending_balance), (10, 10))
        self.assert_integrity()

    def test_expected_unique_race_returns_duplicate_after_rollback(self):
        lot = self.earn()
        self.cancel()
        with patch("loyalty.services.revoking.PointsTransaction.objects.create", side_effect=IntegrityError), \
                patch("loyalty.services.revoking.already_processed", side_effect=[False, True]):
            self.assertEqual(revoke_points_for_cancel(self.shop, 1001).status, "DUPLICATE")
        lot.refresh_from_db()
        self.assertEqual(lot.points_remaining, 10)
        self.assert_integrity()

    def test_integrity_detects_over_revoked_order(self):
        self.earn()
        self.profile.available_balance = -11
        self.profile.save(update_fields=["available_balance"])
        PointsTransaction.objects.create(profile=self.profile, type="REVOKE", order_id=1001,
            delta=-11, balance_after=-11, idempotency_key="fixture-over-revoke")
        self.assertIn("order_over_revoked", check_profile_integrity(self.profile))
        with self.assertRaises(CommandError) as error:
            call_command("check_ledger", stdout=StringIO())
        self.assertEqual(error.exception.returncode, 1)

    def test_integrity_detects_lot_exceeding_total_on_legacy_corruption(self):
        lot = self.earn()
        # Transactional DDL stays in the test database and is rolled back at this savepoint.
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
                cursor.execute('ALTER TABLE loyalty_pointslot DROP CONSTRAINT loyalty_lot_remaining_lte_total')
                cursor.execute('UPDATE loyalty_pointslot SET points_remaining = points_total + 1 WHERE id = %s', [lot.pk])
            self.assertIn("lot_remaining_exceeds_total", check_profile_integrity(self.profile))
            transaction.set_rollback(True)
        self.assert_integrity()


class ConcurrentRevokingTests(RevokeTestSetup, TransactionTestCase):
    def run_race(self, operations, raw):
        barrier = Barrier(2)
        def fetch(_):
            self.assertFalse(connection.in_atomic_block)
            barrier.wait(timeout=15)
            return deepcopy(raw)
        def execute(operation):
            close_old_connections()
            try:
                shop = Shop.objects.get(pk=self.shop.pk)
                if operation is None:
                    return revoke_points_for_cancel(shop, 1001)
                return revoke_points_for_refund(shop, 1001, operation)
            finally:
                connections.close_all()
        with patch("loyalty.services.revoking.fetch_order", side_effect=fetch), ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(execute, operations))

    def test_cancel_and_refund_race_does_not_over_revoke(self):
        self.earn()
        self.cancel()
        self.refund(raw_refund(subtotal="33000"))
        outcomes = self.run_race([None, 501], self.raw)
        self.assertEqual(sum(outcome.points for outcome in outcomes), 10)
        self.assertEqual(PointsTransaction.objects.filter(type="REVOKE").aggregate(n=Sum("delta"))["n"], -10)
        self.assert_integrity()

    def test_two_refunds_race_recompute_under_profile_lock(self):
        self.earn()
        self.refund(raw_refund(501, "33000"), raw_refund(502, "12000"))
        outcomes = self.run_race([501, 502], self.raw)
        self.assertEqual(sum(outcome.points for outcome in outcomes), 5)
        self.assertEqual(PointsTransaction.objects.filter(type="REVOKE").aggregate(n=Sum("delta"))["n"], -5)
        self.assert_integrity()

    def test_duplicate_cancel_race_is_processed_once(self):
        self.earn()
        self.cancel()
        outcomes = self.run_race([None, None], self.raw)
        self.assertCountEqual([outcome.status for outcome in outcomes], ["REVOKED", "DUPLICATE"])
        self.assertEqual(PointsTransaction.objects.filter(type="REVOKE").count(), 1)
        self.assert_integrity()
