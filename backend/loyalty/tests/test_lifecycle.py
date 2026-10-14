from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connections
from django.test import TestCase, TransactionTestCase

from loyalty.models import AuditLog, PointsLot, PointsTransaction
from loyalty.services import lifecycle
from loyalty.services.integrity import check_profile_integrity, check_profile_warnings
from loyalty.services.lifecycle import activate_due_lots, expire_due_lots, run_lifecycle_services
from loyalty.tasks import run_lifecycle
from .lifecycle_fixtures import PointsSetup
from .test_revoking import RevokeTestSetup


class LifecycleTests(PointsSetup, TestCase):
    def test_activation_exact_boundary_and_no_ledger_or_audit(self):
        lot = self.lot(status="PENDING")
        future = self.lot(status="PENDING", available=self.now + timedelta(seconds=1))
        result = activate_due_lots(self.now)
        self.assertEqual((result.processed, result.activated), (1, 1))
        lot.refresh_from_db()
        self.assertEqual(lot.status, "AVAILABLE")
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.available_balance, self.profile.pending_balance), (10, 10))
        self.assertEqual(PointsTransaction.objects.count(), 2)
        self.assertEqual(AuditLog.objects.count(), 0)
        self.assertEqual(activate_due_lots(self.now).processed, 0)
        self.clean_ledger()

    def test_debt_offset_partial_and_full(self):
        for debt, balance, remaining, status in [(5, 3, 3, "AVAILABLE"), (12, -4, 0, "CONSUMED")]:
            with self.subTest(debt=debt):
                self.profile.available_balance = self.profile.pending_balance = 0
                # Separate profiles keep ledger histories independent.
                from loyalty.models import CustomerProfile
                self.profile = CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=400 + debt)
                self.debt(debt)
                lot = self.lot(8, status="PENDING")
                result = activate_due_lots(self.now)
                self.profile.refresh_from_db()
                lot.refresh_from_db()
                self.assertEqual((self.profile.available_balance, lot.points_remaining, lot.status), (balance, remaining, status))
                self.assertEqual(result.offset_total, min(debt, 8))
                self.assertEqual(AuditLog.objects.get(target_id=str(lot.pk)).after["offset"], min(debt, 8))
                self.clean_ledger()

    def test_both_due_expires_pending_without_activation(self):
        lot = self.lot(status="PENDING", expires=self.now)
        result = run_lifecycle_services(self.now)
        self.assertEqual((result.activated, result.expired, result.expired_points), (0, 1, 10))
        entry = PointsTransaction.objects.get(type="EXPIRE")
        self.assertEqual((entry.delta, entry.balance_after, entry.lot_id), (-10, 0, lot.pk))
        self.assertEqual(entry.rule_snapshot["lot_status_before"], "PENDING")
        self.assertEqual(run_lifecycle_services(self.now).processed, 0)
        self.clean_ledger()

    def test_partial_expiry_null_zero_and_lifetime(self):
        lot = self.lot(10, expires=self.now)
        self.lot(3)
        lot.points_remaining = 4
        lot.save()
        self.profile.refresh_from_db()
        self.profile.available_balance -= 6
        self.profile.save()
        PointsTransaction.objects.create(profile=self.profile, type="REDEEM", delta=-6,
            balance_after=7, idempotency_key="fixture-spend")
        self.assertEqual(expire_due_lots(self.now).expired_points, 4)
        self.assertEqual(expire_due_lots(self.now).processed, 0)
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.available_balance, self.profile.lifetime_earned), (3, 13))
        self.clean_ledger()

    def test_pending_expires_before_activation(self):
        self.lot(status="PENDING", available=self.now + timedelta(days=1), expires=self.now)
        self.assertEqual(run_lifecycle_services(self.now).expired, 1)
        self.clean_ledger()

    def test_total_batch_limit_and_error_isolation(self):
        ids = [self.lot(status="PENDING").pk for _ in range(4)]
        original = lifecycle._process_lot
        def process(lot_id, *args):
            if lot_id == ids[0]:
                raise ValueError("bad fixture")
            return original(lot_id, *args)
        with patch.object(lifecycle, "_process_lot", side_effect=process), self.assertLogs("loyalty.services.lifecycle", level="ERROR"):
            result = run_lifecycle_services(self.now, 3)
        self.assertEqual((result.processed, result.activated, result.errors), (3, 2, 1))
        self.assertEqual(run_lifecycle_services(self.now, 1).activated, 1)
        self.assertEqual(run_lifecycle_services(self.now, 1).activated, 1)
        self.clean_ledger()

    def test_commands_dry_run_as_of_and_invalid_time(self):
        lot = self.lot(status="PENDING", available=self.now + timedelta(days=7), expires=self.now + timedelta(days=30))
        at = (self.now + timedelta(days=7)).isoformat()
        out = StringIO()
        call_command("run_lifecycle", as_of=at, dry_run=True, stdout=out)
        lot.refresh_from_db()
        self.assertEqual(lot.status, "PENDING")
        self.assertIn(str(lot.pk), out.getvalue())
        call_command("run_lifecycle", as_of=at, stdout=StringIO())
        lot.refresh_from_db()
        self.assertEqual(lot.status, "AVAILABLE")
        result = run_lifecycle((self.now + timedelta(days=30)).isoformat())
        self.assertEqual(result["expired"], 1)
        for value in ["bad", "2026-10-12T12:00:00"]:
            with self.assertRaises(CommandError):
                call_command("run_lifecycle", as_of=value, stdout=StringIO())
        self.clean_ledger()

    def test_failed_both_due_lot_is_not_retried_in_same_run(self):
        broken = self.lot(status="PENDING", expires=self.now)
        self.lot(expires=self.now)
        original = lifecycle._process_lot
        def process(lot_id, *args):
            if lot_id == broken.pk:
                raise ValueError("bad fixture")
            return original(lot_id, *args)
        with patch.object(lifecycle, "_process_lot", side_effect=process), self.assertLogs("loyalty.services.lifecycle", level="ERROR"):
            result = run_lifecycle_services(self.now, 500)
        self.assertEqual((result.processed, result.errors, result.expired), (2, 1, 1))
        self.clean_ledger()

    def test_warnings_do_not_fail_but_expired_remaining_does(self):
        lot = self.lot(status="PENDING", available=self.now - timedelta(days=2), expires=self.now - timedelta(days=2))
        self.assertEqual(set(check_profile_warnings(self.profile, self.now)), {"activation_overdue", "expiry_overdue"})
        call_command("check_ledger", stdout=StringIO())
        lot.status = "EXPIRED"
        lot.save()
        self.assertIn("expired_lot_has_points", check_profile_integrity(self.profile))

    def test_zero_remaining_expiry_is_ignored(self):
        lot = self.lot(expires=self.now)
        lot.points_remaining, lot.status = 0, "CONSUMED"
        lot.save()
        self.assertEqual(expire_due_lots(self.now).processed, 0)


