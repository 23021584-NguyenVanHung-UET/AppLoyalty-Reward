from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from loyalty.models import CustomerProfile, PointsLot, PointsTransaction, ProgramConfig, Reward
from loyalty.services.integrity import check_profile_integrity
from shops.models import Shop


class PointsSetup:
    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        self.shop = Shop.objects.create(domain="test.myshopify.com", currency="USD")
        self.config = ProgramConfig.objects.create(shop=self.shop, version=1,
            effective_from=self.now - timedelta(days=100), spend_per_point=10)
        self.profile = CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=301)
        self.reward = Reward.objects.create(shop=self.shop, name="Reward", points_cost=10,
            discount_type="FIXED_AMOUNT", discount_value=5)
        self.network = patch("shops.shopify.graphql", side_effect=AssertionError("Unexpected network")).start()
        self.addCleanup(patch.stopall)

    def lot(self, points=10, status="AVAILABLE", expires=None, available=None, order=None, profile=None):
        profile = profile or self.profile
        profile.refresh_from_db()
        lot = PointsLot.objects.create(profile=profile, source_type="ORDER" if order else "WELCOME",
            source_order_id=order, points_total=points, points_remaining=points, status=status,
            available_at=available or self.now, expires_at=expires)
        if status == "PENDING":
            profile.pending_balance += points
        else:
            profile.available_balance += points
        profile.lifetime_earned += points
        profile.save()
        PointsTransaction.objects.create(profile=profile, type="EARN", delta=points,
            balance_after=profile.available_balance, lot=lot, order_id=order,
            idempotency_key=f"fixture:{lot.pk}")
        return lot

    def debt(self, points):
        self.profile.refresh_from_db()
        self.profile.available_balance -= points
        self.profile.save()
        PointsTransaction.objects.create(profile=self.profile, type="REVOKE", delta=-points,
            balance_after=self.profile.available_balance, idempotency_key=f"debt:{PointsTransaction.objects.count()}")

    def config_version(self, **changes):
        current = ProgramConfig.get_effective(self.shop, at=self.now)
        values = {field.attname: getattr(current, field.attname) for field in ProgramConfig._meta.concrete_fields
                  if field.name not in ("id", "created_at", "updated_at", "version", "effective_from")}
        version = ProgramConfig.objects.filter(shop=self.shop).count() + 1
        values.update(changes, version=version, effective_from=self.now - timedelta(minutes=5) + timedelta(seconds=version))
        return ProgramConfig.objects.create(**values)

    def clean_ledger(self):
        for profile in CustomerProfile.objects.all():
            self.assertEqual(check_profile_integrity(profile), [])

