from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from threading import Barrier
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings

from loyalty.models import CustomerProfile, PointsLot, PointsTransaction, Redemption, Reward
from loyalty.services.integrity import check_profile_integrity, check_profile_warnings
from loyalty.services.lifecycle import run_lifecycle_services
from loyalty.services.redemption import (ALPHABET, discount_matches, expected_discount, generate_code,
    issue_redemption, redeem_reward, refund_redemption_points, save_redemption)
from loyalty.services.redemption_jobs import (close_issued, mark_codes_used, process_redemptions_services,
                                             redemption_plan)
from loyalty.tasks import process_redemptions
from shops.shopify import DiscountCodeTaken, ShopifyError, ShopifyTransientError
from webhooks.handlers import orders_paid
from webhooks.models import WebhookEvent
from webhooks.tasks import MAX_ATTEMPTS
from .lifecycle_fixtures import PointsSetup
from .test_revoking import RevokeTestSetup


def found_discount(row):
    expected = expected_discount(row)
    return {"id": "gid://shopify/DiscountCodeNode/123", "settings": {
        **expected, "usage_limit": 1, "applies_once": True, "all_items": True,
        "one_time": True, "subscription": False, "applies_on_each_item": False,
        "minimum_currency": expected["currency"] if expected["min_order_amount"] is not None else None,
        "combines_with": {"orderDiscounts": False, "productDiscounts": False, "shippingDiscounts": False}}}


class RedemptionSetup(PointsSetup):
    def setUp(self):
        super().setUp()
        self.find = patch("loyalty.services.redemption.find_discount_by_code", return_value=None).start()
        self.create = patch("loyalty.services.redemption.create_discount_code", return_value="gid://shopify/DiscountCodeNode/123").start()
        self.deactivate = patch("loyalty.services.redemption_jobs.deactivate_discount", return_value=True).start()
        self.job_find = patch("loyalty.services.redemption_jobs.find_discount_by_code", return_value=None).start()

    def redeem(self, key="one", now=None, profile=None, reward=None):
        return redeem_reward(self.shop, (profile or self.profile).shopify_customer_id,
                             (reward or self.reward).pk, key, now or self.now)

    def pending(self, key="one"):
        self.create.side_effect = ShopifyTransientError("timeout")
        result = self.redeem(key)
        self.assertEqual(result.status, "PENDING")
        return Redemption.objects.get(pk=result.redemption_id)

    def expired(self):
        row = Redemption.objects.get(pk=self.redeem().redemption_id)
        at = row.code_expires_at + timedelta(hours=1)
        self.assertEqual(close_issued(row.pk, "EXPIRED", at), "expired")
        row.refresh_from_db()
        return row, at


