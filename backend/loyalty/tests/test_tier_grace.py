from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.db import close_old_connections, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django_q.brokers.orm import ORM
from django_q.models import OrmQ
from django_q.tasks import async_task as real_async_task

from loyalty.models import CustomerProfile, NotificationLog, NotificationSettings, PointsTransaction, Tier
from loyalty.services.integrity import check_profile_integrity
from loyalty.services.notifications import send_notification, tier_grace_notifications
from loyalty.services.tiers import evaluate_profile_tier, tier_state
from .lifecycle_fixtures import PointsSetup

KIND = "TIER_GRACE_REMINDER"


class GraceSetup(PointsSetup):
    def setUp(self):
        super().setUp()
        self.config_version(tiers_enabled=True, tier_downgrade_grace_days=30)
        self.base = Tier.objects.create(shop=self.shop, name="Base", rank=0, min_qualifying_points=0)
        self.gold = Tier.objects.create(shop=self.shop, name="Gold", rank=1, min_qualifying_points=100, multiplier="1.50")
        self.profile.email = "grace@example.invalid"
        self.profile.email_marketing_opt_in = True
        self.profile.save()
        self.settings = NotificationSettings.objects.create(shop=self.shop, enabled_types=[KIND])
        patch("loyalty.services.notifications.async_task").start()
        patch("django_q.tasks.async_task").start()
        with patch("django.utils.timezone.now", return_value=self.now):
            self.original = self.lot(120, order=1001)
        evaluate_profile_tier(self.profile, self.now)

    def drop(self, points=30, at=None):
        at = at or self.now + timedelta(days=1)
        with transaction.atomic(), patch("django.utils.timezone.now", return_value=at):
            profile = CustomerProfile.objects.select_for_update().get(pk=self.profile.pk)
            self.original.refresh_from_db()
            self.original.points_remaining -= points
            self.original.save(update_fields=["points_remaining"])
            profile.available_balance -= points
            profile.save(update_fields=["available_balance"])
            PointsTransaction.objects.create(profile=profile, type="REVOKE", delta=-points, order_id=1001,
                balance_after=profile.available_balance, idempotency_key=f"grace-revoke:{PointsTransaction.objects.count()}")
        evaluate_profile_tier(self.profile, at)
        self.profile.refresh_from_db()
        return at

    def entry(self, stage="started"):
        return NotificationLog.objects.filter(type=KIND, tier_grace_snapshot__stage=stage).latest("pk")

    def send(self, entry, at):
        with patch("django.utils.timezone.now", return_value=at):
            return send_notification(entry.pk)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class TierGraceTests(GraceSetup, TestCase):
    def test_state_shows_shortfall_from_qualifying_points_and_exact_deadline(self):
        start = self.drop()
        state = tier_state(self.profile, start)
        self.assertEqual(state["grace"], {"active": True,
            "deadline": (start + timedelta(days=30)).isoformat(), "points_to_keep": 10})
        self.assertEqual(state["qualifying_points"], 90)
        self.assertEqual(self.profile.tier_id, self.gold.pk)
        self.assertEqual(check_profile_integrity(self.profile), [])

    def test_start_and_seven_day_boundary_dedupe_and_no_extra_ledger(self):
        start = self.drop()
        count = PointsTransaction.objects.count()
        self.assertEqual(NotificationLog.objects.filter(type=KIND).count(), 1)
        for at in [start, start + timedelta(days=23) - timedelta(microseconds=1)]:
            self.assertEqual(tier_grace_notifications(at), 0)
        self.assertEqual(tier_grace_notifications(start + timedelta(days=23)), 1)
        self.assertEqual(tier_grace_notifications(start + timedelta(days=24)), 0)
        self.assertEqual(NotificationLog.objects.filter(type=KIND).count(), 2)
        self.assertEqual(PointsTransaction.objects.count(), count)
        self.assertEqual(check_profile_integrity(self.profile), [])

    def test_latest_drop_resets_banner_deadline_and_old_email_becomes_irrelevant(self):
        start = self.drop()
        old = self.entry()
        later = self.drop(10, start + timedelta(days=3))
        state = tier_state(self.profile, later)
        self.assertEqual(state["grace"]["deadline"], (later + timedelta(days=30)).isoformat())
        self.assertEqual(state["grace"]["points_to_keep"], 20)
        self.assertEqual(self.send(old, later), "SKIPPED")
        old.refresh_from_db()
        self.assertEqual(old.skip_reason, "EVENT_NO_LONGER_RELEVANT")
        self.assertEqual(NotificationLog.objects.filter(type=KIND).count(), 2)

    def test_recovered_metric_hides_banner_and_skips_queued_email(self):
        start = self.drop()
        entry = self.entry()
        at = start + timedelta(days=1)
        with patch("django.utils.timezone.now", return_value=at):
            self.lot(10, order=1002)
        # Reads hide the warning immediately, even before the tier job runs.
        self.assertFalse(tier_state(self.profile, at)["grace"]["active"])
        self.assertEqual(self.send(entry, at), "SKIPPED")
        evaluate_profile_tier(self.profile, at)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.tier_drop_since)
        self.assertEqual(check_profile_integrity(self.profile), [])

    def test_expired_grace_and_daily_downgrade_hide_banner_and_suppress_email(self):
        start = self.drop()
        entry = self.entry()
        at = start + timedelta(days=30)
        self.assertFalse(tier_state(self.profile, at)["grace"]["active"])
        self.assertEqual(tier_grace_notifications(at), 0)
        evaluate_profile_tier(self.profile, at, allow_downgrade=True)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.tier_id, self.base.pk)
        self.assertEqual(self.send(entry, at), "SKIPPED")
        self.assertEqual(check_profile_integrity(self.profile), [])

    def test_short_grace_has_one_start_message_not_two_immediate_messages(self):
        self.config_version(tier_downgrade_grace_days=3)
        start = self.drop()
        tier_grace_notifications(start)
        tier_grace_notifications(start + timedelta(days=2))
        self.assertEqual(NotificationLog.objects.filter(type=KIND).count(), 1)

    def test_disabled_tiers_zero_grace_inactive_tier_and_blocked_profiles_hide_banner(self):
        start = self.drop()
        self.profile.is_blocked = True
        self.assertFalse(tier_state(self.profile, start)["grace"]["active"])
        self.profile.is_blocked = False
        self.config_version(tier_downgrade_grace_days=0)
        self.assertFalse(tier_state(self.profile, start)["grace"]["active"])
        self.config_version(tiers_enabled=False)
        state = tier_state(self.profile, start)
        self.assertFalse(state["enabled"])
        self.assertFalse(state["grace"]["active"])

    def test_email_consent_and_opt_out_rechecked_at_delivery(self):
        start = self.drop()
        entry = self.entry()
        self.profile.email_marketing_opt_in = False
        self.profile.save(update_fields=["email_marketing_opt_in"])
        self.assertEqual(self.send(entry, start), "SKIPPED")
        entry.refresh_from_db()
        self.assertEqual(entry.skip_reason, "NO_MARKETING_CONSENT")
        self.profile.email_marketing_opt_in = True
        self.profile.email_opt_out = True
        self.profile.save(update_fields=["email_marketing_opt_in", "email_opt_out"])
        tier_grace_notifications(start + timedelta(days=23))
        near = self.entry("seven_days")
        self.assertEqual(self.send(near, start + timedelta(days=23)), "SKIPPED")
        near.refresh_from_db()
        self.assertEqual(near.skip_reason, "OPTED_OUT")
        self.assertEqual(len(mail.outbox), 0)

    def test_type_must_be_enabled_by_merchant(self):
        start = self.drop()
        self.settings.enabled_types = []
        self.settings.save()
        self.assertEqual(self.send(self.entry(), start), "SKIPPED")
        self.assertEqual(NotificationLog.objects.get(pk=self.entry().pk).skip_reason, "DISABLED")

    def test_paused_program_hides_warning_and_skips_email(self):
        start = self.drop()
        self.config_version(is_enabled=False)
        self.assertFalse(tier_state(self.profile, start)["grace"]["active"])
        self.assertEqual(tier_grace_notifications(start), 0)
        self.assertEqual(self.send(self.entry(), start), "SKIPPED")

    def test_daily_task_paginates_and_preserves_the_same_clock(self):
        from loyalty.tasks import tier_grace_notifications as daily
        start = self.drop()
        CustomerProfile.objects.bulk_create([CustomerProfile(shop=self.shop, shopify_customer_id=1000 + i,
            tier=self.gold, tier_drop_since=start) for i in range(200)])
        at = start + timedelta(days=23)
        with patch("django_q.tasks.async_task") as queued:
            result = daily(at.isoformat())
        self.assertEqual(result, {"queued": 200, "profiles": 200, "has_more": True})
        self.assertEqual(queued.call_args.args[:2], ("loyalty.tasks.tier_grace_notifications", at.isoformat()))
        cursor = queued.call_args.args[2]
        result = daily(at.isoformat(), cursor)
        self.assertEqual(result, {"queued": 1, "profiles": 1, "has_more": False})

    def test_email_en_vi_escapes_tier_and_has_unsubscribe_without_pii_in_snapshot(self):
        start = self.drop()
        self.gold.name = "<Gold>"
        self.gold.save()
        for lang, stage, at in [("en", "started", start), ("vi", "seven_days", start + timedelta(days=23))]:
            with self.subTest(lang=lang):
                self.profile.locale = lang
                self.profile.save(update_fields=["locale"])
                tier_grace_notifications(at)
                entry = self.entry(stage)
                self.assertEqual(self.send(entry, at), "SENT")
                email = mail.outbox[-1]
                self.assertIn("10", email.body)
                self.assertIn((start + timedelta(days=30)).strftime("%Y-%m-%d"), email.body)
                self.assertIn("&lt;Gold&gt;", email.alternatives[0].content)
                self.assertNotIn("<Gold>", email.alternatives[0].content)
                self.assertIn("List-Unsubscribe", email.extra_headers)
                self.assertNotIn(self.profile.email, str(entry.tier_grace_snapshot))
                self.assertEqual(self.send(entry, at), "NOOP")
        self.assertEqual(len(mail.outbox), 2)

    def test_dry_run_and_historical_evaluation_do_not_notify(self):
        start = self.drop()
        count = NotificationLog.objects.count()
        call_command("evaluate_tiers", as_of=(start + timedelta(days=23)).isoformat(), dry_run=True, stdout=StringIO())
        evaluate_profile_tier(self.profile, start + timedelta(days=1), notify_upgrade=False)
        self.assertEqual(NotificationLog.objects.count(), count)


