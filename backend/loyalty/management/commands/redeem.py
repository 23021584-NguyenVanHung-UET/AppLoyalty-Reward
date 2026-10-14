from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from loyalty.services.redemption import redeem_reward
from loyalty.services.timing import parse_now
from shops.models import Shop


class Command(BaseCommand):
    help = "Đổi điểm lấy mã Shopify cho một khách hàng."

    def add_arguments(self, parser):
        parser.add_argument("--customer-id", required=True, type=int)
        parser.add_argument("--reward-id", required=True, type=int)
        parser.add_argument("--key", default=None)
        parser.add_argument("--as-of", default=None)

    def handle(self, *args, **options):
        try:
            now = parse_now(options["as_of"])
            shop = Shop.objects.get(domain=settings.SHOPIFY_SHOP_DOMAIN.strip().lower())
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        except Shop.DoesNotExist as exc:
            raise CommandError("SHOP_NOT_FOUND: Chưa có cửa hàng được cấu hình.") from exc
        key = options["key"] or str(uuid4())
        result = redeem_reward(shop, options["customer_id"], options["reward_id"], key, now)
        self.stdout.write(f"Khóa yêu cầu: {key}; lượt đổi: {result.redemption_id}; {result.reason_code}: {result.message}")
        # This explicit one-customer command is the only place that prints a discount code.
        if result.code:
            self.stdout.write(f"Mã giảm giá: {result.code}")