class RedemptionTests(RedemptionSetup, TransactionTestCase):
    def test_happy_fefo_partial_null_last_snapshot_and_frozen_settings(self):
        null = self.lot(5)
        later = self.lot(8, expires=self.now + timedelta(days=4))
        early = self.lot(6, expires=self.now + timedelta(days=2))
        result = self.redeem()
        self.assertEqual((result.status, result.reason_code), ("ISSUED", "ISSUED"))
        row = Redemption.objects.get(pk=result.redemption_id)
        entry = PointsTransaction.objects.get(type="REDEEM")
        self.assertEqual((entry.delta, entry.balance_after, entry.lot_id, entry.order_id), (-10, 9, None, None))
        self.assertEqual(entry.idempotency_key, row.idempotency_key)
        self.assertEqual([x["lot_id"] for x in entry.rule_snapshot["lot_allocations"]], [early.pk, later.pk])
        self.assertEqual([x["points"] for x in entry.rule_snapshot["lot_allocations"]], [6, 4])
        early.refresh_from_db(); later.refresh_from_db(); null.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((early.status, early.points_remaining, later.points_remaining, null.points_remaining), ("CONSUMED", 0, 4, 5))
        self.assertEqual(self.profile.lifetime_earned, 19)
        self.assertTrue({"reward_id", "reward_name", "program_config_id", "program_config_version", "points_cost", "now", "discount_settings"} <= entry.rule_snapshot.keys())
        self.assertNotIn("email", entry.rule_snapshot)
        self.assertEqual(len(result.code), 13)
        self.reward.discount_value = 99
        self.reward.save()
        self.assertEqual(Decimal(expected_discount(row)["discount_value"]), Decimal("5"))
        self.clean_ledger()

    def test_code_alphabet(self):
        for _ in range(30):
            code = generate_code()
            self.assertTrue(code.startswith("LP-"))
            self.assertEqual(len(code), 13)
            self.assertTrue(set(code[3:]) <= set(ALPHABET))
            self.assertFalse(set(code[3:]) & set("01OIL"))

    def test_idempotency_and_key_identity(self):
        self.lot(30)
        first, second = self.redeem(), self.redeem()
        self.assertEqual(first, second)
        self.assertEqual(self.create.call_count, 1)
        self.assertEqual(PointsTransaction.objects.filter(type="REDEEM").count(), 1)
        other = CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=302)
        self.assertEqual(self.redeem(profile=other).reason_code, "KEY_REUSED")
        other_reward = Reward.objects.create(shop=self.shop, name="Other", points_cost=1, discount_type="PERCENTAGE", discount_value=10)
        self.assertEqual(self.redeem(reward=other_reward).reason_code, "KEY_REUSED")
        self.clean_ledger()

    def test_transient_retry_same_code_and_no_double_deduction(self):
        self.lot()
        row = self.pending()
        code = row.discount_code
        self.assertEqual(row.attempts, 1)
        self.assertEqual(self.redeem().status, "PENDING")
        self.create.side_effect = None
        result = process_redemptions_services(self.now + timedelta(minutes=1))
        row.refresh_from_db()
        self.assertEqual((result.issued, row.status, row.discount_code), (1, "ISSUED", code))
        self.assertEqual(PointsTransaction.objects.filter(type="REDEEM").count(), 1)
        self.assertEqual(self.create.call_args.args[0], code)
        self.clean_ledger()

    def test_timeout_after_remote_create_adopts_matching_code(self):
        self.lot()
        row = self.pending()
        self.find.return_value = found_discount(row)
        self.assertEqual(issue_redemption(row.pk, self.now + timedelta(minutes=1)).status, "ISSUED")
        self.assertEqual(self.create.call_count, 1)
        self.clean_ledger()

    def test_collision_rotates_up_to_three_then_refunds(self):
        self.lot(20)
        self.create.side_effect = [DiscountCodeTaken("taken"), "gid://shopify/DiscountCodeNode/123"]
        result = self.redeem()
        self.assertEqual(result.status, "ISSUED")
        self.assertNotEqual(self.create.call_args_list[0].args[0], result.code)
        close_issued(result.redemption_id, "CANCELLED", self.now)
        self.create.reset_mock()
        self.create.side_effect = DiscountCodeTaken("taken")
        result = self.redeem("two")
        self.assertEqual(result.reason_code, "ISSUE_FAILED")
        self.assertEqual(self.create.call_count, 3)
        self.clean_ledger()

    def test_permanent_failure_atomic_refund_and_replay(self):
        self.lot(expires=self.now + timedelta(days=3))
        self.create.side_effect = ShopifyError("remote secret must not appear")
        result = self.redeem()
        row = Redemption.objects.get(pk=result.redemption_id)
        self.assertEqual((row.status, result.reason_code), ("FAILED", "ISSUE_FAILED"))
        self.assertIsNone(result.code)
        self.assertNotIn("secret", row.last_error)
        self.assertEqual(self.redeem().reason_code, "ISSUE_FAILED")
        self.assertFalse(refund_redemption_points(row, "ISSUE_FAILED", self.now))
        self.assertEqual(PointsTransaction.objects.filter(type="REDEEM_REFUND").count(), 1)
        self.clean_ledger()

    def test_exhaustion_and_six_hour_reconciliation(self):
        for attempts, age, found in [(5, 1, False), (1, 7, False), (5, 1, True)]:
            with self.subTest(attempts=attempts, age=age, found=found):
                self.lot()
                row = self.pending(f"{attempts}-{age}-{found}")
                at = self.now + timedelta(hours=age)
                save_redemption(row, self.now, attempts=attempts)
                self.job_find.return_value = found_discount(row) if found else None
                result = process_redemptions_services(at)
                row.refresh_from_db()
                self.assertEqual(row.status, "ISSUED" if found else "FAILED")
                self.assertEqual(result.errors, 0)
                if found:
                    close_issued(row.pk, "CANCELLED", at)
                self.assertEqual(process_redemptions_services(at).processed, 0)
                self.clean_ledger()

    def test_match_rejects_changed_customer_value_window_and_scope(self):
        self.lot()
        row = self.pending()
        expected = expected_discount(row)
        self.assertTrue(discount_matches(found_discount(row), expected))
        for field, value in [("customer_id", "gid://shopify/Customer/999"), ("discount_value", "99"),
                             ("usage_limit", 2), ("all_items", False), ("ends_at", None), ("subscription", True)]:
            found = found_discount(row)
            found["settings"][field] = value
            self.assertFalse(discount_matches(found, expected), field)

    def test_refund_expiry_future_passed_null_and_debt(self):
        for index, (name, original, offset) in enumerate([("future", self.now + timedelta(days=10), 0),
                                        ("passed", self.now - timedelta(days=1), 0),
                                        ("null", None, 0), ("debt", None, 12)]):
            with self.subTest(name=name):
                self.profile = CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=500 + index)
                self.lot(expires=original)
                row = Redemption.objects.get(pk=self.redeem(name).redemption_id)
                if offset:
                    self.debt(offset)
                at = self.now + timedelta(hours=1)
                close_issued(row.pk, "CANCELLED", at)
                lot = PointsLot.objects.get(profile=self.profile, source_type="REFUND_BACK")
                expiry = (at + timedelta(hours=24) if original <= at else original) if original else None
                self.assertEqual(lot.expires_at, expiry)
                self.assertEqual(lot.points_remaining, max(0, 10 - offset))
                self.assertEqual(lot.status, "CONSUMED" if offset else "AVAILABLE")
                row.refresh_from_db()
                self.assertFalse(refund_redemption_points(row, "CANCELLED", at))
                self.clean_ledger()

    def test_expiry_grace_conditional_refund_deactivate_failure(self):
        self.lot(20)
        row = Redemption.objects.get(pk=self.redeem().redemption_id)
        at = row.code_expires_at + timedelta(minutes=60)
        self.assertEqual(process_redemptions_services(at - timedelta(seconds=1)).processed, 0)
        self.deactivate.side_effect = ShopifyTransientError("timeout")
        self.assertEqual(process_redemptions_services(at).errors, 1)
        row.refresh_from_db()
        self.assertEqual(row.status, "ISSUED")
        self.assertFalse(PointsTransaction.objects.filter(type="REDEEM_REFUND").exists())
        self.deactivate.side_effect = None
        self.config_version(refund_points_on_expired_code=False)
        self.assertEqual(process_redemptions_services(at).expired, 1)
        self.assertFalse(PointsTransaction.objects.filter(type="REDEEM_REFUND").exists())
        self.clean_ledger()

    def test_pending_paid_event_defers_expiry_and_cancellation(self):
        self.lot()
        row = Redemption.objects.get(pk=self.redeem().redemption_id)
        event = WebhookEvent.objects.create(shop=self.shop, shop_domain=self.shop.domain, topic="orders/paid",
            webhook_id="pending-paid", payload={"id": 1001, "discount_codes": [{"code": row.discount_code.lower()}]})
        at = row.code_expires_at + timedelta(hours=1)
        for state, attempts, expected in [("RECEIVED", 0, "deferred"), ("PROCESSING", 0, "deferred"),
                ("FAILED", MAX_ATTEMPTS - 1, "deferred"), ("FAILED", MAX_ATTEMPTS, "expired")]:
            event.status, event.attempts = state, attempts
            event.save()
            self.assertEqual(close_issued(row.pk, "EXPIRED", at), expected)
        self.assertEqual(self.deactivate.call_count, 1)
        self.clean_ledger()

    def test_mark_used_before_skipped_earn_and_replay(self):
        self.lot()
        row = Redemption.objects.get(pk=self.redeem().redemption_id)
        event = WebhookEvent(shop=self.shop, shop_domain=self.shop.domain, topic="orders/paid", payload={"id": 1001,
            "discount_codes": [{"code": row.discount_code.lower()}, {"code": "unknown"}], "email": "never use"})
        from loyalty.services.earning import EarnOutcome
        def skipped(*args):
            self.assertEqual(Redemption.objects.get(pk=row.pk).status, "USED")
            return EarnOutcome("SKIPPED", "test")
        with patch("webhooks.handlers.earn_points_for_order", side_effect=skipped):
            orders_paid(event)
        self.assertEqual(event.status, "SKIPPED")
        self.assertEqual(mark_codes_used(self.shop, 1001, event.payload, self.now), 0)
        with self.assertLogs("loyalty.services.redemption_jobs", level="WARNING"):
            self.assertEqual(mark_codes_used(self.shop, 1002, event.payload, self.now), 0)
        row.refresh_from_db()
        self.assertFalse(refund_redemption_points(row, "NO", self.now))
        self.clean_ledger()

    def test_late_used_after_expired_refund_warns_and_preserves_ledger(self):
        self.lot()
        row, at = self.expired()
        with self.assertLogs("loyalty.services.redemption_jobs", level="WARNING") as captured:
            mark_codes_used(self.shop, 1001, {"discount_codes": [{"code": row.discount_code}]}, at)
        self.assertNotIn(row.discount_code, str(captured.output))
        self.assertIn("redemption_used_after_refund", check_profile_warnings(self.profile, at))
        self.clean_ledger()

    def test_integrity_detects_missing_or_duplicate_refund(self):
        self.lot(30)
        row = Redemption.objects.get(pk=self.redeem().redemption_id)
        save_redemption(row, self.now, status="FAILED")
        self.assertIn("redemption_net_mismatch", check_profile_integrity(self.profile))
        refund_redemption_points(row, "FAIL", self.now)
        self.clean_ledger()
        # A different key can bypass key uniqueness; integrity must still detect it.
        PointsTransaction.objects.create(profile=self.profile, type="REDEEM_REFUND", delta=10,
            balance_after=40, redemption=row, idempotency_key="corrupt-second-refund")
        self.assertIn("redemption_entries_invalid", check_profile_integrity(self.profile))

    @override_settings(SHOPIFY_SHOP_DOMAIN="test.myshopify.com")
    def test_commands_dry_runs_redeem_seed_and_schedules(self):
        self.lot(20)
        output = StringIO()
        call_command("redeem", customer_id=301, reward_id=self.reward.pk, key="cli", as_of=self.now.isoformat(), stdout=output)
        self.assertIn("ISSUED", output.getvalue())
        row = Redemption.objects.get(idempotency_key="redeem:cli")
        call_command("cancel_redemption", id=row.pk, stdout=StringIO())
        self.deactivate.assert_not_called()
        call_command("cancel_redemption", id=row.pk, yes=True, stdout=StringIO())
        self.assertEqual(self.deactivate.call_count, 1)
        self.pending("pending-cli")
        before = list(Redemption.objects.values())
        call_command("process_redemptions", as_of=(self.now + timedelta(minutes=5)).isoformat(), dry_run=True, stdout=StringIO())
        self.assertEqual(before, list(Redemption.objects.values()))
        call_command("seed_dev_rewards", stdout=StringIO())
        rewards = list(Reward.objects.values())
        call_command("seed_dev_rewards", stdout=StringIO())
        self.assertEqual(rewards, list(Reward.objects.values()))
        call_command("setup_schedules", stdout=StringIO())
        call_command("setup_schedules", stdout=StringIO())
        from django_q.models import Schedule
        self.assertEqual(Schedule.objects.get(func="loyalty.tasks.run_lifecycle").minutes, 5)
        self.assertEqual(Schedule.objects.get(func="loyalty.tasks.process_redemptions").minutes, 1)
        self.clean_ledger()

    def test_batch_error_isolation_limit_and_task(self):
        self.lot(30)
        self.config_version(max_redemptions_per_day=10, max_unused_codes_per_reward=10)
        one, two = self.pending("one"), self.pending("two")
        with patch("loyalty.services.redemption_jobs.issue_redemption", side_effect=[RuntimeError("bad"), self.redeem("one")]), self.assertLogs("loyalty.services.redemption_jobs", level="ERROR"):
            result = process_redemptions_services(self.now + timedelta(minutes=1))
        self.assertEqual((result.processed, result.errors), (2, 1))
        self.assertEqual(len(redemption_plan(self.now + timedelta(minutes=1), limit=1)), 1)
        self.create.side_effect = None
        self.assertEqual(process_redemptions((self.now + timedelta(minutes=1)).isoformat())["issued"], 2)
        self.clean_ledger()

    def test_deactivation_races_paid_webhook_without_refunding_used_code(self):
        self.lot()
        row = Redemption.objects.get(pk=self.redeem().redemption_id)
        def paid(_):
            mark_codes_used(self.shop, 1001, {"discount_codes": [{"code": row.discount_code}]}, self.now)
        self.deactivate.side_effect = paid
        self.assertEqual(close_issued(row.pk, "EXPIRED", row.code_expires_at + timedelta(hours=1)), "skipped")
        self.assertFalse(PointsTransaction.objects.filter(type="REDEEM_REFUND").exists())
        self.clean_ledger()

    def test_expiry_guard_rechecked_after_api(self):
        self.lot()
        row = Redemption.objects.get(pk=self.redeem().redemption_id)
        def received(_):
            WebhookEvent.objects.create(shop=self.shop, shop_domain=self.shop.domain, topic="orders/paid",
                webhook_id="during-deactivation", payload={"id": 1001, "discount_codes": [{"code": row.discount_code}]})
        self.deactivate.side_effect = received
        self.assertEqual(close_issued(row.pk, "EXPIRED", row.code_expires_at + timedelta(hours=1)), "deferred")
        self.assertFalse(PointsTransaction.objects.filter(type="REDEEM_REFUND").exists())
        self.clean_ledger()

    def test_refund_rolls_back_with_ledger_failure(self):
        self.lot()
        row = Redemption.objects.get(pk=self.redeem().redemption_id)
        before = PointsLot.objects.count()
        with patch("loyalty.services.redemption.PointsTransaction.objects.create", side_effect=RuntimeError("fixture failure")):
            with self.assertRaises(RuntimeError):
                close_issued(row.pk, "CANCELLED", self.now)
        row.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((row.status, self.profile.available_balance, PointsLot.objects.count()), ("ISSUED", 0, before))
        close_issued(row.pk, "CANCELLED", self.now)
        self.clean_ledger()

    def test_percentage_and_minimum_are_frozen_as_fraction(self):
        self.lot()
        Reward.objects.filter(pk=self.reward.pk).update(discount_type="PERCENTAGE", discount_value=10, min_order_amount=20)
        self.assertEqual(self.redeem().status, "ISSUED")
        settings = self.create.call_args.args[1]
        self.assertEqual((Decimal(settings["discount_value"]), Decimal(settings["min_order_amount"])), (Decimal("0.10"), Decimal("20")))
        self.clean_ledger()

    def test_sweeper_backoff_boundaries_and_warnings(self):
        self.lot()
        row = self.pending()
        for attempt, delay in [(1, 1), (2, 5), (3, 15), (4, 60)]:
            save_redemption(row, self.now, attempts=attempt)
            self.assertFalse(redemption_plan(self.now + timedelta(minutes=delay) - timedelta(seconds=1)))
            self.assertEqual(redemption_plan(self.now + timedelta(minutes=delay)), [(row.pk, "retry")])
        self.assertIn("redemption_pending_overdue", check_profile_warnings(self.profile, self.now + timedelta(hours=2)))
        self.assertEqual(redemption_plan(self.now + timedelta(hours=2), limit=0), [])
        self.clean_ledger()

    def test_lifecycle_after_redeem_expires_only_remaining(self):
        self.lot(20, expires=self.now + timedelta(days=2))
        self.redeem()
        result = run_lifecycle_services(self.now + timedelta(days=2))
        self.assertEqual(result.expired_points, 10)
        self.clean_ledger()

    def test_mixed_expiry_refund_preserves_each_allocation_expiry(self):
        self.lot(4, expires=self.now + timedelta(days=3))
        self.lot(6)
        row = Redemption.objects.get(pk=self.redeem().redemption_id)
        close_issued(row.pk, "CANCELLED", self.now)
        self.assertEqual(list(PointsLot.objects.filter(source_type="REFUND_BACK").order_by("pk")
            .values_list("points_remaining", "expires_at")), [(4, self.now + timedelta(days=3)), (6, None)])
        self.clean_ledger()


