from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from webhooks import handlers, tasks
from webhooks.models import WebhookEvent

Status = WebhookEvent.Status


class EventTestCase(TestCase):
    def event(self, **kwargs):
        values = {"webhook_id": f"delivery-{WebhookEvent.objects.count()}",
                  "shop_domain": "test.myshopify.com", "topic": "test/success", "payload": {"id": 1}}
        values.update(kwargs)
        return WebhookEvent.objects.create(**values)


@override_settings(SHOPIFY_CLIENT_SECRET="test-secret")
class ProcessingTests(EventTestCase):
    def test_unknown_topic(self):
        event = self.event()
        tasks.process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual(event.status, Status.SKIPPED)
        self.assertEqual(event.skip_reason, handlers.UNKNOWN_REASON)
        self.assertIsNotNone(event.processed_at)

    def test_remaining_stubs_skip_without_business_changes(self):
        for topic in ("app/uninstalled",):
            with self.subTest(topic=topic):
                event = self.event(topic=topic)
                tasks.process_webhook(event.pk)
                event.refresh_from_db()
                self.assertEqual(event.status, Status.SKIPPED)
                self.assertEqual(event.skip_reason, "Chưa triển khai handler cho topic này")
                self.assertEqual(event.attempts, 1)
                self.assertIsNotNone(event.last_attempt_at)

    def test_success_and_claim_released_before_handler(self):
        event = self.event(error="old error", skip_reason="old reason", status=Status.FAILED)
        def handler(row):
            # Another claim cannot run the handler while this claim is active.
            self.assertIsNone(tasks.claim_webhook(row.pk))
            self.assertEqual(WebhookEvent.objects.get(pk=row.pk).status, Status.PROCESSING)
        with patch.dict(handlers.HANDLERS, {event.topic: handler}):
            tasks.process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual(event.status, Status.PROCESSED)
        self.assertEqual(event.error, "")
        self.assertEqual(event.skip_reason, "")
        self.assertIsNotNone(event.processed_at)

    def test_failure_truncates_message_and_does_not_rethrow(self):
        event = self.event()
        handler = Mock(side_effect=RuntimeError("x" * 3000))
        with patch.dict(handlers.HANDLERS, {event.topic: handler}):
            tasks.process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual(event.status, Status.FAILED)
        self.assertEqual(event.attempts, 1)
        self.assertEqual(event.error, "x" * 2000)
        self.assertIsNone(event.processed_at)

    def test_error_redacts_payload_and_secret_and_logs_only_ids(self):
        event = self.event(payload={"email": "private@example.com"})
        handler = Mock(side_effect=RuntimeError(f"failure {event.payload} test-secret"))
        with patch.dict(handlers.HANDLERS, {event.topic: handler}), self.assertLogs("webhooks.tasks") as logs:
            tasks.process_webhook(event.pk)
        event.refresh_from_db()
        combined = event.error + " ".join(logs.output)
        self.assertNotIn("private@example.com", combined)
        self.assertNotIn("test-secret", combined)

    def test_terminal_or_processing_events_are_noop(self):
        for status in (Status.PROCESSED, Status.SKIPPED, Status.PROCESSING):
            with self.subTest(status=status):
                event = self.event(status=status, attempts=2)
                handler = Mock()
                with patch.dict(handlers.HANDLERS, {event.topic: handler}):
                    tasks.process_webhook(event.pk)
                event.refresh_from_db()
                self.assertEqual(event.attempts, 2)
                self.assertEqual(event.status, status)
                handler.assert_not_called()

    def test_second_claim_is_noop(self):
        event = self.event()
        self.assertIsNotNone(tasks.claim_webhook(event.pk))
        self.assertIsNone(tasks.claim_webhook(event.pk))
        event.refresh_from_db()
        self.assertEqual(event.attempts, 1)

    def test_missing_event_is_noop(self):
        tasks.process_webhook(9999999)

    def test_finally_updates_even_on_base_exception(self):
        event = self.event()
        with patch.dict(handlers.HANDLERS, {event.topic: Mock(side_effect=KeyboardInterrupt)}):
            with self.assertRaises(KeyboardInterrupt):
                tasks.process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual(event.status, Status.FAILED)

    def test_stale_worker_cannot_overwrite_new_attempt(self):
        event = self.event()
        def handler(row):
            WebhookEvent.objects.filter(pk=row.pk).update(status=Status.PROCESSED, attempts=2,
                                                        last_attempt_at=timezone.now(), error="new result")
        with patch.dict(handlers.HANDLERS, {event.topic: handler}):
            tasks.process_webhook(event.pk)
        event.refresh_from_db()
        self.assertEqual(event.attempts, 2)
        self.assertEqual(event.error, "new result")

    def test_exhausted_failed_event_cannot_be_claimed(self):
        self.assertIsNone(tasks.claim_webhook(self.event(status=Status.FAILED, attempts=5).pk))


