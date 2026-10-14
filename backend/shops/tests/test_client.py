from copy import deepcopy
from unittest.mock import Mock, patch

import httpx
from django.test import SimpleTestCase, override_settings

from shops import shopify
from .fixtures import connection, money, order_line, raw_order, raw_refund


def response(status=200, body=None):
    return Mock(status_code=status, json=Mock(return_value=body or {"data": {"ok": True}}))


@override_settings(SHOPIFY_CLIENT_SECRET="test-secret", SHOPIFY_CLIENT_ID="test-id",
                   SHOPIFY_SHOP_DOMAIN="test.myshopify.com", SHOPIFY_API_VERSION="2026-10")
class ClientTests(SimpleTestCase):
    def setUp(self):
        patch.dict(shopify._token, {"value": "cached-test-token", "expires_at": 9999999999}).start()
        self.throttle = patch.dict(shopify._throttle, {}, clear=True).start()
        self.addCleanup(patch.stopall)

    @patch("shops.shopify.httpx.post")
    def test_401_clears_token_and_retries_once(self, post):
        post.side_effect = [response(401), response(body={"access_token": "new-test-token", "expires_in": 3600}), response()]
        self.assertEqual(shopify.graphql("query { shop { id } }"), {"ok": True})
        self.assertEqual(post.call_count, 3)
        self.assertEqual(post.call_args_list[0].kwargs["headers"]["X-Shopify-Access-Token"], "cached-test-token")
        self.assertEqual(post.call_args_list[2].kwargs["headers"]["X-Shopify-Access-Token"], "new-test-token")

    @patch("shops.shopify.httpx.post")
    def test_second_401_stops(self, post):
        post.side_effect = [response(401), response(body={"access_token": "new-test-token"}), response(401)]
        with self.assertRaises(shopify.ShopifyError):
            shopify.graphql("query { shop { id } }")
        self.assertEqual(post.call_count, 3)
        self.assertIsNone(shopify._token["value"])

    @patch("shops.shopify.httpx.post")
    def test_graphql_invalid_token_refreshes(self, post):
        post.side_effect = [response(body={"errors": [{"message": "Invalid access token", "extensions": {"code": "UNAUTHENTICATED"}}]}),
                            response(body={"access_token": "fresh-test-token"}), response()]
        shopify.graphql("query { shop { id } }")
        self.assertEqual(post.call_count, 3)

    @patch("shops.shopify.httpx.post")
    def test_http_transient_classification(self, post):
        for status in (429, 500, 502, 503):
            with self.subTest(status=status):
                post.return_value = response(status)
                with self.assertRaises(shopify.ShopifyTransientError):
                    shopify.graphql("query { shop { id } }")

    @patch("shops.shopify.httpx.post")
    def test_timeout_connection_errors_are_transient(self, post):
        for exception in (httpx.ReadTimeout("private"), httpx.ConnectError("private")):
            post.side_effect = exception
            with self.assertRaises(shopify.ShopifyTransientError) as raised:
                shopify.graphql("query { shop { id } }")
            self.assertNotIn("private", str(raised.exception))

    @patch("shops.shopify.httpx.post")
    def test_token_endpoint_transient_classification(self, post):
        shopify.clear_access_token()
        post.return_value = response(503)
        with self.assertRaises(shopify.ShopifyTransientError):
            shopify.get_access_token()

    @patch("shops.shopify.httpx.post")
    def test_graphql_throttled_transient_permanent_error_compatible(self, post):
        for code, expected in [("THROTTLED", shopify.ShopifyTransientError), ("ACCESS_DENIED", shopify.ShopifyError)]:
            post.return_value = response(body={"errors": [{"message": "denied", "extensions": {"code": code}}]})
            with self.assertRaises(expected):
                shopify.graphql("query { shop { id } }")
        self.assertTrue(issubclass(shopify.ShopifyTransientError, shopify.ShopifyError))

    @patch("shops.shopify.time.sleep")
    @patch("shops.shopify.httpx.post")
    def test_throttle_wait_is_bounded(self, post, sleep):
        post.return_value = response(body={"data": {"ok": True}, "extensions": {"cost": {
            "requestedQueryCost": 100, "throttleStatus": {"currentlyAvailable": 0, "restoreRate": 1}}}})
        shopify.graphql("query { shop { id } }")
        shopify.graphql("query { shop { id } }")
        self.assertEqual(sleep.call_count, 1)
        self.assertLessEqual(sleep.call_args.args[0], 2)