class RejectionTests(RedemptionSetup, TransactionTestCase):
    def assert_rejected(self, code):
        before = (PointsTransaction.objects.count(), Redemption.objects.count())
        self.find.reset_mock(); self.create.reset_mock()
        self.assertEqual(self.redeem().reason_code, code)
        self.assertEqual(before, (PointsTransaction.objects.count(), Redemption.objects.count()))
        self.find.assert_not_called(); self.create.assert_not_called()

    def test_program_block_anon_inactive_window_and_unsupported(self):
        self.lot(10)
        self.config_version(is_enabled=False)
        self.profile.is_blocked = self.profile.is_anonymized = True
        self.profile.save()
        self.assert_rejected("PROGRAM_DISABLED")
        self.config_version(is_enabled=True)
        self.assert_rejected("CUSTOMER_BLOCKED")
        self.profile.is_blocked = False; self.profile.save()
        self.assert_rejected("CUSTOMER_ANONYMIZED")
        self.profile.is_anonymized = False; self.profile.save()
        for changes in [{"is_active": False}, {"is_active": True, "active_from": self.now + timedelta(seconds=1)},
                        {"active_from": None, "active_to": self.now}]:
            Reward.objects.filter(pk=self.reward.pk).update(**changes)
            self.assert_rejected("REWARD_INACTIVE")
        Reward.objects.filter(pk=self.reward.pk).update(active_to=None, discount_type="PERCENTAGE", max_discount_amount=5)
        self.assert_rejected("UNSUPPORTED_REWARD")
        self.clean_ledger()

    def test_insufficient_pending_and_negative(self):
        self.lot(9)
        self.lot(100, status="PENDING")
        self.assert_rejected("INSUFFICIENT_POINTS")
        self.debt(12)
        self.assert_rejected("INSUFFICIENT_POINTS")

    def test_limits_and_rolling_boundary(self):
        self.lot(100)
        row = Redemption.objects.get(pk=self.redeem("first").redemption_id)
        self.assert_rejected("UNUSED_CODES_LIMIT")
        self.config_version(max_redemptions_per_day=1)
        self.assert_rejected("DAILY_LIMIT")
        self.config_version(max_redemptions_per_day=10, max_unused_codes_per_reward=10)
        Reward.objects.filter(pk=self.reward.pk).update(stock_limit=1)
        self.assert_rejected("STOCK_EXHAUSTED")
        Reward.objects.filter(pk=self.reward.pk).update(stock_limit=None, per_customer_limit=1)
        self.assert_rejected("CUSTOMER_LIMIT")
        Reward.objects.filter(pk=self.reward.pk).update(per_customer_limit=None)
        self.config_version(max_redemptions_per_day=1)
        row.created_at = self.now - timedelta(hours=24)
        save_redemption(row, self.now, created_at=row.created_at)
        self.assertEqual(self.redeem().status, "ISSUED")
        self.clean_ledger()

    def test_counted_expired_refunded_does_not_hold_stock(self):
        self.lot(30)
        Reward.objects.filter(pk=self.reward.pk).update(stock_limit=1)
        row, at = self.expired()
        self.assertEqual(self.redeem("next", now=at).status, "ISSUED")
        self.clean_ledger()


