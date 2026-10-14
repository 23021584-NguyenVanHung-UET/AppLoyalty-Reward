from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django_q.models import Schedule

from shops import shopify
from webhooks.security import verify_signature


def page(nodes, has_next=False, cursor=None):
    return {"webhookSubscriptions": {"nodes": nodes,
            "pageInfo": {"hasNextPage": has_next, "endCursor": cursor}}}


def subscription(number, topic, uri, format="JSON"):
    return {"id": f"gid://shopify/WebhookSubscription/{number}",
            "topic": topic, "uri": uri, "format": format}


@override_settings(SHOPIFY_CLIENT_SECRET="command-test-secret")
class RegistrationTests(TestCase):
    base_url = "https://new.example.com"
    callback = base_url + "/webhooks/shopify/"

    def run_command(self, **kwargs):
        stdout, stderr = StringIO(), StringIO()
        call_command("register_webhooks", base_url=self.base_url,
                     stdout=stdout, stderr=stderr, **kwargs)
        return stdout.getvalue(), stderr.getvalue()

    @patch("shops.shopify.graphql")
    def test_dry_run_only_queries_and_prints_plan(self, graphql):
        graphql.return_value = page([])
        stdout, _ = self.run_command(dry_run=True)
        graphql.assert_called_once_with(shopify.WEBHOOK_SUBSCRIPTIONS_QUERY, {"after": None})
        self.assertIn("không thực hiện thay đổi", stdout)
        for topic in shopify.WEBHOOK_TOPICS:
            self.assertIn(topic, stdout)

    @patch("shops.shopify.graphql")
    def test_create_update_skip_decisions_and_variables(self, graphql):
        graphql.side_effect = [
            page([subscription(1, "ORDERS_PAID", self.callback),
                  subscription(2, "CUSTOMERS_CREATE", "https://old.example.com/webhooks/shopify/")]),
            {"webhookSubscriptionUpdate": {"webhookSubscription": {"id": "gid://shopify/WebhookSubscription/2"}, "userErrors": []}},
            {"webhookSubscriptionCreate": {"webhookSubscription": {"id": "gid://shopify/WebhookSubscription/3"}, "userErrors": []}},
        ]
        stdout, _ = self.run_command(topics="orders/paid,customers/create,refunds/create")
        self.assertEqual(graphql.call_count, 3)
        self.assertEqual(graphql.call_args_list[1].args, (shopify.WEBHOOK_UPDATE_MUTATION, {
            "id": "gid://shopify/WebhookSubscription/2",
            "subscription": {"format": "JSON", "callbackUrl": self.callback},
        }))
        self.assertEqual(graphql.call_args_list[2].args, (shopify.WEBHOOK_CREATE_MUTATION, {
            "topic": "REFUNDS_CREATE", "subscription": {"format": "JSON", "callbackUrl": self.callback},
        }))
        self.assertIn("giữ nguyên", stdout)
        self.assertIn("cập nhật", stdout)
        self.assertIn("tạo", stdout)
        self.assertNotIn("gid://", stdout)

    @patch("shops.shopify.graphql")
    def test_same_url_wrong_format_is_updated(self, graphql):
        graphql.side_effect = [page([subscription(1, "ORDERS_PAID", self.callback, format="XML")]),
                               {"webhookSubscriptionUpdate": {"webhookSubscription": {"id": "gid://shopify/WebhookSubscription/1"}, "userErrors": []}}]
        self.run_command(topics="orders/paid")
        self.assertEqual(graphql.call_args.args[0], shopify.WEBHOOK_UPDATE_MUTATION)

    @patch("shops.shopify.graphql")
    def test_paginates_all_subscriptions(self, graphql):
        graphql.side_effect = [page([], True, "cursor-1"),
                               page([subscription(1, "ORDERS_PAID", self.callback)])]
        self.run_command(topics="orders/paid", dry_run=True)
        self.assertEqual(graphql.call_count, 2)
        self.assertEqual(graphql.call_args.args[1], {"after": "cursor-1"})

    @patch("shops.shopify.graphql")
    def test_delete_stale_only_unwanted_topics_on_different_host(self, graphql):
        graphql.side_effect = [page([
            subscription(1, "ORDERS_PAID", self.callback),
            subscription(2, "CUSTOMERS_CREATE", "https://old.example.com/callback"),
            subscription(3, "CUSTOMERS_UPDATE", self.base_url + "/other-path"),
        ]), {"webhookSubscriptionDelete": {"deletedWebhookSubscriptionId": "gid://shopify/WebhookSubscription/2", "userErrors": []}}]
        stdout, _ = self.run_command(topics="orders/paid", delete_stale=True)
        self.assertEqual(graphql.call_count, 2)
        self.assertEqual(graphql.call_args.args, (shopify.WEBHOOK_DELETE_MUTATION,
                                                {"id": "gid://shopify/WebhookSubscription/2"}))
        self.assertNotIn("customers/update", stdout)

    @patch("shops.shopify.graphql")
    def test_dry_run_delete_stale_also_never_mutates(self, graphql):
        graphql.return_value = page([subscription(2, "CUSTOMERS_CREATE", "https://old.example.com/callback")])
        stdout, _ = self.run_command(topics="orders/paid", delete_stale=True, dry_run=True)
        graphql.assert_called_once()
        self.assertIn("xóa", stdout)

    @patch("shops.shopify.graphql")
    def test_rejects_non_https_and_credentials_before_network(self, graphql):
        for url in ("http://example.com", "https://", "https://user:password@example.com",
                    "https://example.com?query=1", "https://example.com:invalid"):
            with self.subTest(url=url), self.assertRaises(CommandError):
                call_command("register_webhooks", base_url=url, stdout=StringIO())
        graphql.assert_not_called()

    @patch("shops.shopify.graphql")
    def test_rejects_unknown_topic_before_network(self, graphql):
        with self.assertRaises(CommandError):
            self.run_command(topics="not/a-topic")
        graphql.assert_not_called()

    @patch("shops.shopify.graphql")
    def test_user_errors_are_visible_and_command_fails(self, graphql):
        graphql.side_effect = [page([]), {"webhookSubscriptionCreate": {
            "webhookSubscription": None,
            "userErrors": [{"field": ["topic"], "message": "You cannot create this subscription"}],
        }}]
        stderr = StringIO()
        with self.assertRaises(CommandError):
            call_command("register_webhooks", base_url=self.base_url, topics="orders/paid",
                         stdout=StringIO(), stderr=stderr)
        self.assertIn("You cannot create this subscription", stderr.getvalue())
        self.assertIn('"field": ["topic"]', stderr.getvalue())

    @patch("shops.shopify.graphql")
    def test_graphql_errors_visible_and_secrets_redacted(self, graphql):
        graphql.side_effect = shopify.ShopifyError("GraphQL errors: Access denied command-test-secret")
        with self.assertRaises(CommandError) as raised:
            self.run_command(dry_run=True)
        self.assertIn("GraphQL errors: Access denied", str(raised.exception))
        self.assertNotIn("command-test-secret", str(raised.exception))


