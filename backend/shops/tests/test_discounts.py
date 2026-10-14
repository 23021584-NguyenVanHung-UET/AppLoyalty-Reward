from unittest.mock import patch

from django.test import SimpleTestCase

from shops.discounts import CREATE_DISCOUNT, DEACTIVATE_DISCOUNT, FIND_DISCOUNT, discount_input, map_discount
from shops.shopify import (DiscountCodeTaken, ShopifyError, ShopifyTransientError,
                          create_discount_code, deactivate_discount, find_discount_by_code)


def raw_discount():
    return {"id": "gid://shopify/DiscountCodeNode/123", "codeDiscount": {
        "__typename": "DiscountCodeBasic", "title": "Loyalty", "startsAt": "2026-10-12T00:00:00Z",
        "endsAt": "2026-11-12T00:00:00Z", "usageLimit": 1, "appliesOncePerCustomer": True,
        "combinesWith": {"orderDiscounts": False, "productDiscounts": False, "shippingDiscounts": False},
        "customerSelection": {"__typename": "DiscountCustomers", "customers": [{"id": "gid://shopify/Customer/301"}]},
        "customerGets": {"appliesOnOneTimePurchase": True, "appliesOnSubscription": False,
            "items": {"__typename": "AllDiscountItems", "allItems": True},
            "value": {"__typename": "DiscountAmount", "amount": {"amount": "5.00", "currencyCode": "USD"},
                      "appliesOnEachItem": False}},
        "minimumRequirement": {"__typename": "DiscountMinimumSubtotal",
                               "greaterThanOrEqualToSubtotal": {"amount": "20.00", "currencyCode": "USD"}}}}


class DiscountClientTests(SimpleTestCase):
    def settings(self, **changes):
        result = {"title": "Loyalty", "starts_at": "2026-10-12T00:00:00Z", "ends_at": "2026-11-12T00:00:00Z",
            "customer_id": "gid://shopify/Customer/301", "discount_type": "FIXED_AMOUNT",
            "discount_value": "5.00", "min_order_amount": "20.00", "currency": "USD"}
        result.update(changes)
        return result

    def test_fixed_mapping_customer_minimum_and_combinations(self):
        value = discount_input("test-code", self.settings())
        self.assertEqual(value["customerSelection"], {"customers": {"add": ["gid://shopify/Customer/301"]}})
        self.assertEqual(value["customerGets"]["value"], {"discountAmount": {"amount": "5.00", "appliesOnEachItem": False}})
        self.assertEqual(value["minimumRequirement"], {"subtotal": {"greaterThanOrEqualToSubtotal": "20.00"}})
        self.assertTrue(all(item is False for item in value["combinesWith"].values()))
        self.assertEqual(value["usageLimit"], 1)

    def test_percentage_fraction_no_minimum(self):
        result = discount_input("test-code", self.settings(discount_type="PERCENTAGE", discount_value="0.10", min_order_amount=None))
        self.assertEqual(result["customerGets"]["value"], {"percentage": 0.1})
        self.assertNotIn("minimumRequirement", result)

    def test_lookup_maps_fixed_percentage_and_rejects_other_discount_shapes(self):
        raw = raw_discount()
        self.assertEqual(map_discount(raw)["settings"]["discount_value"], "5.00")
        raw["codeDiscount"]["customerGets"]["value"] = {"__typename": "DiscountPercentage", "percentage": 0.1}
        self.assertEqual(map_discount(raw)["settings"]["discount_value"], "0.1")
        self.assertIsNone(map_discount(None))
        for field, val in [("__typename", "DiscountCodeFreeShipping"),
                           ("customerSelection", {"__typename": "AllDiscountCustomers"})]:
            raw = raw_discount()
            raw["codeDiscount"][field] = val
            self.assertIsNone(map_discount(raw)["settings"])

    @patch("shops.shopify.graphql")
    def test_lookup_variables_and_null(self, graphql):
        graphql.return_value = {"codeDiscountNodeByCode": raw_discount()}
        self.assertEqual(find_discount_by_code("test-code")["id"], raw_discount()["id"])
        graphql.assert_called_once_with(FIND_DISCOUNT, {"code": "test-code"})
        graphql.return_value = {"codeDiscountNodeByCode": None}
        self.assertIsNone(find_discount_by_code("test-code"))

    @patch("shops.shopify.graphql")
    def test_create_input_success_code_taken_and_permanent_error(self, graphql):
        graphql.return_value = {"discountCodeBasicCreate": {"codeDiscountNode": {"id": "gid"}, "userErrors": []}}
        self.assertEqual(create_discount_code("test-code", self.settings()), "gid")
        graphql.assert_called_once_with(CREATE_DISCOUNT, {"input": discount_input("test-code", self.settings())})
        for code, exception in [("TAKEN", DiscountCodeTaken), ("INVALID", ShopifyError)]:
            graphql.return_value = {"discountCodeBasicCreate": {"codeDiscountNode": None,
                "userErrors": [{"field": ["basicCodeDiscount", "code"], "code": code, "message": "private-data"}]}}
            with self.assertRaises(exception) as raised:
                create_discount_code("test-code", self.settings())
            self.assertNotIn("private-data", str(raised.exception))

    @patch("shops.shopify.graphql")
    def test_transient_and_permanent_errors_are_sanitized(self, graphql):
        for exception in [ShopifyError, ShopifyTransientError]:
            graphql.side_effect = exception("secret private-code")
            with self.assertRaises(exception) as raised:
                find_discount_by_code("test-code")
            self.assertNotIn("secret", str(raised.exception))
            self.assertNotIn("private-code", str(raised.exception))

    @patch("shops.shopify.graphql")
    def test_deactivate_absent_success_missing_response_and_rejection(self, graphql):
        graphql.return_value = {"codeDiscountNode": None}
        self.assertTrue(deactivate_discount("gid"))
        self.assertEqual(graphql.call_count, 1)
        graphql.side_effect = [{"codeDiscountNode": {"id": "gid"}},
            {"discountCodeDeactivate": {"codeDiscountNode": {"id": "gid"}, "userErrors": []}}]
        self.assertTrue(deactivate_discount("gid"))
        self.assertEqual(graphql.call_args.args[0], DEACTIVATE_DISCOUNT)
        for errors, exception in [([], ShopifyTransientError), ([{"code": "INVALID"}], ShopifyError)]:
            graphql.side_effect = [{"codeDiscountNode": {"id": "gid"}},
                {"discountCodeDeactivate": {"codeDiscountNode": None, "userErrors": errors}},
                {"codeDiscountNode": {"id": "gid"}}]
            with self.assertRaises(exception):
                deactivate_discount("gid")

    @patch("shops.shopify.graphql")
    def test_deactivate_concurrent_deletion_is_success(self, graphql):
        graphql.side_effect = [{"codeDiscountNode": {"id": "gid"}},
            {"discountCodeDeactivate": {"codeDiscountNode": None, "userErrors": [{"code": "INVALID"}]}},
            {"codeDiscountNode": None}]
        self.assertTrue(deactivate_discount("gid"))