class FetchAndMappingTests(SimpleTestCase):
    @patch("shops.shopify.graphql")
    def test_customer_page_cursor(self, graphql):
        graphql.return_value = {"customers": connection([{"id": "gid://shopify/Customer/1"}], True, "cursor-2")}
        nodes, cursor = shopify.fetch_customer_page("cursor-1")
        self.assertEqual(cursor, "cursor-2")
        self.assertEqual(len(nodes), 1)
        self.assertEqual(graphql.call_args.args[1], {"after": "cursor-1", "first": 100})

    @patch("shops.shopify.graphql")
    def test_order_null(self, graphql):
        graphql.return_value = {"order": None}
        self.assertIsNone(shopify.fetch_order(1001))

    @patch("shops.shopify.graphql")
    def test_order_line_items_up_to_250_and_nested_collection_pagination(self, graphql):
        raw = raw_order()
        lines = [order_line(n) for n in range(1, 251)]
        lines[0]["product"]["collections"] = connection([{"id": "gid://shopify/Collection/1"}], True, "collection-cursor")
        raw["lineItems"] = connection(lines[:25], True, "25")
        def query(document, variables):
            if document == shopify.ORDER_QUERY:
                return {"order": deepcopy(raw)}
            if document == shopify.ORDER_LINES_QUERY:
                start = int(variables["after"])
                return {"resource": {"lineItems": connection(deepcopy(lines[start:start + 25]), start + 25 < 250, str(start + 25))}}
            if document == shopify.PRODUCT_COLLECTIONS_QUERY:
                return {"resource": {"collections": connection([{"id": "gid://shopify/Collection/2"}])}}
            self.fail("Unexpected query")
        graphql.side_effect = query
        result = shopify.fetch_order(1001)
        self.assertEqual(len(result["lineItems"]["nodes"]), 250)
        self.assertEqual(len(result["lineItems"]["nodes"][0]["product"]["collections"]["nodes"]), 2)

    @patch("shops.shopify.graphql")
    def test_order_more_than_250_fails_loudly(self, graphql):
        raw = raw_order()
        raw["lineItems"] = connection([order_line(n) for n in range(251)])
        graphql.return_value = {"order": raw}
        with self.assertRaisesMessage(shopify.ShopifyError, "250"):
            shopify.fetch_order(1001)

    @patch("shops.shopify.graphql")
    def test_refund_line_pagination(self, graphql):
        raw = raw_order()
        raw["refunds"] = [{"id": "gid://shopify/Refund/501"}]
        refund_line = {"lineItem": {"id": "gid://shopify/LineItem/101"}, "quantity": 1,
                       "subtotalSet": money("10000"), "totalTaxSet": money("0")}
        graphql.side_effect = [{"order": raw},
                               {"resource": {"refundLineItems": connection([refund_line], True, "refund-cursor")}},
                               {"resource": {"refundLineItems": connection([refund_line])}},
                               {"resource": {"refundShippingLines": connection([])}}]
        result = shopify.fetch_order(1001)
        self.assertEqual(len(result["refunds"][0]["refundLineItems"]["nodes"]), 2)

    def test_mapping_tax_inclusive_all_allocations_gift_card_and_refund(self):
        from decimal import Decimal as D
        raw = raw_order()
        raw["taxesIncluded"] = True
        raw["lineItems"]["nodes"][0] = order_line(total="132000", tax="10000", discount="11000")
        raw["lineItems"]["nodes"][0]["discountAllocations"].append({"allocatedAmountSet": money("11000")})
        raw["transactions"] = [
            {"gateway": "gift_card", "kind": "SALE", "status": "SUCCESS", "amountSet": money("30000")},
            {"gateway": "gift_card", "kind": "AUTHORIZATION", "status": "SUCCESS", "amountSet": money("30000")},
            {"gateway": "gift_card", "kind": "SALE", "status": "FAILURE", "amountSet": money("90000")},
            {"gateway": "gift_card", "kind": "REFUND", "status": "SUCCESS", "amountSet": money("5000")},
        ]
        raw["refunds"] = [{"refundLineItems": {"nodes": [{"lineItem": {"id": "gid://shopify/LineItem/101"},
            "quantity": 1, "subtotalSet": money("22000"), "totalTaxSet": money("2000")}]} }]
        snapshot = shopify.map_order_snapshot(raw)
        self.assertEqual(snapshot.lines[0].total_after_discounts, D("110000"))
        self.assertEqual(snapshot.lines[0].tax_amount, D("10000"))
        self.assertEqual(snapshot.gift_card_paid, D("25000"))
        self.assertEqual(snapshot.refund_lines[0].subtotal, D("20000"))
        self.assertEqual(snapshot.channel, "web")
        self.assertEqual(snapshot.customer_id, 301)

    def test_shipping_tax_and_refund_normalized(self):
        from decimal import Decimal as D
        raw = raw_order()
        raw["taxesIncluded"] = True
        raw["currentShippingPriceSet"] = money("11000")
        raw["shippingLines"] = connection([{"discountedPriceSet": money("22000"),
                                            "taxLines": [{"priceSet": money("2000")}]}])
        raw["refunds"] = [{"refundLineItems": connection([]),
                           "refundShippingLines": connection([{"subtotalAmountSet": money("11000"),
                                                                 "taxAmountSet": money("1000")}])}]
        self.assertEqual(shopify.map_order_snapshot(raw).shipping_after_discounts, D("10000"))
        self.assertEqual(shopify.map_order_snapshot(raw, include_tax=True).shipping_after_discounts, D("11000"))

    def test_currency_mismatch_fails_before_earning(self):
        raw = raw_order()
        raw["lineItems"]["nodes"][0]["originalTotalSet"] = money("100000", "USD")
        with self.assertRaises(shopify.ShopifyError):
            shopify.map_order_snapshot(raw)

    def test_refund_tax_basis_for_inclusive_and_exclusive_prices(self):
        from decimal import Decimal as D
        for inclusive, include_tax, expected in [(True, False, "20000"), (True, True, "22000"),
                                                  (False, False, "22000"), (False, True, "24000")]:
            with self.subTest(inclusive=inclusive, include_tax=include_tax):
                raw = raw_order()
                raw["taxesIncluded"] = inclusive
                raw["refunds"] = [raw_refund(subtotal="22000", tax="2000")]
                snapshot = shopify.map_order_snapshot(raw, include_tax=include_tax)
                self.assertEqual(snapshot.refund_lines[0].subtotal, D(expected))
                self.assertEqual(snapshot.refund_summaries[0].refund_id, 501)

    def test_shipping_refunds_net_once_cap_and_separate_tax(self):
        from decimal import Decimal as D
        raw = raw_order()
        raw["shippingLines"] = connection([{"discountedPriceSet": money("20000"),
                                             "taxLines": [{"priceSet": money("2000")}]}])
        raw["currentShippingPriceSet"] = money("10000")
        raw["refunds"] = [raw_refund(subtotal=None, shipping="10000", shipping_tax="1000")]
        self.assertEqual(shopify.map_order_snapshot(raw).shipping_after_discounts, D("10000"))
        self.assertEqual(shopify.map_order_snapshot(raw, include_tax=True).shipping_after_discounts, D("11000"))
        raw["refunds"].append(raw_refund(502, subtotal=None, shipping="9999999"))
        self.assertEqual(shopify.map_order_snapshot(raw).shipping_after_discounts, D("0"))

    def test_manual_refund_shape_is_preserved_without_error(self):
        raw = raw_order()
        raw["refunds"] = [raw_refund(subtotal=None)]
        snapshot = shopify.map_order_snapshot(raw)
        self.assertEqual(snapshot.refund_lines, [])
        self.assertFalse(snapshot.refund_summaries[0].has_line_items)
        self.assertFalse(snapshot.refund_summaries[0].has_shipping)