class ScheduleTests(TestCase):
    def test_setup_idempotent_and_updates_existing_schedule(self):
        call_command("setup_schedules", stdout=StringIO())
        schedule = Schedule.objects.get(func="webhooks.tasks.sweep_webhook_events")
        initial_next_run = schedule.next_run
        schedule.minutes = 5
        schedule.repeats = 0
        schedule.save()
        call_command("setup_schedules", stdout=StringIO())
        self.assertEqual(Schedule.objects.count(), 10)
        self.assertEqual(Schedule.objects.get(func="loyalty.tasks.grant_birthdays").schedule_type, Schedule.DAILY)
        self.assertEqual(Schedule.objects.get(func="loyalty.tasks.sweep_referrals").schedule_type, Schedule.HOURLY)
        self.assertEqual(Schedule.objects.get(func="loyalty.tasks.expiring_notifications").schedule_type, Schedule.DAILY)
        self.assertEqual(Schedule.objects.get(func="loyalty.tasks.sweep_notifications").minutes, 1)
        self.assertEqual(Schedule.objects.get(func="loyalty.tasks.evaluate_tiers").schedule_type, Schedule.DAILY)
        schedule.refresh_from_db()
        self.assertEqual(schedule.minutes, 1)
        self.assertEqual(schedule.schedule_type, Schedule.MINUTES)
        self.assertEqual(schedule.repeats, -1)
        self.assertEqual(schedule.next_run, initial_next_run)


@override_settings(SHOPIFY_CLIENT_SECRET="command-test-secret", SHOPIFY_SHOP_DOMAIN="test.myshopify.com")
class SmokeCommandTests(TestCase):
    @patch("webhooks.management.commands.send_test_webhook.httpx.post")
    def test_duplicate_reuses_id_and_valid_signature(self, post):
        post.return_value = Mock(status_code=200)
        stdout = StringIO()
        call_command("send_test_webhook", duplicate=True, stdout=stdout)
        self.assertEqual(post.call_count, 2)
        first, second = post.call_args_list
        self.assertEqual(first.kwargs["headers"]["X-Shopify-Webhook-Id"],
                         second.kwargs["headers"]["X-Shopify-Webhook-Id"])
        self.assertTrue(verify_signature(first.kwargs["content"],
                                        first.kwargs["headers"]["X-Shopify-Hmac-Sha256"],
                                        "command-test-secret"))
        self.assertEqual(first.args[0], "http://127.0.0.1:8000/webhooks/shopify/")
        self.assertEqual(stdout.getvalue().count("HTTP 200"), 2)

    @patch("webhooks.management.commands.send_test_webhook.httpx.post")
    def test_bad_signature(self, post):
        post.return_value = Mock(status_code=403)
        stdout = StringIO()
        call_command("send_test_webhook", bad_signature=True, stdout=stdout)
        self.assertFalse(verify_signature(post.call_args.kwargs["content"],
                                         post.call_args.kwargs["headers"]["X-Shopify-Hmac-Sha256"],
                                         "command-test-secret"))
        self.assertIn("HTTP 403", stdout.getvalue())