class RedemptionConcurrencyTests(RedemptionSetup, TransactionTestCase):
    def parallel(self, keys, profiles=None):
        barrier = Barrier(len(keys))
        def worker(item):
            key, profile = item
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return self.redeem(key, profile=profile)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(keys)) as pool:
            return list(pool.map(worker, zip(keys, profiles or [self.profile] * len(keys))))

    def test_remote_calls_outside_transactions(self):
        self.lot()
        def create(*args):
            self.assertFalse(connection.in_atomic_block)
            self.assertFalse(connection.get_autocommit() is False)
            return "gid://shopify/DiscountCodeNode/123"
        self.create.side_effect = create
        self.find.side_effect = lambda code: create() and None
        self.assertEqual(self.redeem().status, "ISSUED")
        self.deactivate.side_effect = lambda _: create()
        close_issued(Redemption.objects.get().pk, "CANCELLED", self.now)
        self.clean_ledger()

    def test_same_key_never_double_deducts(self):
        self.lot(20)
        results = self.parallel(["same", "same"])
        self.assertTrue(all(result.status in ("ISSUED", "PENDING") for result in results))
        self.assertEqual(Redemption.objects.count(), 1)
        self.assertEqual(PointsTransaction.objects.filter(type="REDEEM").count(), 1)
        self.clean_ledger()

    def test_different_keys_cannot_overspend(self):
        self.lot(10)
        results = self.parallel(["one", "two"])
        self.assertEqual(sorted(result.status for result in results), ["ISSUED", "REJECTED"])
        self.assertEqual(Redemption.objects.count(), 1)
        self.clean_ledger()

    def test_stock_across_profiles(self):
        self.lot(10)
        other = CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=999)
        self.lot(10, profile=other)
        Reward.objects.filter(pk=self.reward.pk).update(stock_limit=1)
        results = self.parallel(["one", "two"], [self.profile, other])
        self.assertEqual(sorted(result.status for result in results), ["ISSUED", "REJECTED"])
        self.assertEqual(Redemption.objects.count(), 1)
        self.clean_ledger()

    def test_global_key_collision_across_profiles(self):
        self.lot(10)
        other = CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=999)
        self.lot(10, profile=other)
        results = self.parallel(["same", "same"], [self.profile, other])
        self.assertIn("KEY_REUSED", [result.reason_code for result in results])
        self.assertEqual(Redemption.objects.count(), 1)
        self.clean_ledger()

    def test_two_cancellations_refund_once(self):
        self.lot()
        row = Redemption.objects.get(pk=self.redeem().redemption_id)
        barrier = Barrier(2)
        def cancel(_):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return close_issued(row.pk, "CANCELLED", self.now)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(cancel, range(2)))
        self.assertEqual(sorted(results), ["cancelled", "skipped"])
        self.assertEqual(PointsTransaction.objects.filter(type="REDEEM_REFUND").count(), 1)
        self.clean_ledger()