class LifecycleInteractionTests(RevokeTestSetup, TestCase):
    def test_earn_activation_then_revoke(self):
        lot = self.earn()
        self.assertEqual(run_lifecycle_services(lot.available_at).activated, 1)
        self.cancel()
        from loyalty.services.revoking import revoke_points_for_cancel
        revoke_points_for_cancel(self.shop, 1001)
        self.assert_integrity()

    def test_revoke_after_expiry_uses_debt(self):
        lot = self.earn()
        run_lifecycle_services(lot.expires_at)
        self.cancel()
        from loyalty.services.revoking import revoke_points_for_cancel
        revoke_points_for_cancel(self.shop, 1001)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.available_balance, -10)
        self.assert_integrity()


class LifecycleConcurrencyTests(PointsSetup, TransactionTestCase):
    def parallel(self, functions):
        barrier = Barrier(len(functions))
        def worker(fn):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return fn()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(functions)) as pool:
            return list(pool.map(worker, functions))

    def test_two_activations_and_two_expiries(self):
        lot = self.lot(status="PENDING", expires=self.now + timedelta(days=1))
        results = self.parallel([lambda: activate_due_lots(self.now)] * 2)
        self.assertEqual(sum(result.activated for result in results), 1)
        results = self.parallel([lambda: expire_due_lots(self.now + timedelta(days=1))] * 2)
        self.assertEqual(sum(result.expired for result in results), 1)
        self.assertEqual(PointsTransaction.objects.filter(type="EXPIRE").count(), 1)
        self.clean_ledger()

    def test_activation_races_with_revocation(self):
        from copy import deepcopy
        from shops.tests.fixtures import raw_order
        from loyalty.services.earning import earn_points_for_order
        from loyalty.services.revoking import revoke_points_for_cancel
        # Earn service has the complete rule snapshot required by revoke.
        self.shop.currency = "VND"
        self.shop.save()
        self.config_version(spend_per_point=10000)
        raw = raw_order()
        with patch("loyalty.services.earning.fetch_order", return_value=deepcopy(raw)):
            earn_points_for_order(self.shop, 1001)
        lot = PointsLot.objects.get(source_order_id=1001)
        raw["cancelledAt"] = self.now.isoformat()
        with patch("loyalty.services.revoking.fetch_order", return_value=raw):
            results = self.parallel([lambda: activate_due_lots(lot.available_at), lambda: revoke_points_for_cancel(self.shop, 1001)])
        self.assertEqual(results[0].errors, 0)
        self.clean_ledger()
