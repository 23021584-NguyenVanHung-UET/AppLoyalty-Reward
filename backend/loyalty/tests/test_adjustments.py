from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.db import close_old_connections, connections
from django.test import TestCase, TransactionTestCase, override_settings

from loyalty.models import AuditLog, CustomerProfile, PointsLot, PointsTransaction
from loyalty.services.adjustments import adjust_points
from loyalty.services.errors import LoyaltyError
from .lifecycle_fixtures import PointsSetup


class AdjustmentTests(PointsSetup, TestCase):
    def adjust(self, delta=10, key="test", **kwargs):
        return adjust_points(self.profile, delta, "GIFT", "Test adjustment", "shopify_user:123", key,
                             now=self.now, **kwargs)

    def test_positive_expiry_audit_and_integrity(self):
        result = self.adjust()
        self.assertEqual(result["available_balance"], 10)
        lot = PointsLot.objects.get(source_type="ADJUSTMENT")
        self.assertEqual(lot.expires_at, self.now+timedelta(days=30))
        entry = PointsTransaction.objects.get(type="ADJUST")
        self.assertEqual(entry.actor, "shopify_user:123")
        self.assertEqual(entry.rule_snapshot["config_version"], 1)
        self.assertEqual(AuditLog.objects.filter(action="points.adjust").count(), 1)
        self.clean_ledger()

    def test_no_expiry(self):
        self.config_version(expiry_days=0)
        self.adjust()
        self.assertIsNone(PointsLot.objects.get(source_type="ADJUSTMENT").expires_at)

    def test_positive_offsets_debt_partial_and_full(self):
        self.debt(15)
        self.adjust(10)
        lot = PointsLot.objects.get(source_type="ADJUSTMENT")
        self.assertEqual((lot.points_remaining, lot.status), (0, "CONSUMED"))
        self.assertEqual(self.adjust(8, key="next")["available_balance"], 3)
        self.assertEqual(PointsLot.objects.get(source_type="ADJUSTMENT", status="AVAILABLE").points_remaining, 3)
        self.clean_ledger()

    def test_negative_fefo_null_last_and_pending_untouched(self):
        null = self.lot(5)
        later = self.lot(5, expires=self.now+timedelta(days=10))
        first = self.lot(5, expires=self.now+timedelta(days=1))
        pending = self.lot(7, status="PENDING")
        self.adjust(-8)
        for row in (null, later, first, pending):
            row.refresh_from_db()
        self.assertEqual([first.points_remaining, later.points_remaining, null.points_remaining, pending.points_remaining], [0, 2, 5, 7])
        self.assertEqual(first.status, "CONSUMED")
        self.clean_ledger()

    def test_insufficient_allows_debt(self):
        self.lot(3)
        self.assertEqual(self.adjust(-10)["available_balance"], -7)
        entry = PointsTransaction.objects.get(type="ADJUST")
        self.assertEqual(entry.rule_snapshot["debt_created"], 7)
        self.clean_ledger()

    def test_insufficient_disallows_debt_rolls_back_everything(self):
        self.config_version(allow_negative_balance=False)
        lot = self.lot(3)
        before = (PointsTransaction.objects.count(), PointsLot.objects.count(), AuditLog.objects.count())
        with self.assertRaises(LoyaltyError) as error:
            self.adjust(-10)
        self.assertEqual(error.exception.code, "INSUFFICIENT_POINTS")
        self.profile.refresh_from_db()
        lot.refresh_from_db()
        self.assertEqual((self.profile.available_balance, lot.points_remaining), (3, 3))
        self.assertEqual(before, (PointsTransaction.objects.count(), PointsLot.objects.count(), AuditLog.objects.count()))
        self.clean_ledger()

    def test_same_key_returns_original_result_after_other_changes(self):
        first = self.adjust()
        self.adjust(20, key="next")
        replay = self.adjust()
        self.assertEqual({**first, "duplicate": True}, replay)
        self.assertEqual(PointsTransaction.objects.filter(type="ADJUST").count(), 2)
        self.clean_ledger()

    def test_same_key_changed_payload_rejected(self):
        self.adjust()
        with self.assertRaises(LoyaltyError) as error:
            self.adjust(11)
        self.assertEqual(error.exception.code, "KEY_REUSED")

    def test_global_key_rejects_other_profile(self):
        self.adjust()
        other = CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=302)
        with self.assertRaises(LoyaltyError) as error:
            adjust_points(other, 10, "GIFT", "Test adjustment", "shopify_user:123", "test", self.now)
        self.assertEqual(error.exception.code, "KEY_REUSED")
        self.clean_ledger()

    def test_blocked_allowed_anonymized_rejected(self):
        self.profile.is_blocked = True
        self.profile.save()
        self.adjust()
        self.profile.is_anonymized = True
        self.profile.save()
        with self.assertRaises(LoyaltyError) as error:
            self.adjust(key="anon")
        self.assertEqual(error.exception.code, "CUSTOMER_ANONYMIZED")

    @override_settings(LOYALTY_MAX_ADJUST_POINTS=20)
    def test_invalid_inputs_and_max(self):
        for delta in (0, True, 1.5, "5", 21, -21):
            with self.subTest(delta=delta), self.assertRaises(LoyaltyError):
                self.adjust(delta)
        for reason, note, key in (("BAD", "Valid note", "key"), ("GIFT", "tiny", "key"),
                                  ("GIFT", "Valid note", ""), ("GIFT", "x"*501, "key")):
            with self.assertRaises(LoyaltyError):
                adjust_points(self.profile, 1, reason, note, "actor", key, self.now)
        self.assertEqual(PointsTransaction.objects.count(), 0)


class ConcurrentAdjustmentTests(PointsSetup, TransactionTestCase):
    def parallel(self, operations):
        barrier = Barrier(len(operations))
        def work(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                delta, key, profile_id = operation
                profile = CustomerProfile.objects.get(pk=profile_id)
                try:
                    return adjust_points(profile, delta, "GIFT", "Concurrent test", "actor", key, self.now)
                except LoyaltyError as error:
                    return error.code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            return list(pool.map(work, operations))

    def test_different_keys_preserve_balance(self):
        rows = self.parallel([(10, "first", self.profile.pk), (20, "second", self.profile.pk)])
        self.assertTrue(all(isinstance(row, dict) for row in rows))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.available_balance, 30)
        self.clean_ledger()

    def test_same_key_mutates_once(self):
        rows = self.parallel([(10, "same", self.profile.pk)]*2)
        self.assertEqual(sorted(row["duplicate"] for row in rows), [False, True])
        self.assertEqual(PointsTransaction.objects.filter(type="ADJUST").count(), 1)
        self.clean_ledger()

    def test_cross_profile_key_race_rolls_back_loser(self):
        other = CustomerProfile.objects.create(shop=self.shop, shopify_customer_id=302)
        rows = self.parallel([(10, "shared", self.profile.pk), (10, "shared", other.pk)])
        self.assertEqual(sum(row == "KEY_REUSED" for row in rows), 1)
        self.assertEqual(PointsLot.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="points.adjust").count(), 1)
        self.clean_ledger()
