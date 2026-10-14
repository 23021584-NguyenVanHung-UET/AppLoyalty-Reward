import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.db import IntegrityError, close_old_connections, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from loyalty.models import CustomerProfile, PointsLot, PointsTransaction, ProgramConfig
from loyalty.services.customers import upsert_customer_profile
from loyalty.services.earning import earn_points_for_order
from loyalty.services.integrity import check_profile_integrity
from shops.models import Shop
from shops.shopify import ShopifyError, ShopifyTransientError
from shops.tests.fixtures import raw_order, raw_refund
from webhooks.models import WebhookEvent
from webhooks.tasks import process_webhook


@override_settings(SHOPIFY_CLIENT_SECRET="test-secret")
class EarningTests(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(domain="test.myshopify.com", currency="VND")
        self.now = timezone.now()
        self.config = ProgramConfig.objects.create(shop=self.shop, version=1,
                                                  effective_from=self.now - timedelta(days=2))
        self.raw = raw_order()
        self.fetch = patch("loyalty.services.earning.fetch_order", side_effect=lambda _: deepcopy(self.raw)).start()
        patch("loyalty.services.earning.timezone.now", return_value=self.now).start()
        self.addCleanup(patch.stopall)

    def config_version(self, **kwargs):
        version = ProgramConfig.objects.filter(shop=self.shop).count() + 1
        return ProgramConfig.objects.create(shop=self.shop, version=version,
            effective_from=self.now - timedelta(hours=1) + timedelta(minutes=version), **kwargs)

    def event(self, topic="orders/paid", payload=None):
        return WebhookEvent.objects.create(shop=self.shop, shop_domain=self.shop.domain,
            topic=topic, webhook_id=f"delivery-{WebhookEvent.objects.count()}",
            payload={"id": 1001, "total_price": "999999999"} if payload is None else payload)

    def assert_integrity(self):
        for profile in CustomerProfile.objects.all():
            self.assertEqual(check_profile_integrity(profile), [])

    def test_pending_happy_path_and_snapshot_no_personal_data(self):
        event = self.event()
        process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual(event.status, WebhookEvent.Status.PROCESSED)
        lot, entry, profile = PointsLot.objects.get(), PointsTransaction.objects.get(), CustomerProfile.objects.get()
        self.assertEqual((lot.status, lot.points_total, lot.points_remaining), (PointsLot.Status.PENDING, 10, 10))
        self.assertEqual(lot.available_at, self.now + timedelta(days=7))
        self.assertEqual(lot.expires_at, self.now + timedelta(days=30))
        self.assertEqual((profile.available_balance, profile.pending_balance, profile.lifetime_earned), (0, 10, 10))
        self.assertEqual((entry.delta, entry.balance_after, entry.type), (10, 0, "EARN"))
        self.assertEqual(entry.idempotency_key, "earn:1001")
        self.assertTrue({"program_config_id", "program_config_version", "rules", "eligible_spend", "points",
                         "pending_days", "expiry_days", "breakdown", "shop_currency", "earned_at", "code"}
                        <= entry.rule_snapshot.keys())
        self.assertEqual(entry.rule_snapshot["program_config_version"], 1)
        self.assertEqual(entry.rule_snapshot["eligible_spend"], "100000")
        serialized = json.dumps(entry.rule_snapshot)
        for field in ("email", "firstName", "lastName", "customer_id", "total_price"):
            self.assertNotIn(field, serialized)
        self.assert_integrity()

    def test_available_uses_effective_newer_config(self):
        config = self.config_version(pending_days=0)
        self.assertEqual(earn_points_for_order(self.shop, 1001).status, "EARNED")
        lot, profile = PointsLot.objects.get(), CustomerProfile.objects.get()
        self.assertEqual(lot.status, PointsLot.Status.AVAILABLE)
        self.assertEqual(lot.available_at, self.now)
        self.assertEqual(profile.available_balance, 10)
        self.assertEqual(PointsTransaction.objects.get().rule_snapshot["program_config_id"], config.pk)
        self.assert_integrity()

    def test_no_expiry_when_zero(self):
        self.config_version(expiry_days=0)
        earn_points_for_order(self.shop, 1001)
        self.assertIsNone(PointsLot.objects.get().expires_at)
        self.assert_integrity()

    @override_settings(DEBUG=True, LOYALTY_ALLOW_TEST_ORDERS=True)
    def test_dev_test_order_earns_pending_once_and_snapshots_opt_in(self):
        self.raw["test"] = True
        self.assertEqual(earn_points_for_order(self.shop, 1001).status, "EARNED")
        self.assertEqual(earn_points_for_order(self.shop, 1001).status, "DUPLICATE")
        entry = PointsTransaction.objects.get()
        self.assertTrue(entry.rule_snapshot["rules"]["allow_test_orders"])
        self.assertEqual(PointsLot.objects.get().status, "PENDING")
        self.assertEqual(CustomerProfile.objects.get().pending_balance, 10)
        self.assert_integrity()

    def test_test_order_setting_requires_debug_and_explicit_opt_in(self):
        self.raw["test"] = True
        for debug, allow in [(False, True), (True, False), (False, False)]:
            with self.subTest(debug=debug, allow=allow), override_settings(DEBUG=debug, LOYALTY_ALLOW_TEST_ORDERS=allow):
                result = earn_points_for_order(self.shop, 1001)
                self.assertEqual((result.status, result.reason), ("SKIPPED", "Đơn hàng thử nghiệm"))
        self.assertFalse(PointsTransaction.objects.exists())

    def test_refund_of_dev_test_order_uses_original_opt_in_after_setting_is_off(self):
        from loyalty.services.revoking import revoke_points_for_refund, rules_from_earn
        self.raw["test"] = True
        with override_settings(DEBUG=True, LOYALTY_ALLOW_TEST_ORDERS=True):
            self.assertEqual(earn_points_for_order(self.shop, 1001).status, "EARNED")
        self.raw["displayFinancialStatus"] = "PARTIALLY_REFUNDED"
        self.raw["refunds"] = [raw_refund(subtotal="50000")]
        with override_settings(DEBUG=False, LOYALTY_ALLOW_TEST_ORDERS=False), \
                patch("loyalty.services.revoking.fetch_order", return_value=self.raw):
            result = revoke_points_for_refund(self.shop, 1001, 501)
        self.assertEqual(result.points, 5)
        self.assertEqual(CustomerProfile.objects.get().pending_balance, 5)
        # Existing immutable earning snapshots predate this optional rule field.
        old_entry = PointsTransaction.objects.get(type="EARN")
        old_entry.rule_snapshot["rules"].pop("allow_test_orders")
        self.assertFalse(rules_from_earn(old_entry).allow_test_orders)
        self.assert_integrity()

    def test_duplicate_order_and_replayed_event(self):
        event = self.event()
        process_webhook(event.pk)
        process_webhook(event.pk)
        second = self.event()
        process_webhook(second.pk)
        second.refresh_from_db()
        self.assertEqual(second.status, WebhookEvent.Status.PROCESSED)
        self.assertEqual(earn_points_for_order(self.shop, 1001).status, "DUPLICATE")
        self.assertEqual(PointsTransaction.objects.count(), 1)
        self.assertEqual(PointsLot.objects.count(), 1)
        self.assertEqual(CustomerProfile.objects.get().lifetime_earned, 10)
        self.assert_integrity()

    def test_skipped_order_table_has_no_ledger(self):
        scenarios = [({"customer": None}, "Đơn hàng không có khách hàng"),
                     ({"test": True}, "Đơn hàng thử nghiệm"),
                     ({"cancelledAt": self.now.isoformat()}, "Đơn hàng đã hủy")]
        for status in ("REFUNDED", "AUTHORIZED", "PENDING", "PARTIALLY_PAID"):
            scenarios.append(({"displayFinancialStatus": status}, "Trạng thái thanh toán không đủ điều kiện"))
        for changes, reason in scenarios:
            with self.subTest(changes=changes):
                self.raw = raw_order()
                self.raw.update(changes)
                event = self.event()
                process_webhook(event.pk)
                event.refresh_from_db()
                self.assertEqual(event.status, WebhookEvent.Status.SKIPPED)
                self.assertEqual(event.skip_reason, reason)
                self.assertFalse(PointsTransaction.objects.exists())
                self.assertFalse(PointsLot.objects.exists())
                self.assert_integrity()

    def test_no_config_and_disabled_program(self):
        other_shop = Shop.objects.create(domain="other.myshopify.com", currency="VND")
        event = self.event()
        event.shop = other_shop
        event.save()
        process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual(event.skip_reason, "Chưa có cấu hình chương trình")
        self.config_version(is_enabled=False)
        event = self.event()
        process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual(event.skip_reason, "Chương trình đang tắt")
        self.assertFalse(PointsTransaction.objects.exists())
        self.assert_integrity()

    def test_blocked_and_anonymized_profiles(self):
        profile, _ = upsert_customer_profile(self.shop, 301)
        for field, reason in [("is_blocked", "Khách bị chặn"), ("is_anonymized", "Hồ sơ khách đã ẩn danh")]:
            with self.subTest(field=field):
                profile.is_blocked = profile.is_anonymized = False
                setattr(profile, field, True)
                profile.save()
                event = self.event()
                process_webhook(event.pk)
                event.refresh_from_db()
                self.assertEqual(event.skip_reason, reason)
                self.assertEqual(event.status, WebhookEvent.Status.SKIPPED)
                self.assertFalse(PointsTransaction.objects.exists())
                self.assert_integrity()

    def test_auto_created_profile_later_customer_webhook_preserves_balances(self):
        earn_points_for_order(self.shop, 1001)
        event = self.event("customers/create", {"id": 301, "email": "customer@example.com", "first_name": "An"})
        process_webhook(event.pk)
        profile = CustomerProfile.objects.get()
        self.assertEqual(profile.pending_balance, 10)
        self.assertEqual(profile.lifetime_earned, 10)
        self.assertEqual(profile.first_name, "An")
        self.assertEqual(CustomerProfile.objects.count(), 1)
        self.assert_integrity()

    def setup_debt(self, debt):
        profile, _ = upsert_customer_profile(self.shop, 301)
        profile.available_balance = -debt
        profile.save(update_fields=["available_balance"])
        PointsTransaction.objects.create(profile=profile, type="ADJUST", delta=-debt, balance_after=-debt,
                                         idempotency_key="test-debt", actor="system")
        return profile

    def test_available_debt_offset_and_consumed_lot(self):
        self.config_version(pending_days=0)
        profile = self.setup_debt(5)
        earn_points_for_order(self.shop, 1001)
        profile.refresh_from_db()
        self.assertEqual(profile.available_balance, 5)
        self.assertEqual(PointsLot.objects.get().points_remaining, 5)
        self.assertEqual(PointsTransaction.objects.get(type="EARN").delta, 10)
        self.assert_integrity()

    def test_available_points_completely_consumed_by_debt(self):
        self.config_version(pending_days=0)
        profile = self.setup_debt(15)
        earn_points_for_order(self.shop, 1001)
        profile.refresh_from_db()
        lot = PointsLot.objects.get()
        self.assertEqual((lot.status, lot.points_remaining), (PointsLot.Status.CONSUMED, 0))
        self.assertEqual(profile.available_balance, -5)
        self.assert_integrity()

    def test_pending_points_do_not_offset_debt(self):
        profile = self.setup_debt(5)
        earn_points_for_order(self.shop, 1001)
        profile.refresh_from_db()
        self.assertEqual((profile.available_balance, profile.pending_balance), (-5, 10))
        self.assertEqual(PointsLot.objects.get().points_remaining, 10)
        self.assert_integrity()

    def test_missing_order_fails_then_retry_earns(self):
        self.fetch.side_effect = None
        self.fetch.return_value = None
        event = self.event()
        process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual(event.status, WebhookEvent.Status.FAILED)
        self.assertFalse(PointsTransaction.objects.exists())
        self.fetch.return_value = raw_order()
        process_webhook(event.pk)
        process_webhook(event.pk)
        self.assertEqual(PointsTransaction.objects.count(), 1)
        self.assert_integrity()

    def test_transient_failure_then_retry(self):
        self.fetch.side_effect = ShopifyTransientError("Shopify tạm thời bị lỗi")
        event = self.event()
        process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual(event.status, WebhookEvent.Status.FAILED)
        self.fetch.side_effect = lambda _: raw_order()
        process_webhook(event.pk)
        self.assertEqual(PointsTransaction.objects.count(), 1)
        self.assert_integrity()

    def test_config_at_processing_not_receipt(self):
        event = self.event()
        config = self.config_version(spend_per_point="20000")
        process_webhook(event.pk)
        entry = PointsTransaction.objects.get()
        self.assertEqual(entry.delta, 5)
        self.assertEqual(entry.rule_snapshot["program_config_version"], config.version)
        self.assert_integrity()

    def test_currency_mismatch_fails_without_ledger(self):
        self.raw["currencyCode"] = "USD"
        with self.assertRaises(ShopifyError):
            earn_points_for_order(self.shop, 1001)
        self.assertFalse(PointsTransaction.objects.exists())

    def test_missing_order_id_fails(self):
        event = self.event(payload={})
        process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual(event.status, WebhookEvent.Status.FAILED)
        self.fetch.assert_not_called()

    def test_integrity_detects_corrupted_cache(self):
        earn_points_for_order(self.shop, 1001)
        profile = CustomerProfile.objects.get()
        CustomerProfile.objects.filter(pk=profile.pk).update(pending_balance=999)
        self.assertIn("ledger_balance_mismatch", check_profile_integrity(profile))
        self.assertIn("pending_lots_mismatch", check_profile_integrity(profile))

    def test_transaction_rolls_back_lot_and_balances_on_insert_failure(self):
        with patch("loyalty.services.earning.PointsTransaction.objects.create", side_effect=IntegrityError):
            with self.assertRaises(IntegrityError):
                earn_points_for_order(self.shop, 1001)
        self.assertFalse(PointsLot.objects.exists())
        profile = CustomerProfile.objects.get()
        self.assertEqual((profile.available_balance, profile.pending_balance, profile.lifetime_earned), (0, 0, 0))
        self.assert_integrity()

    def test_partial_refund_earns_net_points(self):
        from shops.tests.fixtures import connection, money
        self.raw["displayFinancialStatus"] = "PARTIALLY_REFUNDED"
        self.raw["refunds"] = [{"refundLineItems": connection([{
            "lineItem": {"id": "gid://shopify/LineItem/101"}, "quantity": 1,
            "subtotalSet": money("30000"), "totalTaxSet": money("0"),
        }])}]
        self.assertEqual(earn_points_for_order(self.shop, 1001).points, 7)
        self.assert_integrity()

    def test_money_only_refund_does_not_reduce_eligible_line_spend(self):
        from shops.tests.fixtures import connection
        self.raw["displayFinancialStatus"] = "PARTIALLY_REFUNDED"
        self.raw["refunds"] = [{"refundLineItems": connection([]), "refundShippingLines": connection([])}]
        event = self.event()
        process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual(event.status, WebhookEvent.Status.PROCESSED)
        self.assertEqual(PointsTransaction.objects.get().delta, 10)
        self.assert_integrity()


class ConcurrentEarningTests(TransactionTestCase):
    def test_two_workers_earn_one_order_once(self):
        shop = Shop.objects.create(domain="test.myshopify.com", currency="VND")
        ProgramConfig.objects.create(shop=shop, version=1, effective_from=timezone.now() - timedelta(days=1))
        barrier = Barrier(2)
        def fetch(_):
            barrier.wait(timeout=10)
            return raw_order()
        def earn(_):
            close_old_connections()
            try:
                return earn_points_for_order(Shop.objects.get(pk=shop.pk), 1001).status
            finally:
                connections.close_all()
        with patch("loyalty.services.earning.fetch_order", side_effect=fetch), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(earn, range(2)))
        self.assertCountEqual(results, ["EARNED", "DUPLICATE"])
        self.assertEqual(PointsTransaction.objects.count(), 1)
        self.assertEqual(PointsLot.objects.count(), 1)
        self.assertEqual(CustomerProfile.objects.count(), 1)
        self.assertEqual(check_profile_integrity(CustomerProfile.objects.get()), [])
