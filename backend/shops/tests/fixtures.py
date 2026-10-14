def money(value, currency="VND"):
    return {"shopMoney": {"amount": str(value), "currencyCode": currency}}


def connection(nodes, has_next=False, cursor=None):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": has_next, "endCursor": cursor}}


def order_line(number=101, total="100000", tax="0", discount="0"):
    return {"id": f"gid://shopify/LineItem/{number}", "quantity": 1, "isGiftCard": False,
            "originalTotalSet": money(total),
            "discountAllocations": [{"allocatedAmountSet": money(discount)}],
            "taxLines": [{"priceSet": money(tax)}],
            "product": {"id": "gid://shopify/Product/201", "collections": connection([])}}


def raw_order(number=1001):
    return {"id": f"gid://shopify/Order/{number}", "test": False, "cancelledAt": None,
            "displayFinancialStatus": "PAID", "taxesIncluded": False, "currencyCode": "VND",
            "sourceName": "web", "customer": {"id": "gid://shopify/Customer/301"},
            "lineItems": connection([order_line()]), "currentShippingPriceSet": money("20000"),
            "shippingLines": connection([{"discountedPriceSet": money("20000"), "taxLines": []}]),
            "transactions": [], "refunds": []}


def raw_refund(number=501, subtotal="33000", tax="0", shipping=None, shipping_tax="0", line_id=101):
    return {"id": f"gid://shopify/Refund/{number}",
            "refundLineItems": connection([] if subtotal is None else [{
                "lineItem": {"id": f"gid://shopify/LineItem/{line_id}"}, "quantity": 1,
                "subtotalSet": money(subtotal), "totalTaxSet": money(tax)}]),
            "refundShippingLines": connection([] if shipping is None else [{
                "subtotalAmountSet": money(shipping), "taxAmountSet": money(shipping_tax)}])}
