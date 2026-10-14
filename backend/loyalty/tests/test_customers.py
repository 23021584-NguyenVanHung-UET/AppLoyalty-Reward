from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections, connections
from django.test import TestCase, TransactionTestCase

from loyalty.models import CustomerProfile
from loyalty.services.customers import upsert_customer_profile
from shops.models import Shop
from webhooks.models import WebhookEvent
from webhooks.tasks import process_webhook


class CustomerSyncTests(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(domain="test.myshopify.com", currency="VND")

    def test_create_and_update_changed_nonempty_only_and_noop(self):
        profile, created = upsert_customer_profile(self.shop, 301, email="first@example.com", first_name="An")
        self.assertTrue(created)
        self.assertEqual((profile.available_balance, profile.pending_balance, profile.lifetime_earned), (0, 0, 0))
        profile.available_balance, profile.pending_balance, profile.lifetime_earned = -5, 10, 20
        profile.save()
        before = profile.updated_at
        same, created = upsert_customer_profile(self.shop, 301, email="first@example.com", first_name="An")
        self.assertFalse(created)
        self.assertEqual(same.updated_at, before)
        updated, _ = upsert_customer_profile(self.shop, 301, email="new@example.com", first_name="", last_name="Nguyen")
        self.assertEqual(updated.first_name, "An")
        self.assertEqual(updated.email, "new@example.com")
        self.assertEqual(updated.last_name, "Nguyen")
        self.assertEqual((updated.available_balance, updated.pending_balance, updated.lifetime_earned), (-5, 10, 20))

    def test_anonymized_profile_is_not_overwritten(self):
        profile = CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=301, is_anonymized=True)
        upsert_customer_profile(self.shop, 301, email="private@example.com", first_name="Private")
        profile.refresh_from_db()
        self.assertEqual(profile.email, "")
        self.assertEqual(profile.first_name, "")

    def event(self, topic, payload):
        event = WebhookEvent.objects.create(shop=self.shop, shop_domain=self.shop.domain, topic=topic,
                                            webhook_id=f"delivery-{WebhookEvent.objects.count()}", payload=payload)
        process_webhook(event.pk)
        event.refresh_from_db()
        return event

    def test_update_before_create_and_missing_names(self):
        self.assertEqual(self.event("customers/update", {"id": 301}).status, WebhookEvent.Status.PROCESSED)
        self.assertEqual(self.event("customers/create", {"id": 301, "first_name": "An"}).status,
                         WebhookEvent.Status.PROCESSED)
        self.assertEqual(CustomerProfile.objects.get().first_name, "An")
        self.assertEqual(CustomerProfile.objects.count(), 1)

    def test_missing_id_fails_without_profile(self):
        event = self.event("customers/create", {"email": "private@example.com"})
        self.assertEqual(event.status, WebhookEvent.Status.FAILED)
        self.assertIn("thiếu mã", event.error)
        self.assertFalse(CustomerProfile.objects.exists())


class ConcurrentCustomerTests(TransactionTestCase):
    def test_concurrent_upsert_keeps_one_profile(self):
        shop = Shop.objects.create(domain="test.myshopify.com", currency="VND")
        barrier = Barrier(2)
        def sync(_):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                profile, created = upsert_customer_profile(Shop.objects.get(pk=shop.pk), 301, first_name="An")
                return profile.pk, created
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(sync, range(2)))
        self.assertEqual(results[0][0], results[1][0])
        self.assertCountEqual([created for _, created in results], [True, False])
        self.assertEqual(CustomerProfile.objects.count(), 1)
