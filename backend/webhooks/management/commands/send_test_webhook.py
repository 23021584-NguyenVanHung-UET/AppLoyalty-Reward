import base64
import hashlib
import hmac
import json
import uuid

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Gửi webhook giả có chữ ký tới backend local; không gọi Shopify."

    def add_arguments(self, parser):
        parser.add_argument("--topic", default="orders/paid", help="Topic cần thử.")
        parser.add_argument("--duplicate", action="store_true", help="Gửi cùng webhook hai lần.")
        parser.add_argument("--bad-signature", action="store_true", help="Cố ý gửi chữ ký sai.")

    def handle(self, *args, **options):
        if not settings.SHOPIFY_CLIENT_SECRET or not settings.SHOPIFY_SHOP_DOMAIN:
            raise CommandError("Cần cấu hình client secret và tên miền cửa hàng trước khi thử.")
        raw_body = json.dumps({"id": 1000001, "test": True}).encode("utf-8")
        signature = base64.b64encode(hmac.new(
            settings.SHOPIFY_CLIENT_SECRET.encode("utf-8"), raw_body, hashlib.sha256,
        ).digest()).decode("ascii")
        headers = {
            "Content-Type": "application/json",
            "X-Shopify-Hmac-Sha256": "invalid" if options["bad_signature"] else signature,
            "X-Shopify-Webhook-Id": str(uuid.uuid4()),
            "X-Shopify-Event-Id": str(uuid.uuid4()),
            "X-Shopify-Topic": options["topic"],
            "X-Shopify-Shop-Domain": settings.SHOPIFY_SHOP_DOMAIN,
        }
        for number in range(2 if options["duplicate"] else 1):
            try:
                response = httpx.post("http://127.0.0.1:8000/webhooks/shopify/",
                                      content=raw_body, headers=headers, timeout=10)
            except httpx.HTTPError:
                raise CommandError("Không kết nối được backend local tại 127.0.0.1:8000.") from None
            self.stdout.write(f"Lần gửi {number + 1}: HTTP {response.status_code}")