class SweeperTests(EventTestCase):
    def setUp(self):
        self.now = timezone.now()
        self.enqueue = patch("webhooks.tasks.async_task").start()
        patch("webhooks.tasks.timezone.now", return_value=self.now).start()
        self.addCleanup(patch.stopall)

    def aged_event(self, age, **kwargs):
        event = self.event(**kwargs)
        WebhookEvent.objects.filter(pk=event.pk).update(received_at=self.now - timedelta(minutes=age))
        return event

    def sweep(self):
        with self.captureOnCommitCallbacks(execute=True):
            tasks.sweep_webhook_events()

    def test_old_received_only(self):
        old = self.aged_event(3)
        self.aged_event(1)
        self.sweep()
        self.enqueue.assert_called_once_with("webhooks.tasks.process_webhook", old.pk)

    def test_backoff_each_attempt_boundary(self):
        for attempt, wait in enumerate([1, 5, 15, 60], start=1):
            self.aged_event(100, status=Status.FAILED, attempts=attempt,
                            last_attempt_at=self.now - timedelta(minutes=wait) + timedelta(seconds=1))
            due = self.aged_event(100, status=Status.FAILED, attempts=attempt,
                                   last_attempt_at=self.now - timedelta(minutes=wait))
            self.sweep()
            self.enqueue.assert_any_call("webhooks.tasks.process_webhook", due.pk)
        due_ids = {call.args[1] for call in self.enqueue.call_args_list}
        self.assertEqual(len(due_ids), 4)

    def test_failed_without_attempt_time_uses_received_time(self):
        event = self.aged_event(2, status=Status.FAILED, attempts=1)
        self.sweep()
        self.enqueue.assert_called_once_with("webhooks.tasks.process_webhook", event.pk)

    def test_exhausted_events_warn_once_and_remain_untouched(self):
        events = [self.aged_event(100, status=Status.FAILED, attempts=5, error="keep") for _ in range(2)]
        with self.assertLogs("webhooks.tasks", level="WARNING") as logs:
            self.sweep()
        self.assertEqual(len(logs.output), 1)
        self.assertIn("2", logs.output[0])
        self.enqueue.assert_not_called()
        for event in events:
            event.refresh_from_db()
            self.assertEqual(event.error, "keep")

    def test_stuck_processing_recovered_and_enqueued_if_due(self):
        event = self.aged_event(20, status=Status.PROCESSING, attempts=1,
                                 last_attempt_at=self.now - timedelta(minutes=11))
        self.sweep()
        event.refresh_from_db()
        self.assertEqual(event.status, Status.FAILED)
        self.assertEqual(event.error, "Tiến trình xử lý bị gián đoạn")
        self.enqueue.assert_called_once_with("webhooks.tasks.process_webhook", event.pk)

    def test_stuck_processing_still_obeys_long_backoff(self):
        event = self.aged_event(100, status=Status.PROCESSING, attempts=4,
                                 last_attempt_at=self.now - timedelta(minutes=11))
        self.sweep()
        event.refresh_from_db()
        self.assertEqual(event.status, Status.FAILED)
        self.enqueue.assert_not_called()

    def test_fresh_processing_is_untouched_even_with_old_received_time(self):
        event = self.aged_event(100, status=Status.PROCESSING, attempts=1,
                                 last_attempt_at=self.now - timedelta(minutes=1))
        self.sweep()
        event.refresh_from_db()
        self.assertEqual(event.status, Status.PROCESSING)
        self.enqueue.assert_not_called()

    def test_legacy_stuck_processing_without_attempt_time(self):
        event = self.aged_event(20, status=Status.PROCESSING, attempts=1)
        self.sweep()
        event.refresh_from_db()
        self.assertEqual(event.status, Status.FAILED)
        self.enqueue.assert_called_once()

    def test_limit_200_oldest_first_and_ineligible_rows_do_not_starve(self):
        for _ in range(201):
            self.aged_event(200, status=Status.FAILED, attempts=4, last_attempt_at=self.now)
        due = [self.aged_event(210 - n / 10) for n in range(201)]
        self.sweep()
        self.assertEqual([call.args[1] for call in self.enqueue.call_args_list], [event.pk for event in due[:200]])

    def test_duplicate_sweeps_are_safe_with_claim(self):
        event = self.aged_event(3, topic="app/uninstalled")
        self.sweep()
        self.sweep()
        self.assertEqual(self.enqueue.call_count, 2)
        for call in self.enqueue.call_args_list:
            tasks.process_webhook(call.args[1])
        event.refresh_from_db()
        self.assertEqual(event.attempts, 1)
        self.assertEqual(event.status, Status.SKIPPED)