class RedemptionInteractionTests(RevokeTestSetup, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.reward = Reward.objects.create(shop=self.shop, name="Reward", points_cost=10,
            discount_type="FIXED_AMOUNT", discount_value=50000)
        patch("loyalty.services.redemption.find_discount_by_code", return_value=None).start()
        patch("loyalty.services.redemption.create_discount_code", return_value="gid://shopify/DiscountCodeNode/123").start()

    def earn_activate_redeem(self):
        lot = self.earn()
        run_lifecycle_services(lot.available_at)
        result = redeem_reward(self.shop, 301, self.reward.pk, "interaction", lot.available_at)
        self.assertEqual(result.status, "ISSUED")
        self.assert_integrity()
        return Redemption.objects.get(pk=result.redemption_id)

    def test_earn_activation_redeem_then_revoke_creates_debt(self):
        self.earn_activate_redeem()
        self.cancel()
        from loyalty.services.revoking import revoke_points_for_cancel
        revoke_points_for_cancel(self.shop, 1001)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.available_balance, -10)
        self.assert_integrity()

    def test_cancel_order_using_reward_never_refunds_redemption(self):
        row = self.earn_activate_redeem()
        mark_codes_used(self.shop, 1002, {"discount_codes": [{"code": row.discount_code}]}, self.now)
        lot = self.earn(1002)
        from shops.tests.fixtures import raw_order
        from loyalty.services.revoking import revoke_points_for_cancel
        raw = raw_order(1002)
        raw["cancelledAt"] = self.now.isoformat()
        with patch("loyalty.services.revoking.fetch_order", return_value=raw):
            revoke_points_for_cancel(self.shop, 1002)
        row.refresh_from_db()
        self.assertEqual(row.status, "USED")
        self.assertFalse(PointsTransaction.objects.filter(type="REDEEM_REFUND").exists())
        self.assert_integrity()
