from django.db import IntegrityError, transaction
from django.test import TestCase

from shops.models import Shop


class ShopTests(TestCase):
    def test_domain_is_unique_and_defaults_match(self):
        shop = Shop.objects.create(domain="test.myshopify.com", currency="VND")
        self.assertTrue(shop.is_active)
        self.assertIsNone(shop.uninstalled_at)
        self.assertIsNotNone(shop.created_at)
        self.assertIsNotNone(shop.updated_at)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Shop.objects.create(domain=shop.domain, currency="USD")
