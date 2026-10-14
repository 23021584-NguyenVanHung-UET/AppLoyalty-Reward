"""Financial and distributed-systems audit. Real PostgreSQL commits, mocked I/O.

Every scenario ends with the public check_ledger command, including warnings.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from threading import Barrier, Event
from unittest.mock import patch

import httpx

from django.core.management import call_command
from django.db import close_old_connections, connection, connections, transaction
from django.db.models import Sum
from django.test import TransactionTestCase
from django.utils import timezone

from loyalty.models import (AuditLog, Campaign, CustomerProfile, EarnRule, OrderCancellation,
                            PointsLot, PointsTransaction, Redemption, Reward, Tier)
from loyalty.services.earning import earn_points_for_order
from loyalty.services.integrity import check_profile_integrity, check_profile_warnings
from loyalty.services.lifecycle import activate_due_lots, expire_due_lots
from loyalty.services.redemption import (claim_issuance, fail_redemption, issue_redemption,
                                       redeem_reward, refund_redemption_points, save_redemption)
from loyalty.services.redemption_jobs import process_redemptions_services
from loyalty.services.revoking import revoke_points_for_cancel, revoke_points_for_refund
from shops.shopify import ShopifyError, ShopifyTransientError
from shops.shopify import create_discount_code as real_create_discount
from shops.shopify import graphql as real_graphql
from shops.tests.fixtures import connection as page, money, order_line, raw_order, raw_refund
from webhooks.handlers import HANDLERS
from webhooks.models import WebhookEvent
from .lifecycle_fixtures import PointsSetup
from .test_redemption import found_discount


class AuditSetup(PointsSetup):
    def setUp(self):
        super().setUp()
        self.config_version(pending_days=0, max_redemptions_per_day=100, max_unused_codes_per_reward=100)
        self.reward.code_valid_days = 3650
        self.reward.save()
        self.orders = {}
        self.create = patch("loyalty.services.redemption.create_discount_code",
            return_value="gid://shopify/DiscountCodeNode/123").start()
        self.find = patch("loyalty.services.redemption.find_discount_by_code", return_value=None).start()
        self.job_find = patch("loyalty.services.redemption_jobs.find_discount_by_code", return_value=None).start()
        patch("loyalty.services.redemption_jobs.deactivate_discount", return_value=True).start()
        patch("django_q.tasks.async_task").start()
        patch("loyalty.services.notifications.async_task").start()
        patch("loyalty.services.earning.fetch_order", side_effect=lambda pk: deepcopy(self.orders[pk])).start()
        patch("loyalty.services.revoking.fetch_order", side_effect=lambda pk: deepcopy(self.orders[pk])).start()

    def raw(self, pk, points):
        order = raw_order(pk)
        order["lineItems"] = page([order_line(total=str(Decimal(points) * 10))])
        order["currentShippingPriceSet"] = money("0")
        order["shippingLines"] = page([])
        # Reuse the mapper fixtures while testing the shop's actual USD currency.
        import json
        return json.loads(json.dumps(order).replace('"VND"', '"USD"'))

    def earn(self, pk, points, pending=0, expires=None):
        self.config_version(pending_days=pending)
        self.orders[pk] = self.raw(pk, points)
        result = earn_points_for_order(self.shop, pk)
        self.assertEqual(result.status, "EARNED")
        lot = PointsLot.objects.get(profile=self.profile, source_order_id=pk)
        if expires is not None:
            lot.expires_at = expires
            lot.save(update_fields=["expires_at"])
        return lot

    def redeem(self, points, key="one"):
        Reward.objects.filter(pk=self.reward.pk).update(points_cost=points)
        return redeem_reward(self.shop, 301, self.reward.pk, key, self.now)

    def cancel(self, pk):
        self.orders[pk]["cancelledAt"] = self.now.isoformat()
        return revoke_points_for_cancel(self.shop, pk)

    def refund(self, pk, refund_id, spend, **kw):
        import json
        refund = raw_refund(refund_id, str(spend), **kw)
        self.orders[pk]["refunds"].append(json.loads(json.dumps(refund).replace('"VND"', '"USD"')))
        self.orders[pk]["displayFinancialStatus"] = "PARTIALLY_REFUNDED"
        return revoke_points_for_refund(self.shop, pk, refund_id)

    def balances(self):
        self.profile.refresh_from_db()
        return self.profile.available_balance, self.profile.pending_balance

    def assert_ledger(self, now=None):
        at = now or self.now
        for profile in CustomerProfile.objects.all():
            self.assertEqual(check_profile_integrity(profile), [])
            self.assertEqual(check_profile_warnings(profile, at), [])
        output = StringIO()
        with patch("loyalty.management.commands.check_ledger.resolve_now", return_value=at):
            call_command("check_ledger", stdout=output)
        self.assertNotIn("Cảnh báo", output.getvalue())
        self.assertIn("có lỗi: 0", output.getvalue())

    def worker(self, operation):
        close_old_connections()
        try:
            return operation()
        finally:
            connections.close_all()


class WalletLifecycleAuditTests(AuditSetup, TransactionTestCase):
    def test_f1_fifty_pending_offsets_thirty_debt_and_leaves_twenty(self):
        self.earn(1001, 30)
        self.redeem(30)
        self.cancel(1001)
        pending = self.earn(1002, 50, pending=7)
        count = PointsTransaction.objects.count()
        activate_due_lots(pending.available_at)
        pending.refresh_from_db()
        self.assertEqual((self.balances(), pending.points_remaining), ((20, 0), 20))
        self.assertEqual(PointsTransaction.objects.count(), count)
        self.assert_ledger(now=pending.available_at)

    def test_f1_pending_activation_absorbs_full_and_partial_debt_without_ledger(self):
        self.earn(1001, 100)
        self.redeem(100)
        pending = self.earn(1002, 50, pending=7)
        self.cancel(1001)
        self.assertEqual(self.balances(), (-100, 50))
        count = PointsTransaction.objects.count()
        activate_due_lots(pending.available_at)
        pending.refresh_from_db()
        self.assertEqual((self.balances(), pending.points_remaining), ((-50, 0), 0))
        self.assertEqual(PointsTransaction.objects.count(), count)
        another = self.earn(1003, 70, pending=7)
        activate_due_lots(another.available_at)
        another.refresh_from_db()
        self.assertEqual((self.balances(), another.points_remaining), ((20, 0), 20))
        self.assert_ledger(now=another.available_at)

    def test_f2_immediate_earn_absorbs_debt_and_leaves_only_surplus(self):
        self.earn(1001, 80)
        self.redeem(80)
        self.cancel(1001)
        small = self.earn(1002, 50)
        self.assertEqual((self.balances(), small.points_remaining), ((-30, 0), 0))
        # Restore the exact -80 case with a genuine refund of the just-earned order.
        self.cancel(1002)
        large = self.earn(1003, 120)
        self.assertEqual((self.balances(), large.points_remaining), ((40, 0), 40))
        self.assert_ledger()

    def test_f3_partial_refunds_take_own_pending_before_older_available(self):
        older = self.earn(1001, 200, expires=self.now + timedelta(days=2))
        pending = self.earn(1002, 80, pending=7)
        self.assertEqual(self.refund(1002, 501, "300").points, 30)
        self.assertEqual(self.balances(), (200, 50))
        self.assertEqual(self.refund(1002, 502, "500").points, 50)
        older.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual((older.points_remaining, pending.points_remaining), (200, 0))
        self.assertEqual(self.balances(), (200, 0))
        self.assert_ledger()

    def test_f3_pending_shortfall_alone_falls_back_to_other_available(self):
        older = self.earn(1001, 200)
        pending = self.earn(1002, 80, pending=7)
        # Valid historical partial activation: split 30 into an available lot.
        with transaction.atomic():
            profile = CustomerProfile.objects.select_for_update().get(pk=self.profile.pk)
            pending.points_remaining = 50
            pending.save(update_fields=["points_remaining"])
            PointsLot.objects.create(profile=profile, source_type="ORDER", source_order_id=1002,
                status="AVAILABLE", points_total=30, points_remaining=30, available_at=self.now)
            profile.pending_balance -= 30
            profile.available_balance += 30
            profile.save(update_fields=["pending_balance", "available_balance"])
        self.assertEqual(self.refund(1002, 501, "800").points, 80)
        older.refresh_from_db()
        self.assertEqual((self.balances(), older.points_remaining), ((200, 0), 200))
        self.assert_ledger()

    def test_f3_pending_shortfall_uses_other_available_only_for_missing_thirty(self):
        older = self.earn(1001, 200)
        pending = self.earn(1002, 80, pending=7)
        # Legacy partial expiry fixture: 30 were already removed from pending
        # through a non-REVOKE event; the refund still owes the original 80.
        with transaction.atomic():
            profile = CustomerProfile.objects.select_for_update().get(pk=self.profile.pk)
            pending.points_remaining = 50
            pending.save(update_fields=["points_remaining"])
            profile.pending_balance -= 30
            profile.save(update_fields=["pending_balance"])
            PointsTransaction.objects.create(profile=profile, type="EXPIRE", delta=-30, lot=pending,
                order_id=1002, balance_after=profile.available_balance, idempotency_key="legacy-partial-expire")
        self.assertEqual(self.refund(1002, 501, "800").points, 80)
        allocations = PointsTransaction.objects.get(type="REVOKE").rule_snapshot["lot_allocations"]
        self.assertEqual([(item["lot_id"], item["points"]) for item in allocations], [(pending.pk, 50), (older.pk, 30)])
        self.assertEqual(self.balances(), (170, 0))
        self.assert_ledger()

    def cross_lots(self, remaining, allow_negative):
        self.config_version(allow_negative_balance=allow_negative)
        later = self.earn(1001, 100, expires=self.now + timedelta(days=365))
        earlier = self.earn(1002, 100, expires=self.now + timedelta(days=7))
        self.redeem(100)
        earlier.refresh_from_db()
        self.assertEqual(earlier.points_remaining, 0)
        if remaining < 100:
            self.redeem(100 - remaining, "second")
        result = self.refund(1002, 501, "1000")
        later.refresh_from_db()
        self.assertEqual(later.points_remaining, 0)
        self.assertEqual(result.points, 100 if allow_negative else remaining)

    def test_f4_cross_lot_refund_depletes_other_order(self):
        self.cross_lots(100, True)
        self.assertEqual(self.balances(), (0, 0))
        self.assert_ledger()

    def test_f4_cross_lot_shortage_creates_debt_when_allowed(self):
        self.cross_lots(40, True)
        self.assertEqual(self.balances(), (-60, 0))
        self.assert_ledger()

    def test_f4_cross_lot_shortage_is_audited_without_double_counting_waiver(self):
        with self.assertLogs("loyalty.services.revoking", level="WARNING"):
            self.cross_lots(40, False)
        self.assertEqual(self.balances(), (0, 0))
        audit = AuditLog.objects.get(action="revoke_unrecovered")
        self.assertEqual(audit.after["unrecovered_points"], 60)
        self.assertTrue(audit.after["recorded_in_ledger"])
        self.earn(1003, 100)
        self.assertEqual(self.cancel(1002).points, 0)
        self.assertEqual(self.balances(), (100, 0))
        self.assert_ledger()

    def test_f5_three_multiplier_refunds_round_once_and_duplicate_five_times(self):
        tier = Tier.objects.create(shop=self.shop, name="Gold", rank=1, min_qualifying_points=0, multiplier="3")
        CustomerProfile.objects.filter(pk=self.profile.pk).update(tier=tier, tier_since=self.now)
        self.config_version(tiers_enabled=True)
        rule = EarnRule.objects.create(shop=self.shop, name="Product", rule_type="PRODUCT", target_ids=[201],
            multiplier="2", starts_at=self.now - timedelta(days=1))
        campaign = Campaign.objects.create(shop=self.shop, name="Campaign", multiplier="1.5", starts_at=self.now - timedelta(days=1))
        self.orders[1001] = self.raw(1001, 20)
        self.orders[1001]["createdAt"] = (timezone.now() + timedelta(seconds=1)).isoformat()
        self.assertEqual(earn_points_for_order(self.shop, 1001).points, 180)
        rule.is_active = campaign.is_active = False
        rule.multiplier = campaign.multiplier = Decimal("1")
        rule.save()
        campaign.save()
        self.assertEqual(self.refund(1001, 500, "0").status, "NOOP")
        for refund_id, spend, expected in [(501, "66.67", 61), (502, "66.67", 60), (503, "66.66", 59)]:
            self.assertEqual(self.refund(1001, refund_id, spend, tax="999", shipping="999").points, expected)
            for _ in range(5):
                self.assertEqual(revoke_points_for_refund(self.shop, 1001, refund_id).status, "DUPLICATE")
        self.assertEqual(PointsTransaction.objects.filter(type="REVOKE").aggregate(n=Sum("delta"))["n"], -180)
        self.assertEqual(self.balances(), (0, 0))
        self.assert_ledger()

    def test_f6_expiry_spent_sixty_expires_only_forty_and_replay_skips(self):
        expiry = self.now + timedelta(minutes=1)
        lot = self.earn(1001, 100, expires=expiry)
        self.redeem(60)
        self.assertEqual(expire_due_lots(expiry).expired_points, 40)
        self.assertEqual(expire_due_lots(expiry).expired_points, 0)
        lot.refresh_from_db()
        self.assertEqual((lot.status, lot.points_remaining), ("EXPIRED", 0))
        self.assertEqual(PointsTransaction.objects.get(type="EXPIRE").delta, -40)
        self.assertEqual(self.balances(), (0, 0))
        self.assert_ledger(now=expiry)

    def test_f6_expiry_fully_consumed_emits_no_zero_transaction(self):
        expiry = self.now + timedelta(minutes=1)
        self.earn(1001, 100, expires=expiry)
        self.redeem(100)
        self.assertEqual(expire_due_lots(expiry).expired_points, 0)
        self.assertFalse(PointsTransaction.objects.filter(type="EXPIRE").exists())
        self.assert_ledger(now=expiry)

    def test_f6_expiry_races_redeem_with_profile_lock(self):
        expiry = self.now + timedelta(minutes=1)
        self.earn(1001, 100, expires=expiry)
        barrier = Barrier(2)
        def expire():
            barrier.wait(timeout=10)
            return expire_due_lots(expiry)
        def redeem():
            barrier.wait(timeout=10)
            return self.redeem(60)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.worker, op) for op in [expire, redeem]]
            results = [future.result(timeout=15) for future in futures]
        self.assertEqual(results[0].errors, 0)
        self.assertEqual(self.balances(), (0, 0))
        self.assertFalse(PointsTransaction.objects.filter(delta=0).exists())
        self.assert_ledger(now=expiry)

    def test_f6_expiry_races_revoke_without_consuming_the_same_points_twice(self):
        expiry = self.now + timedelta(minutes=1)
        self.earn(1001, 100, expires=expiry)
        self.redeem(60)
        barrier = Barrier(2)
        def expire():
            barrier.wait(timeout=10)
            return expire_due_lots(expiry)
        def revoke():
            barrier.wait(timeout=10)
            return self.cancel(1001)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.worker, op) for op in [expire, revoke]]
            results = [future.result(timeout=15) for future in futures]
        self.assertEqual(results[0].errors, 0)
        self.assertEqual(results[1].points, 100)
        # Expiry may commit before revocation. It only ever removes the 40
        # remaining; refunds retain the existing policy of revoking earned points.
        self.assertEqual(self.balances(), (-60 - results[0].expired_points, 0))
        self.assertFalse(PointsTransaction.objects.filter(delta=0).exists())
        self.assert_ledger(now=expiry)


class CancellationSagaAuditTests(AuditSetup, TransactionTestCase):
    def event(self, topic, pk=1001):
        return WebhookEvent.objects.create(shop=self.shop, shop_domain=self.shop.domain,
            topic=topic, webhook_id=f"audit:{topic}:{pk}", payload={"id": pk})

    def test_s1_cancel_before_paid_blocks_even_a_stale_paid_snapshot(self):
        self.orders[1001] = self.raw(1001, 100)
        # The authenticated cancellation delivery is authoritative even if fetch fails.
        cancel = self.event("orders/cancelled")
        with patch("loyalty.services.revoking.fetch_order", side_effect=ShopifyTransientError("timeout")):
            with self.assertRaises(ShopifyTransientError):
                HANDLERS[cancel.topic](cancel)
        self.assertTrue(OrderCancellation.objects.filter(shop=self.shop, order_id=1001).exists())
        paid = self.event("orders/paid")
        HANDLERS[paid.topic](paid)
        self.assertEqual((paid.status, paid.skip_reason), ("SKIPPED", "Đơn đã hủy"))
        self.assertFalse(PointsTransaction.objects.filter(order_id=1001).exists())
        self.assert_ledger()

    def test_s2_paid_cancel_concurrent_and_both_lock_orders_finish_at_zero(self):
        stale = self.raw(1001, 100)
        cancelled = {**stale, "cancelledAt": self.now.isoformat()}
        barrier = Barrier(2)
        def fetch(raw):
            self.assertFalse(connection.in_atomic_block)
            barrier.wait(timeout=10)
            return deepcopy(raw)
        with patch("loyalty.services.earning.fetch_order", side_effect=lambda _: fetch(stale)), \
                patch("loyalty.services.revoking.fetch_order", side_effect=lambda _: fetch(cancelled)), \
                ThreadPoolExecutor(max_workers=2) as pool:
            paid = pool.submit(self.worker, lambda: earn_points_for_order(self.shop, 1001))
            cancel = pool.submit(self.worker, lambda: revoke_points_for_cancel(self.shop, 1001))
            paid.result(timeout=15)
            cancel.result(timeout=15)
        self.assertEqual(PointsTransaction.objects.filter(order_id=1001).aggregate(n=Sum("delta"))["n"] or 0, 0)
        self.assertEqual(self.balances(), (0, 0))
        self.assert_ledger()

    def test_s2_paid_commits_first_then_cancel_revokes_under_profile_lock(self):
        self.earn(1001, 100)
        self.assertEqual(self.cancel(1001).points, 100)
        self.assertEqual(self.balances(), (0, 0))
        self.assert_ledger()

    def test_s3_cancel_and_refund_parallel_never_double_revoke(self):
        self.earn(1001, 100)
        self.orders[1001]["cancelledAt"] = self.now.isoformat()
        import json
        self.orders[1001]["refunds"] = [json.loads(json.dumps(raw_refund(501, "1000")).replace('"VND"', '"USD"'))]
        barrier = Barrier(2)
        def fetch(_):
            barrier.wait(timeout=10)
            return deepcopy(self.orders[1001])
        with patch("loyalty.services.revoking.fetch_order", side_effect=fetch), ThreadPoolExecutor(max_workers=2) as pool:
            operations = [lambda: revoke_points_for_cancel(self.shop, 1001),
                          lambda: revoke_points_for_refund(self.shop, 1001, 501)]
            outcomes = [future.result(timeout=15) for future in [pool.submit(self.worker, op) for op in operations]]
        self.assertEqual(sum(result.points for result in outcomes), 100)
        self.assertEqual(self.balances(), (0, 0))
        self.assert_ledger()

    def test_s3_refund_then_cancel_and_cancel_then_refund(self):
        self.earn(1001, 100)
        self.assertEqual(self.refund(1001, 501, "1000").points, 100)
        self.assertEqual(self.cancel(1001).points, 0)
        self.earn(1002, 100)
        self.assertEqual(self.cancel(1002).points, 100)
        self.assertEqual(self.refund(1002, 502, "1000").points, 0)
        self.assertEqual(PointsTransaction.objects.filter(type="REVOKE").aggregate(n=Sum("delta"))["n"], -200)
        self.assert_ledger()

    def test_s4_network_sees_committed_debit_and_another_connection_can_lock_wallet(self):
        self.earn(1001, 100)
        def inspect(*_):
            self.assertFalse(connection.in_atomic_block)
            self.assertTrue(connection.get_autocommit())
            def other_connection():
                with transaction.atomic():
                    profile = CustomerProfile.objects.select_for_update(nowait=True).get(pk=self.profile.pk)
                    list(PointsLot.objects.select_for_update(nowait=True).filter(profile=profile))
                    return profile.available_balance, PointsTransaction.objects.filter(type="REDEEM").count()
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertEqual(pool.submit(self.worker, other_connection).result(timeout=10), (0, 1))
            return "gid://shopify/DiscountCodeNode/123"
        self.create.side_effect = inspect
        self.assertEqual(self.redeem(100).status, "ISSUED")
        self.assert_ledger()

    def test_s4_outer_transaction_defers_io_until_real_commit_and_discards_on_rollback(self):
        self.earn(1001, 100)
        with transaction.atomic():
            self.assertEqual(self.redeem(100).status, "PENDING")
            self.create.assert_not_called()
        self.create.assert_called_once()
        self.earn(1002, 100)
        with transaction.atomic():
            self.redeem(100, "rolled-back")
            transaction.set_rollback(True)
        self.assertFalse(Redemption.objects.filter(idempotency_key="redeem:rolled-back").exists())
        self.create.assert_called_once()
        self.assert_ledger()

    def compensation_lots(self):
        self.earn(1001, 40, expires=self.now + timedelta(days=5))
        self.earn(1002, 100, expires=self.now + timedelta(days=30))

    def assert_compensation(self, row):
        self.assertEqual(self.balances(), (140, 0))
        self.assertEqual(list(PointsLot.objects.filter(source_type="REFUND_BACK").order_by("pk")
            .values_list("points_remaining", "expires_at")),
            [(40, self.now + timedelta(days=5)), (60, self.now + timedelta(days=30))])
        self.assertEqual(PointsTransaction.objects.get(type="REDEEM").delta, -100)
        self.assertEqual(PointsTransaction.objects.get(type="REDEEM_REFUND").delta, 100)
        self.assertFalse(refund_redemption_points(row, "ISSUE_FAILED", self.now))

    def test_s5_permanent_422_and_user_errors_compensate_preserving_allocation_expiries(self):
        self.compensation_lots()
        self.create.side_effect = ShopifyError("DISCOUNT_REJECTED: GraphQL userErrors / HTTP 422")
        result = self.redeem(100)
        row = Redemption.objects.get(pk=result.redemption_id)
        self.assertEqual(row.status, "FAILED")
        self.assert_compensation(row)
        self.assert_ledger()

    def test_s5_http_500_and_timeout_reconcile_absence_before_compensation(self):
        self.compensation_lots()
        self.create.side_effect = ShopifyTransientError("HTTP 500 / timeout")
        row = Redemption.objects.get(pk=self.redeem(100).redemption_id)
        self.assertEqual(row.status, "PENDING_CODE")
        self.assertEqual(self.balances(), (40, 0))
        self.assert_ledger()
        save_redemption(row, self.now, attempts=5)
        self.assertEqual(process_redemptions_services(self.now + timedelta(minutes=1)).failed, 1)
        self.assert_compensation(row)
        self.assert_ledger(now=self.now + timedelta(minutes=1))

    def test_s5_timeout_after_remote_success_adopts_code_without_refund(self):
        self.earn(1001, 100)
        self.create.side_effect = ShopifyTransientError("response lost")
        row = Redemption.objects.get(pk=self.redeem(100).redemption_id)
        self.find.return_value = found_discount(row)
        self.assertEqual(issue_redemption(row.pk, self.now + timedelta(minutes=1)).status, "ISSUED")
        self.assertEqual(self.create.call_count, 1)
        self.assertFalse(PointsTransaction.objects.filter(type="REDEEM_REFUND").exists())
        self.assert_ledger()

    def protocol_failure(self, failure):
        self.compensation_lots()
        request = httpx.Request("POST", "https://test.myshopify.com/admin/api/test/graphql.json")
        if failure == "timeout":
            response = httpx.ReadTimeout("response lost", request=request)
        elif failure == "userErrors":
            response = httpx.Response(200, request=request, json={"data": {"discountCodeBasicCreate": {
                "codeDiscountNode": None, "userErrors": [{"field": ["basicCodeDiscount"],
                    "code": "INVALID", "message": "private provider details"}]}}})
        else:
            response = httpx.Response(failure, request=request, json={})
        self.create.side_effect = real_create_discount
        with patch("shops.shopify.graphql", side_effect=real_graphql), \
                patch("shops.shopify.get_access_token", return_value="test-only"), \
                patch("shops.shopify.wait_for_capacity"), \
                patch("shops.shopify.httpx.post", side_effect=response if isinstance(response, Exception) else None,
                      return_value=response):
            result = self.redeem(100)
        row = Redemption.objects.get(pk=result.redemption_id)
        if failure in (500, 503, "timeout"):
            self.assertEqual(result.status, "PENDING")
            save_redemption(row, self.now, attempts=5)
            self.assertEqual(process_redemptions_services(self.now + timedelta(minutes=1)).failed, 1)
        else:
            self.assertEqual(result.reason_code, "ISSUE_FAILED")
        self.assert_compensation(row)

    def test_s5_real_adapter_http_500(self):
        self.protocol_failure(500)
        self.assert_ledger()

    def test_s5_real_adapter_http_503(self):
        self.protocol_failure(503)
        self.assert_ledger()

    def test_s5_real_adapter_http_422(self):
        self.protocol_failure(422)
        self.assert_ledger()

    def test_s5_real_adapter_graphql_user_errors(self):
        self.protocol_failure("userErrors")
        self.assert_ledger()

    def test_s5_real_adapter_network_read_timeout(self):
        self.protocol_failure("timeout")
        self.assert_ledger()

    def test_s5_failed_lookup_after_timeout_never_refunds_an_unconfirmed_code(self):
        self.earn(1001, 100)
        self.create.side_effect = ShopifyTransientError("response lost")
        row = Redemption.objects.get(pk=self.redeem(100).redemption_id)
        self.find.side_effect = ShopifyError("lookup access denied")
        self.assertEqual(issue_redemption(row.pk, self.now + timedelta(minutes=1)).status, "PENDING")
        self.assertFalse(PointsTransaction.objects.filter(type="REDEEM_REFUND").exists())
        self.assertEqual(self.balances(), (0, 0))
        self.assert_ledger(now=self.now + timedelta(minutes=1))

    def test_s5_compensation_absorbs_debt_created_while_network_waits(self):
        self.compensation_lots()
        def create(*_):
            self.assertFalse(connection.in_atomic_block)
            # Cancel both orders during I/O: the 40 unspent points are consumed,
            # then a further 100 debt remains, entirely absorbed by compensation.
            self.cancel(1001)
            self.cancel(1002)
            raise ShopifyError("rejected")
        self.create.side_effect = create
        self.redeem(100)
        self.assertEqual(self.balances(), (0, 0))
        self.assertFalse(PointsLot.objects.filter(status="AVAILABLE", points_remaining__gt=0).exists())
        self.assertEqual(PointsTransaction.objects.get(type="REDEEM_REFUND").rule_snapshot["offset"], 100)
        self.assert_ledger()

    def test_s6_inflight_same_and_different_keys_cannot_spam_or_double_spend(self):
        self.earn(1001, 100)
        entered, release = Event(), Event()
        def create(*_):
            entered.set()
            if not release.wait(timeout=10):
                raise AssertionError("HTTP test was not released")
            return "gid://shopify/DiscountCodeNode/123"
        self.create.side_effect = create
        Reward.objects.filter(pk=self.reward.pk).update(points_cost=100)
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.worker, lambda: self.redeem(100, "same"))
            try:
                self.assertTrue(entered.wait(timeout=10))
                for index in range(4):
                    self.assertEqual(self.redeem(100, "same").status, "PENDING")
                    self.assertEqual(self.redeem(100, f"other-{index}").reason_code, "INSUFFICIENT_POINTS")
                row = Redemption.objects.get()
                # A second qcluster worker must respect the issuance claim too.
                self.assertEqual(issue_redemption(row.pk, self.now, force=True).status, "PENDING")
            finally:
                release.set()
            self.assertEqual(first.result(timeout=15).status, "ISSUED")
        self.assertEqual(self.create.call_count, 1)
        self.assertEqual(PointsTransaction.objects.filter(type="REDEEM").count(), 1)
        self.assert_ledger()

    def test_s6_crashed_claim_can_be_reconciled_and_stale_worker_cannot_compensate(self):
        self.earn(1001, 100)
        self.create.side_effect = ShopifyTransientError("timeout")
        row = Redemption.objects.get(pk=self.redeem(100).redemption_id)
        _, stale_claim = claim_issuance(row.pk, self.now, force=True)
        at = self.now + timedelta(minutes=6)
        _, new_claim = claim_issuance(row.pk, at, force=True)
        self.assertNotEqual(stale_claim, new_claim)
        self.assertEqual(fail_redemption(row.pk, at, stale_claim).status, "PENDING")
        self.assertEqual(fail_redemption(row.pk, at, new_claim).reason_code, "ISSUE_FAILED")
        self.assertEqual(self.balances(), (100, 0))
        self.assert_ledger(now=at)

    def test_s7_expiry_passes_during_io_compensation_grace_then_cron_expires_safely(self):
        expiry = self.now + timedelta(seconds=2)
        self.earn(1001, 50, expires=expiry)
        self.create.side_effect = ShopifyTransientError("timeout")
        row = Redemption.objects.get(pk=self.redeem(50).redemption_id)
        at = self.now + timedelta(seconds=10)
        # Retry receives a definitive rejection after the original lot expired.
        self.create.side_effect = ShopifyError("userErrors")
        self.assertEqual(issue_redemption(row.pk, at, force=True).reason_code, "ISSUE_FAILED")
        returned = PointsLot.objects.get(source_type="REFUND_BACK")
        self.assertEqual((returned.status, returned.expires_at), ("AVAILABLE", at + timedelta(hours=24)))
        self.assertEqual(expire_due_lots(returned.expires_at).expired_points, 50)
        self.assertEqual(expire_due_lots(returned.expires_at).expired_points, 0)
        self.assertEqual(self.balances(), (0, 0))
        self.assert_ledger(now=returned.expires_at)

    def test_s7_live_clock_uses_completion_time_after_remote_delay(self):
        expiry = self.now + timedelta(seconds=2)
        self.earn(1001, 50, expires=expiry)
        Reward.objects.filter(pk=self.reward.pk).update(points_cost=50)
        clock = [self.now]
        def rejected(*_):
            clock[0] += timedelta(seconds=10)
            raise ShopifyError("rejected")
        self.create.side_effect = rejected
        with patch("loyalty.services.redemption.resolve_now", side_effect=lambda at=None: at or clock[0]):
            result = redeem_reward(self.shop, 301, self.reward.pk, "live-clock")
        self.assertEqual(result.reason_code, "ISSUE_FAILED")
        returned = PointsLot.objects.get(source_type="REFUND_BACK")
        self.assertEqual(returned.expires_at, clock[0] + timedelta(hours=24))
        self.assert_ledger(now=clock[0])
