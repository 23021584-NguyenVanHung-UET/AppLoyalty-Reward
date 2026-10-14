from urllib.parse import urlsplit

import httpx
from django.core.management.base import BaseCommand, CommandError

from shops import shopify


class Command(BaseCommand):
    help = "Đăng ký webhook Shopify; dùng --dry-run để chỉ xem kế hoạch."

    def add_arguments(self, parser):
        parser.add_argument("--base-url", required=True, help="URL HTTPS công khai của backend.")
        parser.add_argument("--topics", default=",".join(shopify.WEBHOOK_TOPICS),
                            help="Danh sách topic phân tách bằng dấu phẩy.")
        parser.add_argument("--dry-run", action="store_true", help="Chỉ đọc và in kế hoạch.")
        parser.add_argument("--delete-stale", action="store_true",
                            help="Xóa topic không còn yêu cầu nếu đích thuộc host khác.")

    def handle(self, *args, **options):
        base_url = options["base_url"].rstrip("/")
        try:
            parsed = urlsplit(base_url)
            valid = (parsed.scheme == "https" and parsed.hostname and not parsed.username
                     and not parsed.password and not parsed.query and not parsed.fragment)
            parsed.port  # Validate malformed ports before contacting Shopify.
        except ValueError:
            valid = False
        if not valid:
            raise CommandError("--base-url phải là URL HTTPS hợp lệ, không chứa thông tin đăng nhập.")
        topics = list(dict.fromkeys(topic.strip() for topic in options["topics"].split(",") if topic.strip()))
        if not topics or any(topic not in shopify.WEBHOOK_TOPICS for topic in topics):
            raise CommandError("Topic không hợp lệ. Hỗ trợ: " + ", ".join(shopify.WEBHOOK_TOPICS))
        callback_url = base_url + "/webhooks/shopify/"
        try:
            existing = shopify.list_webhook_subscriptions()
        except (shopify.ShopifyError, httpx.HTTPError) as exception:
            raise CommandError(shopify.redact_shopify_error(exception)) from None

        wanted = {shopify.WEBHOOK_TOPICS[topic] for topic in topics}
        reverse_topics = {enum: topic for topic, enum in shopify.WEBHOOK_TOPICS.items()}
        plan = []
        for topic in topics:
            matches = [item for item in existing if item["topic"] == shopify.WEBHOOK_TOPICS[topic]]
            if not matches:
                plan.append((topic, "create", None))
            for item in matches:
                action = "skip" if item["uri"] == callback_url and item["format"] == "JSON" else "update"
                plan.append((topic, action, item))
        if options["delete_stale"]:
            for item in existing:
                if item["topic"] not in wanted and urlsplit(item["uri"]).hostname != parsed.hostname:
                    plan.append((reverse_topics.get(item["topic"], item["topic"]), "delete", item))

        labels = {"create": "tạo", "update": "cập nhật", "skip": "giữ nguyên", "delete": "xóa"}
        if options["dry_run"]:
            self.stdout.write("Kế hoạch xem trước — không thực hiện thay đổi.")
        self.stdout.write(f"{'Chủ đề':<28} {'Hành động':<16} Mã đăng ký")
        failures = 0
        for topic, action, item in plan:
            subscription_id = item["id"] if item else "-"
            label = labels[action]
            if not options["dry_run"]:
                try:
                    if action == "create":
                        subscription_id = shopify.create_webhook_subscription(topic, callback_url)["id"]
                    elif action == "update":
                        subscription_id = shopify.update_webhook_subscription(subscription_id, callback_url)["id"]
                    elif action == "delete":
                        shopify.delete_webhook_subscription(subscription_id)
                except (shopify.ShopifyError, httpx.HTTPError) as exception:
                    self.stderr.write(shopify.redact_shopify_error(exception))
                    label = "lỗi"
                    failures += 1
            self.stdout.write(f"{topic:<28} {label:<16} {subscription_id.rsplit('/', 1)[-1]}")
        if failures:
            raise CommandError(f"Có {failures} thao tác thất bại; xem lỗi Shopify ở trên.")
