import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from loyalty.models import CustomerProfile
from loyalty.services.customers import customer_changes, upsert_customer_profile
from shops.models import Shop
from shops.shopify import ShopifyError, ShopifyTransientError, fetch_customer_page, numeric_id


class Command(BaseCommand):
    help = "Đồng bộ khách hàng Shopify theo trang; không thay đổi số dư điểm."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, help="Số khách hàng tối đa cần đọc.")
        parser.add_argument("--dry-run", action="store_true", help="Chỉ thống kê, không ghi dữ liệu.")
        parser.add_argument("--cursor", help="Tiếp tục từ con trỏ trang đã báo khi gặp lỗi.")

    def handle(self, *args, **options):
        if options["limit"] is not None and options["limit"] < 1:
            raise CommandError("--limit phải lớn hơn 0.")
        shop = Shop.objects.filter(domain__iexact=settings.SHOPIFY_SHOP_DOMAIN, is_active=True).first()
        if not shop:
            raise CommandError("Không tìm thấy cửa hàng đang hoạt động theo cấu hình Shopify.")
        cursor, seen = options["cursor"], set()
        counts = {"created": 0, "updated": 0, "unchanged": 0}
        processed = 0

        def summary():
            prefix = "Dự kiến" if options["dry_run"] else "Đã đồng bộ"
            self.stdout.write(f"{prefix}: tạo {counts['created']}, cập nhật {counts['updated']}, giữ nguyên {counts['unchanged']}.")

        while True:
            try:
                nodes, next_cursor = fetch_customer_page(cursor, page_size=100)
            except ShopifyTransientError:
                summary()
                self.stdout.write(f"Con trỏ trang hoàn tất gần nhất: {cursor or '(bắt đầu)'}")
                raise CommandError("Shopify tạm thời bị lỗi; chạy lại với --cursor nếu có con trỏ.") from None
            except ShopifyError:
                summary()
                raise CommandError("Không đọc được khách hàng Shopify; kiểm tra quyền truy cập và cấu hình API.") from None
            for customer in nodes:
                customer_id = numeric_id(customer["id"])
                profile = CustomerProfile.objects.filter(shop=shop, shopify_customer_id=customer_id).first()
                values = {"email": customer.get("email"), "first_name": customer.get("firstName"),
                          "last_name": customer.get("lastName")}
                values["locale"] = customer.get("locale")
                values["shopify_created_at"] = customer.get("createdAt")
                if "emailMarketingConsent" in customer:
                    values["email_marketing_opt_in"] = (customer.get("emailMarketingConsent") or {}).get("marketingState") == "SUBSCRIBED"
                changed = bool(profile and customer_changes(profile, **values))
                if options["dry_run"]:
                    created = profile is None
                else:
                    _, created = upsert_customer_profile(shop, customer_id, **values)
                counts["created" if created else "updated" if changed else "unchanged"] += 1
                processed += 1
                if options["limit"] is not None and processed >= options["limit"]:
                    summary()
                    return
            if next_cursor is None:
                summary()
                return
            if next_cursor == cursor or next_cursor in seen:
                raise CommandError("Con trỏ khách hàng bị lặp; dừng để tránh vòng lặp vô hạn.")
            cursor = next_cursor
            seen.add(cursor)
            time.sleep(0.1)
