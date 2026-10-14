from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from loyalty.models import Reward
from shops.models import Shop


class Command(BaseCommand):
    help = "Tạo hai phần thưởng mẫu theo tiền tệ shop, không ghi đè phần thưởng đã có."

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            shop = Shop.objects.select_for_update().get(domain=settings.SHOPIFY_SHOP_DOMAIN.strip().lower())
        except Shop.DoesNotExist as exc:
            raise CommandError("SHOP_NOT_FOUND: Chưa có shop; chạy seed_dev_data trước.") from exc
        values = {"USD": Decimal("5"), "VND": Decimal("50000")}
        if shop.currency not in values:
            raise CommandError("UNSUPPORTED_CURRENCY: Chưa có giá trị mẫu cho tiền tệ shop; tạo phần thưởng thủ công.")
        for name, cost, kind, value in [
            (f"Mẫu: giảm {values[shop.currency]} {shop.currency}", 10, "FIXED_AMOUNT", values[shop.currency]),
            ("Mẫu: giảm 10%", 20, "PERCENTAGE", Decimal("10")),
        ]:
            reward, created = Reward.objects.get_or_create(shop=shop, name=name,
                defaults={"points_cost": cost, "discount_type": kind, "discount_value": value, "code_valid_days": 30})
            self.stdout.write(f"{'CREATED: Đã tạo' if created else 'KEPT: Giữ nguyên'} phần thưởng {reward.pk}: {reward.name}.")
