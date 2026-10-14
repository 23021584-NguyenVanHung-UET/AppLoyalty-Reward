from dataclasses import replace
from decimal import Decimal as D

from django.test import SimpleTestCase

from loyalty.services.calculation import EarnRules, OrderLine, OrderSnapshot, RefundLine, compute_earn


class CalculationTests(SimpleTestCase):
    def setUp(self):
        self.line = OrderLine(101, 201, [401], False, 1, D("100000"), D("0"))
        self.order = OrderSnapshot(1001, False, False, "PAID", False, 301, "web", [self.line])
        self.rules = EarnRules(D("10000"))

    def calculate(self, order=None, rules=None):
        return compute_earn(order or self.order, rules or self.rules)

    def test_spend_and_decimal_precision_table(self):
        for amount, expected in [("100000", 10), ("95000", 9), ("99999.99", 9),
                                 ("9999.99999999", 0), ("0", 0)]:
            with self.subTest(amount=amount):
                order = replace(self.order, lines=[replace(self.line, total_after_discounts=D(amount))])
                self.assertEqual(self.calculate(order).points, expected)

    def test_discount_is_already_in_total(self):
        result = self.calculate(replace(self.order, lines=[replace(self.line, total_after_discounts=D("80000"))]))
        self.assertEqual(result.points, 8)

    def test_tax_modes_table(self):
        for included, include_tax, expected in [(True, False, 9), (True, True, 10),
                                                (False, True, 11), (False, False, 10)]:
            with self.subTest(included=included, include_tax=include_tax):
                order = replace(self.order, taxes_included=included, lines=[replace(self.line, tax_amount=D("10000"))])
                result = self.calculate(order, replace(self.rules, include_tax=include_tax))
                self.assertEqual(result.points, expected)

    def test_shipping_flag(self):
        order = replace(self.order, shipping_after_discounts=D("20000"))
        self.assertEqual(self.calculate(order).points, 10)
        self.assertEqual(self.calculate(order, replace(self.rules, include_shipping=True)).points, 12)

    def test_gift_card_paid_30_percent(self):
        self.assertEqual(self.calculate(replace(self.order, gift_card_paid=D("30000"))).points, 7)

    def test_each_line_exclusion(self):
        scenarios = [([replace(self.line, is_gift_card=True)], self.rules, "gift_card_products_excluded"),
                     ([self.line], replace(self.rules, excluded_product_ids=[201]), "products_excluded"),
                     ([self.line], replace(self.rules, excluded_product_ids=["gid://shopify/Product/201"]), "products_excluded"),
                     ([self.line], replace(self.rules, excluded_collection_ids=["401"]), "collections_excluded")]
        for lines, rules, key in scenarios:
            with self.subTest(key=key):
                result = self.calculate(replace(self.order, lines=lines), rules)
                self.assertEqual(result.points, 0)
                self.assertEqual(result.breakdown[key], 1)

    def test_channel_excluded(self):
        result = self.calculate(rules=replace(self.rules, excluded_channels=["web"]))
        self.assertEqual(result.breakdown["code"], "channel_excluded")

    def test_partial_refund_per_line_and_clamping(self):
        order = replace(self.order, financial_status="PARTIALLY_REFUNDED",
                        refund_lines=[RefundLine(101, 1, D("25000"))])
        self.assertEqual(self.calculate(order).eligible_spend, D("75000"))
        order.refund_lines.append(RefundLine(101, 1, D("90000")))
        self.assertEqual(self.calculate(order).eligible_spend, D("0"))

    def test_refund_on_excluded_line_does_not_reduce_eligible_line(self):
        excluded = replace(self.line, id=102, is_gift_card=True)
        order = replace(self.order, lines=[self.line, excluded], refund_lines=[RefundLine(102, 1, D("100000"))])
        self.assertEqual(self.calculate(order).points, 10)

    def test_financial_status_table(self):
        for status in ("REFUNDED", "AUTHORIZED", "PARTIALLY_PAID", "PENDING", "VOIDED"):
            with self.subTest(status=status):
                self.assertEqual(self.calculate(replace(self.order, financial_status=status)).breakdown["code"],
                                 "financial_status_ineligible")
        for status in ("PAID", "PARTIALLY_REFUNDED"):
            self.assertEqual(self.calculate(replace(self.order, financial_status=status)).points, 10)

    def test_test_cancelled_guest_table(self):
        for changes, code in [({"is_test": True}, "test_order"), ({"cancelled": True}, "cancelled_order"),
                              ({"customer_id": None}, "guest")]:
            with self.subTest(code=code):
                self.assertEqual(self.calculate(replace(self.order, **changes)).breakdown["code"], code)

    def test_test_order_opt_in_preserves_other_eligibility_checks(self):
        rules = replace(self.rules, allow_test_orders=True)
        order = replace(self.order, is_test=True)
        self.assertEqual(self.calculate(order, rules).points, 10)
        for changes, code in [({"cancelled": True}, "cancelled_order"),
                              ({"customer_id": None}, "guest"),
                              ({"financial_status": "PENDING"}, "financial_status_ineligible")]:
            with self.subTest(code=code):
                self.assertEqual(self.calculate(replace(order, **changes), rules).breakdown["code"], code)

    def test_zero_points_reason_and_gift_card_clamp(self):
        result = self.calculate(replace(self.order, gift_card_paid=D("200000")))
        self.assertEqual(result.points, 0)
        self.assertEqual(result.eligible_spend, D("0"))
        self.assertEqual(result.breakdown["code"], "zero_points")

    def test_invalid_amounts_are_zero_and_noted(self):
        for amount in (None, D("-1"), D("NaN"), 1.25):
            with self.subTest(amount=amount):
                result = self.calculate(replace(self.order, lines=[replace(self.line, total_after_discounts=amount)]))
                self.assertEqual(result.points, 0)
                self.assertEqual(result.breakdown["invalid_amounts"]["line_total"], 1)

    def test_nonpositive_rate_raises(self):
        for rate in (D("0"), D("-1")):
            with self.assertRaisesMessage(ValueError, "phải lớn hơn 0"):
                self.calculate(rules=replace(self.rules, spend_per_point=rate))

    def test_tier_multiplier_applied_before_single_final_floor(self):
        order = replace(self.order, lines=[replace(self.line, total_after_discounts=D("95000"))])
        self.assertEqual(self.calculate(order, replace(self.rules, tier_multiplier=2)).points, 19)