class TierGraceConcurrencyTests(GraceSetup, TransactionTestCase):
    def test_real_orm_enqueue_can_close_connection_without_breaking_profile_scan(self):
        from loyalty.services.notifications import profile_id_batches
        start = self.drop()
        CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=302,
            tier=self.gold, tier_drop_since=start)
        broker = ORM("test-grace-reminders")
        with patch("loyalty.services.notifications.async_task",
                   side_effect=lambda *args, **kwargs: real_async_task(*args, **kwargs, broker=broker, sync=False)), \
                patch("loyalty.services.notifications.profile_id_batches",
                      side_effect=lambda query: profile_id_batches(query, batch_size=1)):
            self.assertEqual(tier_grace_notifications(start + timedelta(days=23)), 2)
            self.assertEqual(tier_grace_notifications(start + timedelta(days=23)), 0)
        self.assertEqual(OrmQ.objects.filter(key="test-grace-reminders").count(), 2)
        self.assertEqual(check_profile_integrity(self.profile), [])

    def test_two_daily_workers_queue_one_seven_day_reminder(self):
        start = self.drop()
        barrier = Barrier(2)
        def run(_):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return tier_grace_notifications(start + timedelta(days=23))
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(run, [1, 2])), 1)
        self.assertEqual(NotificationLog.objects.filter(type=KIND, tier_grace_snapshot__stage="seven_days").count(), 1)
        self.assertEqual(check_profile_integrity(self.profile), [])
