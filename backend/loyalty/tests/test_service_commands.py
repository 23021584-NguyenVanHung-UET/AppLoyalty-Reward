from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from loyalty.models import CustomerProfile, PointsLot, PointsTransaction
from shops.models import Shop
from shops.shopify import ShopifyTransientError
from webhooks.models import WebhookEvent


@override_settings(SHOPIFY_SHOP_DOMAIN="test.myshopify.com")
class SyncCommandTests(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(domain="test.myshopify.com", currency="VND")
        self.stdout = StringIO()

    def customer(self, number, **kwargs):
        return {"id": f"gid://shopify/Customer/{number}", "email": None,
                "firstName": None, "lastName": None, "state": "DISABLED", **kwargs}

    @patch("loyalty.management.commands.sync_customers.fetch_customer_page")
    def test_dry_run_writes_nothing_and_prints_counts(self, fetch):
        fetch.return_value = ([self.customer(1, email="private@example.com")], None)
        call_command("sync_customers", dry_run=True, stdout=self.stdout)
        self.assertFalse(CustomerProfile.objects.exists())
        self.assertIn("tạo 1", self.stdout.getvalue())
        self.assertNotIn("private@example.com", self.stdout.getvalue())

    @patch("loyalty.management.commands.sync_customers.time.sleep")
    @patch("loyalty.management.commands.sync_customers.fetch_customer_page")
    def test_pagination_idempotent_and_balances_unchanged(self, fetch, sleep):
        profile = CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=1, email="old@example.com",
                                                 available_balance=-5, pending_balance=10, lifetime_earned=20)
        pages = [([self.customer(1, email="new@example.com")], "cursor-1"), ([self.customer(2)], None)]
        fetch.side_effect = pages
        call_command("sync_customers", stdout=self.stdout)
        self.assertIn("tạo 1, cập nhật 1, giữ nguyên 0", self.stdout.getvalue())
        self.assertEqual(fetch.call_args_list[1].args, ("cursor-1",))
        self.assertEqual(fetch.call_args_list[0].kwargs, {"page_size": 100})
        sleep.assert_called_once_with(0.1)
        profile.refresh_from_db()
        self.assertEqual((profile.available_balance, profile.pending_balance, profile.lifetime_earned), (-5, 10, 20))
        fetch.side_effect = pages
        again = StringIO()
        call_command("sync_customers", stdout=again)
        self.assertIn("tạo 0, cập nhật 0, giữ nguyên 2", again.getvalue())
        self.assertEqual(CustomerProfile.objects.count(), 2)

    @patch("loyalty.management.commands.sync_customers.time.sleep")
    @patch("loyalty.management.commands.sync_customers.fetch_customer_page")
    def test_transient_stops_with_cursor_and_resume(self, fetch, sleep):
        fetch.side_effect = [([self.customer(1)], "last-completed-cursor"), ShopifyTransientError("transient")]
        with self.assertRaises(CommandError):
            call_command("sync_customers", stdout=self.stdout)
        self.assertIn("last-completed-cursor", self.stdout.getvalue())
        self.assertEqual(CustomerProfile.objects.count(), 1)
        fetch.side_effect = [([self.customer(2)], None)]
        call_command("sync_customers", cursor="last-completed-cursor", stdout=StringIO())
        self.assertEqual(fetch.call_args.args, ("last-completed-cursor",))

    @patch("loyalty.management.commands.sync_customers.fetch_customer_page")
    def test_limit_stops_without_fetching_next_page(self, fetch):
        fetch.return_value = ([self.customer(1), self.customer(2)], "cursor-1")
        call_command("sync_customers", limit=1, stdout=self.stdout)
        self.assertEqual(CustomerProfile.objects.count(), 1)
        fetch.assert_called_once()

    @patch("loyalty.management.commands.sync_customers.fetch_customer_page")
    def test_dry_run_anonymized_profile_unchanged(self, fetch):
        CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=1, is_anonymized=True)
        fetch.return_value = ([self.customer(1, email="private@example.com")], None)
        call_command("sync_customers", dry_run=True, stdout=self.stdout)
        self.assertIn("giữ nguyên 1", self.stdout.getvalue())


