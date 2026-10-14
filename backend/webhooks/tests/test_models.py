from django.db import IntegrityError, transaction
from django.test import TestCase

from webhooks.models import WebhookEvent


class WebhookEventTests(TestCase):
    def test_webhook_id_unique_and_defaults_match(self):
        event = WebhookEvent.objects.create(shop_domain="test.myshopify.com", webhook_id="delivery-1",
                                            topic="orders/paid", payload={"id": 1001})
        self.assertEqual(event.status, WebhookEvent.Status.RECEIVED)
        self.assertEqual(event.attempts, 0)
        self.assertIsNone(event.shop_id)
        self.assertIsNone(event.processed_at)
        with self.assertRaises(IntegrityError), transaction.atomic():
            WebhookEvent.objects.create(shop_domain=event.shop_domain, webhook_id=event.webhook_id,
                                         topic=event.topic, payload={})
