from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from shops.models import Shop
from webhooks.models import WebhookEvent

from loyalty.models import AuditLog, CustomerProfile, PointsLot, PointsTransaction, ProgramConfig, Redemption, Reward


class ModelTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.shop = Shop.objects.create(domain="test.myshopify.com", currency="VND")
        cls.profile = CustomerProfile.objects.create(shop=cls.shop, shopify_customer_id=101)
        cls.reward = Reward.objects.create(shop=cls.shop, name="Voucher", points_cost=10,
                                           discount_type="FIXED_AMOUNT", discount_value=10000)

    def create_entry(self, **overrides):
        values = {"profile": self.profile, "type": "EARN", "delta": 10,
                  "balance_after": 10, "idempotency_key": "shop:1:earn:1001"}
        values.update(overrides)
        return PointsTransaction.objects.create(**values)


class ConstraintTests(ModelTestCase):
    def assert_database_rejects(self, callback):
        with self.assertRaises(IntegrityError), transaction.atomic():
            callback()

    def test_customer_id_unique_per_shop(self):
        self.assert_database_rejects(lambda: CustomerProfile.objects.create(
            shop=self.shop, shopify_customer_id=101))
        other = Shop.objects.create(domain="other.myshopify.com", currency="USD")
        CustomerProfile.objects.create(shop=other, shopify_customer_id=101)

    def test_config_version_unique_per_shop(self):
        ProgramConfig.objects.create(shop=self.shop, version=1, effective_from=timezone.now())
        self.assert_database_rejects(lambda: ProgramConfig.objects.create(
            shop=self.shop, version=1, effective_from=timezone.now()))

    def test_transaction_key_unique(self):
        self.create_entry()
        self.assert_database_rejects(self.create_entry)

    def test_redemption_key_unique(self):
        values = {"profile": self.profile, "reward": self.reward, "points_spent": 10,
                  "idempotency_key": "redeem:client-1"}
        Redemption.objects.create(**values)
        self.assert_database_rejects(lambda: Redemption.objects.create(**values))

    def test_spend_must_be_positive(self):
        for spend in (Decimal("0"), Decimal("-1")):
            with self.subTest(spend=spend):
                self.assert_database_rejects(lambda: ProgramConfig.objects.create(
                    shop=self.shop, version=1, effective_from=timezone.now(), spend_per_point=spend))

    def test_lot_remaining_bounded_and_never_negative(self):
        for remaining in (-1, 11):
            with self.subTest(remaining=remaining):
                self.assert_database_rejects(lambda: PointsLot.objects.create(
                    profile=self.profile, source_type="ORDER", status="PENDING",
                    points_total=10, points_remaining=remaining))

    def test_transaction_delta_nonzero(self):
        self.assert_database_rejects(lambda: self.create_entry(delta=0))

    def test_reward_values_and_percentage_limit(self):
        for values in ({"points_cost": 0}, {"discount_value": 0},
                       {"discount_type": "PERCENTAGE", "discount_value": 101}):
            with self.subTest(values=values):
                params = {"shop": self.shop, "name": "Invalid", "points_cost": 10,
                          "discount_type": "FIXED_AMOUNT", "discount_value": 10000}
                params.update(values)
                self.assert_database_rejects(lambda: Reward.objects.create(**params))
        Reward.objects.create(shop=self.shop, name="100%", points_cost=10,
                              discount_type="PERCENTAGE", discount_value=100)

    def test_discount_code_unique_only_when_present(self):
        def create(key, code=None):
            return Redemption.objects.create(profile=self.profile, reward=self.reward,
                                             points_spent=10, idempotency_key=key, discount_code=code)
        create("one")
        create("two")
        create("three", "LOYALTY-CODE")
        self.assert_database_rejects(lambda: create("four", "LOYALTY-CODE"))

    def test_available_balance_can_be_negative(self):
        self.profile.available_balance = -10
        self.profile.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.available_balance, -10)

    def test_reward_cannot_be_hard_deleted(self):
        with self.assertRaises(ValidationError):
            self.reward.delete()
        with self.assertRaises(ValidationError):
            Reward.objects.filter(pk=self.reward.pk).delete()


