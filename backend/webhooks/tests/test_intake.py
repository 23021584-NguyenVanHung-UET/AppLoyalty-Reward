import base64
import hashlib
import hmac
from unittest.mock import patch

from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings

from shops.models import Shop
from webhooks.models import WebhookEvent
from webhooks.security import verify_signature
from webhooks.views import shopify_webhook

SECRET = "webhook-test-secret"
DOMAIN = "test.myshopify.com"


def signature(body):
    return base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()


class SignatureTests(SimpleTestCase):
    def test_correct_signature(self):
        self.assertTrue(verify_signature(b'{"id": 1}', signature(b'{"id": 1}'), SECRET))

    def test_tampered_body(self):
        self.assertFalse(verify_signature(b'{"id": 2}', signature(b'{"id": 1}'), SECRET))

    def test_tampered_header(self):
        for header in ("invalid", "🔒", signature(b"{}")[:-1]):
            with self.subTest(header=header):
                self.assertFalse(verify_signature(b"{}", header, SECRET))

    def test_missing_signature_or_secret(self):
        self.assertFalse(verify_signature(b"{}", "", SECRET))
        self.assertFalse(verify_signature(b"{}", signature(b"{}"), ""))


@override_settings(SHOPIFY_CLIENT_SECRET=SECRET, SHOPIFY_SHOP_DOMAIN=DOMAIN)
class IntakeTests(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(domain=DOMAIN, currency="VND")
        self.client = Client(enforce_csrf_checks=True)
        self.enqueue = patch("webhooks.tasks.async_task").start()
        self.addCleanup(patch.stopall)

    def send(self, body=b'{"id": 1}', **overrides):
        headers = {
            "HTTP_X_SHOPIFY_HMAC_SHA256": signature(body),
            "HTTP_X_SHOPIFY_WEBHOOK_ID": "delivery-1",
            "HTTP_X_SHOPIFY_EVENT_ID": "event-1",
            "HTTP_X_SHOPIFY_TOPIC": "orders/paid",
            "HTTP_X_SHOPIFY_SHOP_DOMAIN": DOMAIN,
        }
        headers.update(overrides)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post("/webhooks/shopify/", body,
                                    content_type="application/json", **headers)

    def test_valid_and_case_insensitive_domain(self):
        response = self.send(HTTP_X_SHOPIFY_SHOP_DOMAIN=DOMAIN.upper())
        self.assertEqual(response.status_code, 200)
        event = WebhookEvent.objects.get()
        self.assertEqual(event.status, WebhookEvent.Status.RECEIVED)
        self.assertEqual(event.shop, self.shop)
        self.assertEqual(event.payload, {"id": 1})
        self.assertEqual(event.event_id, "event-1")
        self.enqueue.assert_called_once_with("webhooks.tasks.process_webhook", event.pk)

    def test_enqueue_waits_until_commit(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            response = self.client.post("/webhooks/shopify/", b"{}", content_type="application/json",
                                        HTTP_X_SHOPIFY_HMAC_SHA256=signature(b"{}"),
                                        HTTP_X_SHOPIFY_WEBHOOK_ID="delivery-1",
                                        HTTP_X_SHOPIFY_TOPIC="orders/paid",
                                        HTTP_X_SHOPIFY_SHOP_DOMAIN=DOMAIN)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(callbacks), 1)
        self.enqueue.assert_not_called()

    def test_duplicate_delivery_does_not_enqueue_twice(self):
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(WebhookEvent.objects.count(), 1)
        self.enqueue.assert_called_once()

    def test_bad_or_missing_signature(self):
        for header in ("", "bad"):
            with self.subTest(header=header), self.assertLogs("webhooks.views", level="WARNING"):
                self.assertEqual(self.send(HTTP_X_SHOPIFY_HMAC_SHA256=header).status_code, 403)
        self.assertFalse(WebhookEvent.objects.exists())
        self.enqueue.assert_not_called()

    def test_signature_is_checked_before_required_headers(self):
        self.assertEqual(self.send(HTTP_X_SHOPIFY_HMAC_SHA256="", HTTP_X_SHOPIFY_TOPIC="").status_code, 403)

    def test_required_headers(self):
        for header in ("HTTP_X_SHOPIFY_WEBHOOK_ID", "HTTP_X_SHOPIFY_TOPIC", "HTTP_X_SHOPIFY_SHOP_DOMAIN"):
            with self.subTest(header=header):
                self.assertEqual(self.send(**{header: ""}).status_code, 400)
        self.assertFalse(WebhookEvent.objects.exists())

    def test_invalid_json_with_valid_signature(self):
        for body in (b"{", b"\xff", b'{"value": NaN}', b"null"):
            with self.subTest(body=body):
                self.assertEqual(self.send(body).status_code, 400)
        self.assertFalse(WebhookEvent.objects.exists())

    def test_wrong_domain(self):
        self.assertEqual(self.send(HTTP_X_SHOPIFY_SHOP_DOMAIN="other.myshopify.com").status_code, 403)
        self.assertFalse(WebhookEvent.objects.exists())

    def test_inactive_shop(self):
        self.shop.is_active = False
        self.shop.save()
        self.assertEqual(self.send().status_code, 403)
        self.assertFalse(WebhookEvent.objects.exists())

    def test_unknown_shop(self):
        self.shop.delete()
        self.assertEqual(self.send().status_code, 403)
        self.assertFalse(WebhookEvent.objects.exists())

    def test_declared_oversize_rejected_before_signature(self):
        self.assertEqual(self.send(CONTENT_LENGTH="2000001", HTTP_X_SHOPIFY_HMAC_SHA256="").status_code, 413)
        self.assertFalse(WebhookEvent.objects.exists())

    def test_actual_oversize_even_if_declared_length_is_small(self):
        request = RequestFactory().post("/webhooks/shopify/", b"{}", content_type="application/json")
        request._body = b"x" * 21
        request.META["CONTENT_LENGTH"] = "2"
        with patch("webhooks.views.WEBHOOK_MAX_BODY_BYTES", 20):
            self.assertEqual(shopify_webhook(request).status_code, 413)
        self.assertFalse(WebhookEvent.objects.exists())

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get("/webhooks/shopify/").status_code, 405)

    def test_enqueue_failure_keeps_row_and_acknowledges(self):
        self.enqueue.side_effect = RuntimeError("broker failure with private details")
        with self.assertLogs("webhooks.tasks", level="ERROR") as logs:
            self.assertEqual(self.send().status_code, 200)
        self.assertEqual(WebhookEvent.objects.get().status, WebhookEvent.Status.RECEIVED)
        self.assertNotIn("private details", " ".join(logs.output))