class ReprocessCommandTests(TestCase):
    def setUp(self):
        self.events = []
        for status, topic in [("SKIPPED", "orders/paid"), ("FAILED", "orders/paid"),
                              ("SKIPPED", "customers/create"), ("PROCESSED", "orders/paid"),
                              ("PROCESSING", "orders/paid")]:
            self.events.append(WebhookEvent.objects.create(shop_domain="test.myshopify.com",
                webhook_id=f"delivery-{len(self.events)}", topic=topic, payload={"id": 1001}, status=status,
                attempts=4, skip_reason="old reason", error="old error", last_attempt_at=timezone.now(),
                processed_at=timezone.now()))

    def test_default_is_dry_run(self):
        output = StringIO()
        call_command("reprocess_events", topic="orders/paid", stdout=output)
        self.assertIn("Dự kiến đặt lại 2", output.getvalue())
        self.assertIn("2 phút", output.getvalue())
        self.assertFalse(WebhookEvent.objects.filter(status="RECEIVED").exists())

    def test_resets_only_matching_rows_and_preserves_received_time(self):
        event = self.events[1]
        received = event.received_at
        call_command("reprocess_events", topic="orders/paid", status="FAILED", yes=True, stdout=StringIO())
        event.refresh_from_db()
        self.assertEqual((event.status, event.attempts, event.error, event.skip_reason), ("RECEIVED", 0, "", ""))
        self.assertIsNone(event.last_attempt_at)
        self.assertIsNone(event.processed_at)
        self.assertEqual(event.received_at, received)
        self.assertEqual(WebhookEvent.objects.filter(status="RECEIVED").count(), 1)

    def test_event_id_and_limit_and_dry_run_override(self):
        call_command("reprocess_events", event_id=self.events[0].pk, yes=True, dry_run=True, stdout=StringIO())
        self.assertFalse(WebhookEvent.objects.filter(status="RECEIVED").exists())
        call_command("reprocess_events", topic="orders/paid", yes=True, limit=1, stdout=StringIO())
        self.assertEqual(WebhookEvent.objects.filter(status="RECEIVED").count(), 1)
        self.events[0].refresh_from_db()
        self.assertEqual(self.events[0].status, "RECEIVED")


class LedgerCommandTests(TestCase):
    def setUp(self):
        shop = Shop.objects.create(domain="test.myshopify.com", currency="VND")
        self.profile = CustomerProfile.objects.create(shop=shop, shopify_customer_id=301)

    def test_valid_profile_exit_zero(self):
        output = StringIO()
        call_command("check_ledger", profile=self.profile.pk, stdout=output)
        self.assertIn("hợp lệ: 1; có lỗi: 0", output.getvalue())

    def test_corrupt_balance_exit_one(self):
        CustomerProfile.objects.filter(pk=self.profile.pk).update(available_balance=10)
        with self.assertRaises(CommandError) as error:
            call_command("check_ledger", stdout=StringIO())
        self.assertEqual(error.exception.returncode, 1)

    def test_detects_debt_with_available_lots(self):
        from loyalty.services.integrity import check_profile_integrity
        CustomerProfile.objects.filter(pk=self.profile.pk).update(available_balance=-5)
        PointsLot.objects.create(profile=self.profile, source_type="ADJUSTMENT", points_total=1,
                                 points_remaining=1, status="AVAILABLE")
        PointsTransaction.objects.create(profile=self.profile, type="ADJUST", delta=-5, balance_after=-4,
                                         idempotency_key="test-broken")
        problems = check_profile_integrity(self.profile)
        self.assertIn("debt_with_available_lots", problems)

    def test_missing_profile_exit_one(self):
        with self.assertRaises(CommandError):
            call_command("check_ledger", profile=999999, stdout=StringIO())