class ConfigTests(ModelTestCase):
    def test_defaults(self):
        config = ProgramConfig.objects.create(shop=self.shop, version=1, effective_from=timezone.now())
        expected = {"pending_days": 7, "expiry_days": 30, "allow_negative_balance": True,
                    "spend_per_point": Decimal("10000"), "include_tax": False,
                    "include_shipping": False, "expiry_reminder_days": 5,
                    "max_redemptions_per_day": 3, "max_unused_codes_per_reward": 1,
                    "refund_points_on_expired_code": True, "refund_points_on_cancelled_order": True,
                    "negative_balance_alert_threshold": -500, "welcome_points": 0,
                    "is_enabled": True}
        for field, value in expected.items():
            with self.subTest(field=field):
                self.assertEqual(getattr(config, field), value)
        self.assertEqual(config.excluded_product_ids, [])
        self.assertEqual(config.excluded_collection_ids, [])
        self.assertEqual(config.excluded_channels, [])

    def test_effective_boundary_and_shop_isolation(self):
        start = timezone.now()
        boundary = start + timedelta(days=1)
        first = ProgramConfig.objects.create(shop=self.shop, version=1, effective_from=start)
        second = ProgramConfig.objects.create(shop=self.shop, version=2, effective_from=boundary)
        self.assertIsNone(ProgramConfig.get_effective(self.shop, start - timedelta(seconds=1)))
        self.assertEqual(ProgramConfig.get_effective(self.shop, boundary - timedelta(seconds=1)), first)
        self.assertEqual(ProgramConfig.get_effective(self.shop, boundary), second)
        self.assertEqual(ProgramConfig.get_effective(self.shop, boundary + timedelta(days=1)), second)
        other = Shop.objects.create(domain="other.myshopify.com", currency="USD")
        self.assertIsNone(ProgramConfig.get_effective(other, boundary))
        with patch("loyalty.models.timezone.now", return_value=boundary):
            self.assertEqual(ProgramConfig.get_effective(self.shop), second)

    def test_zero_pending_and_no_expiration_are_supported(self):
        config = ProgramConfig.objects.create(shop=self.shop, version=1, effective_from=timezone.now(),
                                              pending_days=0, expiry_days=0)
        self.assertEqual(config.expiry_days, 0)


class ImmutabilityTests(ModelTestCase):
    def immutable_rows(self):
        return [self.create_entry(), AuditLog.objects.create(
            shop=self.shop, actor="system", action="test", target_type="profile", target_id=str(self.profile.pk)),
            ProgramConfig.objects.create(shop=self.shop, version=1, effective_from=timezone.now())]

    def test_orm_history_protection(self):
        for row in self.immutable_rows():
            with self.subTest(model=type(row).__name__):
                with self.assertRaises(ValidationError):
                    row.save()
                with self.assertRaises(ValidationError):
                    row.delete()
                with self.assertRaises(ValidationError):
                    type(row).objects.filter(pk=row.pk).update(pk=row.pk)
                with self.assertRaises(ValidationError):
                    type(row).objects.filter(pk=row.pk).delete()
                with self.assertRaises(ValidationError):
                    type(row).objects.bulk_update([row], ["created_at"])
                with self.assertRaises(ValidationError):
                    type(row).objects.bulk_create([row], update_conflicts=True,
                                                  update_fields=["created_at"], unique_fields=["id"])

    def test_database_trigger_blocks_update_and_delete(self):
        self.assertEqual(connection.vendor, "postgresql")
        rows = self.immutable_rows()[:2]
        for row in rows:
            table = connection.ops.quote_name(row._meta.db_table)
            for sql in (f"UPDATE {table} SET id = id WHERE id = %s", f"DELETE FROM {table} WHERE id = %s"):
                with self.subTest(model=type(row).__name__, sql=sql):
                    with self.assertRaises(DatabaseError), transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute(sql, [row.pk])


class AdminTests(ModelTestCase):
    def test_history_and_events_are_fully_read_only(self):
        request = RequestFactory().get("/admin/")
        request.user = get_user_model().objects.create_superuser(username="admin", password="test")
        for model in (PointsTransaction, AuditLog, WebhookEvent):
            model_admin = admin.site._registry[model]
            self.assertFalse(model_admin.has_add_permission(request))
            self.assertFalse(model_admin.has_change_permission(request))
            self.assertFalse(model_admin.has_delete_permission(request))
            self.assertTrue(model_admin.has_view_permission(request))
            self.assertEqual(set(model_admin.get_readonly_fields(request)),
                             {field.name for field in model._meta.fields})
        config_admin = admin.site._registry[ProgramConfig]
        self.assertTrue(config_admin.has_add_permission(request))
        self.assertFalse(config_admin.has_change_permission(request))
        self.assertFalse(config_admin.has_delete_permission(request))


@override_settings(SHOPIFY_SHOP_DOMAIN="seed.myshopify.com")
class SeedTests(TestCase):
    def test_seed_idempotency_and_preservation(self):
        with patch.dict("os.environ", {"SHOP_CURRENCY": "USD"}):
            call_command("seed_dev_data", stdout=StringIO())
        shop = Shop.objects.get(domain="seed.myshopify.com")
        config = ProgramConfig.objects.get(shop=shop, version=1)
        original_time = config.effective_from
        with patch.dict("os.environ", {"SHOP_CURRENCY": "VND"}):
            call_command("seed_dev_data", stdout=StringIO())
        shop.refresh_from_db()
        config.refresh_from_db()
        self.assertEqual(shop.currency, "USD")
        self.assertEqual(config.effective_from, original_time)
        self.assertEqual(Shop.objects.count(), 1)
        self.assertEqual(ProgramConfig.objects.count(), 1)
        self.assertTrue(config.allow_negative_balance)
