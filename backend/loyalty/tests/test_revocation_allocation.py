from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase

from loyalty.services.revocation_allocation import RevokeLot, plan_revocation


class AllocationTests(SimpleTestCase):
    def lot(self, number, points, status="PENDING", order=1001, expires=None, consumed=False):
        return RevokeLot(number, "ORDER", order, status, points, expires, consumed)

    def test_pending_only_and_inputs_unchanged(self):
        lot = self.lot(1, 10)
        plan = plan_revocation([lot], 1001, 10)
        self.assertEqual([(a.lot_id, a.points, a.status_after) for a in plan.allocations], [(1, 10, "VOIDED")])
        self.assertEqual((plan.leftover, plan.debt_created, plan.unrecovered_points), (0, 0, 0))
        self.assertEqual(lot.points_remaining, 10)

    def test_pending_precedes_own_available_and_other_pending_untouched(self):
        lots = [self.lot(1, 5, "AVAILABLE"), self.lot(2, 7), self.lot(3, 20, order=2001)]
        plan = plan_revocation(lots, 1001, 10)
        self.assertEqual([(a.lot_id, a.points) for a in plan.allocations], [(2, 7), (1, 3)])
        self.assertEqual(plan.allocations[1].status_after, "AVAILABLE")

    def test_other_lots_fefo_null_last_then_debt(self):
        now = datetime(2026, 10, 12, tzinfo=timezone.utc)
        lots = [self.lot(1, 2), self.lot(2, 3, "AVAILABLE", 2001, None),
                self.lot(3, 4, "AVAILABLE", 2002, now + timedelta(days=2)),
                self.lot(4, 5, "AVAILABLE", 2003, now + timedelta(days=1)),
                self.lot(5, 100, "EXPIRED")]
        plan = plan_revocation(lots, 1001, 20)
        self.assertEqual([a.lot_id for a in plan.allocations], [1, 4, 3, 2])
        self.assertEqual([a.status_after for a in plan.allocations], ["VOIDED", "CONSUMED", "CONSUMED", "CONSUMED"])
        self.assertEqual((plan.leftover, plan.debt_created), (6, 6))

    def test_consumed_own_lot_marked_consumed_at_zero(self):
        plan = plan_revocation([self.lot(1, 3, "AVAILABLE", consumed=True)], 1001, 3)
        self.assertEqual(plan.allocations[0].status_after, "CONSUMED")

    def test_no_negative_clamps_capacity_and_reports_shortage(self):
        plan = plan_revocation([self.lot(1, 3, "AVAILABLE")], 1001, 10,
                               allow_negative_balance=False, available_balance=3)
        self.assertEqual((plan.allocations[0].points, plan.debt_created, plan.unrecovered_points), (3, 0, 7))
        plan = plan_revocation([self.lot(1, 10, "AVAILABLE")], 1001, 10,
                               allow_negative_balance=False, available_balance=3)
        self.assertEqual((plan.allocations[0].points, plan.unrecovered_points), (3, 7))

    def test_no_negative_pending_can_be_taken_with_existing_debt(self):
        plan = plan_revocation([self.lot(1, 4)], 1001, 10,
                               allow_negative_balance=False, available_balance=-3)
        self.assertEqual((plan.allocations[0].points, plan.unrecovered_points), (4, 6))

    def test_no_points_and_zero_request(self):
        self.assertEqual(plan_revocation([], 1001, 5).debt_created, 5)
        self.assertEqual(plan_revocation([], 1001, 0).allocations, [])
