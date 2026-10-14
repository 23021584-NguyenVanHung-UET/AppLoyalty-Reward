import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from loyalty.models import ProgramConfig
from shops.models import Shop


class Command(BaseCommand):
    help = "Tạo cửa hàng và cấu hình phát triển nếu chưa tồn tại, không ghi đè dữ liệu."

    @transaction.atomic
    def handle(self, *args, **options):
        domain = settings.SHOPIFY_SHOP_DOMAIN.strip().lower()
        if not domain or not domain.endswith(".myshopify.com") or "/" in domain:
            raise CommandError("SHOPIFY_SHOP_DOMAIN phải là tên miền *.myshopify.com, không gồm https://.")
        currency = os.environ.get("SHOP_CURRENCY", "VND").strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise CommandError("SHOP_CURRENCY phải là mã tiền tệ gồm 3 chữ cái.")
        shop, shop_created = Shop.objects.get_or_create(domain=domain, defaults={"currency": currency})
        config, config_created = ProgramConfig.objects.get_or_create(
            shop=shop, version=1,
            defaults={"effective_from": timezone.now(), "created_by": "system"},
        )
        self.stdout.write(f"Shop: {'created' if shop_created else 'kept'} ({shop.domain})")
        self.stdout.write(f"ProgramConfig: {'created' if config_created else 'kept'} (version {config.version})")
