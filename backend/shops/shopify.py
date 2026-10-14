"""
Client Shopify tối giản.
Đặt file này tại: backend/shops/shopify.py

- get_access_token(): lấy token bằng client credentials grant, cache trong bộ nhớ
  (token sống ~24 giờ nên tự lấy lại khi gần hết hạn).
- graphql(): gọi GraphQL Admin API.

Lưu ý: cache nằm trong bộ nhớ của từng tiến trình (runserver và qcluster mỗi nơi
một bản). Ổn cho giai đoạn dev; sau này nên chuyển sang lưu ở DB/cache chung.
"""
import time
from datetime import timezone as utc_timezone
from django.utils.dateparse import parse_datetime

import httpx
from django.conf import settings

_token = {"value": None, "expires_at": 0.0}


class ShopifyError(Exception):
    pass


class ShopifyTransientError(ShopifyError):
    """Retry later through the webhook sweeper, not inside an API call."""


class DiscountCodeTaken(ShopifyError):
    pass


_throttle = {}


def clear_access_token():
    _token.update(value=None, expires_at=0.0)


def checked_post(url, **kwargs):
    try:
        response = httpx.post(url, **kwargs)
    except httpx.RequestError:
        raise ShopifyTransientError("Kết nối Shopify tạm thời bị gián đoạn.") from None
    if response.status_code == 429 or response.status_code >= 500:
        raise ShopifyTransientError(f"Shopify tạm thời không sẵn sàng (HTTP {response.status_code}).")
    return response


def get_access_token() -> str:
    # Leave sixty seconds for requests already in flight.
    if _token["value"] and time.time() < _token["expires_at"] - 60:
        return _token["value"]

    url = f"https://{settings.SHOPIFY_SHOP_DOMAIN}/admin/oauth/access_token"
    resp = checked_post(
        url,
        data={
            "grant_type": "client_credentials",
            "client_id": settings.SHOPIFY_CLIENT_ID,
            "client_secret": settings.SHOPIFY_CLIENT_SECRET,
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise ShopifyError(f"Lấy token thất bại (HTTP {resp.status_code}).")

    data = resp.json()
    _token["value"] = data["access_token"]
    _token["expires_at"] = time.time() + int(data.get("expires_in", 3600))
    return _token["value"]


def graphql(query: str, variables: dict | None = None) -> dict:
    url = (
        f"https://{settings.SHOPIFY_SHOP_DOMAIN}"
        f"/admin/api/{settings.SHOPIFY_API_VERSION}/graphql.json"
    )
    for attempt in range(2):
        wait_for_capacity()
        resp = checked_post(
            url, json={"query": query, "variables": variables or {}},
            headers={"X-Shopify-Access-Token": get_access_token()}, timeout=30,
        )
        if resp.status_code == 401:
            clear_access_token()
            if attempt == 0:
                continue
        if resp.status_code != 200:
            raise ShopifyError(f"GraphQL lỗi HTTP {resp.status_code}.")
        body = resp.json()
        remember_capacity(body.get("extensions", {}).get("cost", {}))
        errors = body.get("errors", [])
        codes = {error.get("extensions", {}).get("code") for error in errors}
        invalid_token = bool(codes & {"UNAUTHENTICATED", "INVALID_TOKEN"}) or any(
            "invalid access token" in error.get("message", "").lower() for error in errors)
        if invalid_token:
            clear_access_token()
            if attempt == 0:
                continue
        if "THROTTLED" in codes:
            raise ShopifyTransientError(redact_shopify_error(f"GraphQL errors: {errors}"))
        if errors:
            raise ShopifyError(redact_shopify_error(f"GraphQL errors: {errors}"))
        return body["data"]


def remember_capacity(cost):
    status = cost.get("throttleStatus") or {}
    if status.get("currentlyAvailable") is not None and status.get("restoreRate"):
        _throttle.update(available=status["currentlyAvailable"], restore_rate=status["restoreRate"],
                         next_cost=cost.get("requestedQueryCost") or 1, observed_at=time.monotonic())


def wait_for_capacity():
    if not _throttle:
        return
    restored = (time.monotonic() - _throttle["observed_at"]) * _throttle["restore_rate"]
    deficit = _throttle["next_cost"] - _throttle["available"] - restored
    if deficit > 0:
        time.sleep(min(2, deficit / _throttle["restore_rate"]))


# Shopify Admin GraphQL 2026-10. Keep the slash-topic mapping in one place.
WEBHOOK_TOPICS = {
    "orders/paid": "ORDERS_PAID",
    "orders/cancelled": "ORDERS_CANCELLED",
    "refunds/create": "REFUNDS_CREATE",
    "customers/create": "CUSTOMERS_CREATE",
    "customers/update": "CUSTOMERS_UPDATE",
    "app/uninstalled": "APP_UNINSTALLED",
}

WEBHOOK_SUBSCRIPTIONS_QUERY = """
query WebhookSubscriptions($after: String) {
  webhookSubscriptions(first: 100, after: $after) {
    nodes { id topic uri format }
    pageInfo { hasNextPage endCursor }
  }
}
"""
WEBHOOK_CREATE_MUTATION = """
mutation WebhookCreate($topic: WebhookSubscriptionTopic!, $subscription: WebhookSubscriptionInput!) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $subscription) {
    webhookSubscription { id topic uri }
    userErrors { field message }
  }
}
"""
WEBHOOK_UPDATE_MUTATION = """
mutation WebhookUpdate($id: ID!, $subscription: WebhookSubscriptionInput!) {
  webhookSubscriptionUpdate(id: $id, webhookSubscription: $subscription) {
    webhookSubscription { id topic uri }
    userErrors { field message }
  }
}
"""
WEBHOOK_DELETE_MUTATION = """
mutation WebhookDelete($id: ID!) {
  webhookSubscriptionDelete(id: $id) {
    deletedWebhookSubscriptionId
    userErrors { field message }
  }
}
"""


def list_webhook_subscriptions():
    subscriptions = []
    after = None
    seen_cursors = set()
    while True:
        page = graphql(WEBHOOK_SUBSCRIPTIONS_QUERY, {"after": after})["webhookSubscriptions"]
        subscriptions.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return subscriptions
        after = page["pageInfo"]["endCursor"]
        if not after or after in seen_cursors:
            raise ShopifyError("Shopify trả về con trỏ phân trang không hợp lệ.")
        seen_cursors.add(after)


def webhook_mutation(query, variables, operation):
    import json

    result = graphql(query, variables)[operation]
    if result.get("userErrors"):
        raise ShopifyError(json.dumps(result["userErrors"], ensure_ascii=False))
    return result


def create_webhook_subscription(topic, callback_url):
    return webhook_mutation(WEBHOOK_CREATE_MUTATION, {
        "topic": WEBHOOK_TOPICS[topic],
        # callbackUrl remains valid (deprecated) in 2026-10; uri is the new name.
        "subscription": {"format": "JSON", "callbackUrl": callback_url},
    }, "webhookSubscriptionCreate")["webhookSubscription"]


def update_webhook_subscription(subscription_id, callback_url):
    return webhook_mutation(WEBHOOK_UPDATE_MUTATION, {
        "id": subscription_id,
        "subscription": {"format": "JSON", "callbackUrl": callback_url},
    }, "webhookSubscriptionUpdate")["webhookSubscription"]


def delete_webhook_subscription(subscription_id):
    return webhook_mutation(WEBHOOK_DELETE_MUTATION, {"id": subscription_id},
                            "webhookSubscriptionDelete")["deletedWebhookSubscriptionId"]


def redact_shopify_error(exception):
    message = str(exception)
    for value in (settings.SHOPIFY_CLIENT_SECRET, _token["value"]):
        if value:
            message = message.replace(value, "[đã ẩn]")
    return message


ORDER_LINE_FRAGMENT = """
fragment EarnLine on LineItem {
  id quantity isGiftCard
  originalTotalSet { shopMoney { amount currencyCode } }
  discountAllocations { allocatedAmountSet { shopMoney { amount currencyCode } } }
  taxLines { priceSet { shopMoney { amount currencyCode } } }
  product { id collections(first: 10) { nodes { id } pageInfo { hasNextPage endCursor } } }
}
"""
ORDER_QUERY = """
query EarnOrder($id: ID!) {
  order(id: $id) {
    id createdAt test cancelledAt displayFinancialStatus taxesIncluded currencyCode sourceName
    customer { id email createdAt }
    customAttributes { key value }
    lineItems(first: 25) { nodes { ...EarnLine } pageInfo { hasNextPage endCursor } }
    currentShippingPriceSet { shopMoney { amount currencyCode } }
    shippingLines(first: 25) {
      nodes {
        discountedPriceSet { shopMoney { amount currencyCode } }
        taxLines { priceSet { shopMoney { amount currencyCode } } }
      }
      pageInfo { hasNextPage endCursor }
    }
    transactions { id gateway kind status amountSet { shopMoney { amount currencyCode } } }
    refunds { id }
  }
}
""" + ORDER_LINE_FRAGMENT
ORDER_LINES_QUERY = """
query EarnOrderLines($id: ID!, $after: String) {
  resource: order(id: $id) {
    lineItems(first: 25, after: $after) { nodes { ...EarnLine } pageInfo { hasNextPage endCursor } }
  }
}
""" + ORDER_LINE_FRAGMENT
PRODUCT_COLLECTIONS_QUERY = """
query EarnCollections($id: ID!, $after: String) {
  resource: product(id: $id) {
    collections(first: 100, after: $after) { nodes { id } pageInfo { hasNextPage endCursor } }
  }
}
"""
SHIPPING_LINES_QUERY = """
query EarnShipping($id: ID!, $after: String) {
  resource: order(id: $id) {
    shippingLines(first: 25, after: $after) {
      nodes {
        discountedPriceSet { shopMoney { amount currencyCode } }
        taxLines { priceSet { shopMoney { amount currencyCode } } }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""
REFUND_LINES_QUERY = """
query EarnRefundLines($id: ID!, $after: String) {
  resource: node(id: $id) {
    ... on Refund {
      refundLineItems(first: 100, after: $after) {
        nodes {
          lineItem { id } quantity
          subtotalSet { shopMoney { amount currencyCode } }
          totalTaxSet { shopMoney { amount currencyCode } }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""
REFUND_SHIPPING_QUERY = """
query EarnRefundShipping($id: ID!, $after: String) {
  resource: node(id: $id) {
    ... on Refund {
      refundShippingLines(first: 100, after: $after) {
        nodes {
          subtotalAmountSet { shopMoney { amount currencyCode } }
          taxAmountSet { shopMoney { amount currencyCode } }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""
CUSTOMER_PAGE_QUERY = """
query SyncCustomers($after: String, $first: Int!) {
  customers(first: $first, after: $after, sortKey: ID) {
    nodes { id email firstName lastName state locale createdAt emailMarketingConsent { marketingState } }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def connection_nodes(resource_id, field, query, initial=None, max_nodes=None):
    nodes, after, seen = [], None, set()
    page = initial
    while True:
        if page is None:
            resource = graphql(query, {"id": resource_id, "after": after})["resource"]
            if not resource or field not in resource:
                raise ShopifyError("Dữ liệu Shopify thay đổi trong khi đọc phân trang; cần thử lại.")
            page = resource[field]
        nodes.extend(page["nodes"])
        if max_nodes is not None and len(nodes) > max_nodes:
            raise ShopifyError(f"Đơn hàng vượt quá giới hạn {max_nodes} dòng hàng được hỗ trợ.")
        if not page["pageInfo"]["hasNextPage"]:
            return nodes
        after = page["pageInfo"]["endCursor"]
        if not after or after in seen:
            raise ShopifyError("Shopify trả về con trỏ phân trang không hợp lệ.")
        seen.add(after)
        page = None


def fetch_order(order_id: int):
    order = graphql(ORDER_QUERY, {"id": f"gid://shopify/Order/{order_id}"})["order"]
    if order is None:
        return None
    order["lineItems"]["nodes"] = connection_nodes(
        order["id"], "lineItems", ORDER_LINES_QUERY, order["lineItems"], max_nodes=250)
    collections_cache = {}
    for line in order["lineItems"]["nodes"]:
        product = line.get("product")
        if product:
            if product["id"] not in collections_cache:
                collections_cache[product["id"]] = connection_nodes(
                    product["id"], "collections", PRODUCT_COLLECTIONS_QUERY, product["collections"])
            product["collections"]["nodes"] = collections_cache[product["id"]]
    order["shippingLines"]["nodes"] = connection_nodes(
        order["id"], "shippingLines", SHIPPING_LINES_QUERY, order["shippingLines"])
    for refund in order["refunds"]:
        refund["refundLineItems"] = {"nodes": connection_nodes(
            refund["id"], "refundLineItems", REFUND_LINES_QUERY)}
        refund["refundShippingLines"] = {"nodes": connection_nodes(
            refund["id"], "refundShippingLines", REFUND_SHIPPING_QUERY)}
    return order


def fetch_customer_page(cursor, page_size=100):
    if not 1 <= page_size <= 250:
        raise ValueError("Số khách hàng mỗi trang phải từ 1 đến 250.")
    page = graphql(CUSTOMER_PAGE_QUERY, {"after": cursor, "first": page_size})["customers"]
    next_cursor = page["pageInfo"]["endCursor"] if page["pageInfo"]["hasNextPage"] else None
    if page["pageInfo"]["hasNextPage"] and (not next_cursor or next_cursor == cursor):
        raise ShopifyError("Shopify trả về con trỏ khách hàng không hợp lệ.")
    return page["nodes"], next_cursor


def numeric_id(value):
    try:
        number = int(str(value).rsplit("/", 1)[-1])
        if number <= 0 or isinstance(value, bool) or number > 9223372036854775807:
            raise ValueError
        return number
    except (ValueError, TypeError):
        raise ValueError("Mã Shopify phải là số nguyên dương hợp lệ.") from None


def map_order_snapshot(raw, include_tax=False):
    """The only Shopify-to-domain mapping. Refunds/shipping use the chosen tax basis."""
    from decimal import Decimal
    from loyalty.services.calculation import OrderLine, OrderSnapshot, RefundLine, RefundSummary

    currency = raw["currencyCode"]
    created_at = None
    customer_created_at = None
    if (raw.get("customer") or {}).get("createdAt"):
        try:
            customer_created_at = parse_datetime(raw["customer"]["createdAt"])
            if customer_created_at is None or customer_created_at.utcoffset() is None:
                raise ValueError()
            customer_created_at = customer_created_at.astimezone(utc_timezone.utc)
        except (ValueError, TypeError):
            raise ShopifyError("Customer createdAt must have a timezone.") from None
    if raw.get("createdAt"):
        try:
            created_at = parse_datetime(raw["createdAt"])
            if created_at is None or created_at.utcoffset() is None:
                raise ValueError()
            created_at = created_at.astimezone(utc_timezone.utc)
        except (TypeError, ValueError):
            raise ShopifyError("Order createdAt must be a timezone-aware date.") from None

    def money(bag):
        if not bag or not bag.get("shopMoney") or bag["shopMoney"].get("amount") is None:
            return None
        if bag["shopMoney"]["currencyCode"] != currency:
            raise ShopifyError("Tiền tệ shopMoney không nhất quán trong đơn hàng.")
        amount = bag["shopMoney"]["amount"]
        if not isinstance(amount, str):
            raise ShopifyError("Số tiền Shopify phải được truyền dưới dạng chuỗi.")
        value = Decimal(amount)
        if not value.is_finite():
            raise ShopifyError("Số tiền Shopify không hợp lệ.")
        return value

    def amount(bag):
        value = money(bag)
        return max(Decimal("0"), value) if value is not None else Decimal("0")

    lines = []
    for line in raw["lineItems"]["nodes"]:
        product = line.get("product")
        total = money(line.get("originalTotalSet"))
        discounts = sum((amount(allocation["allocatedAmountSet"])
                         for allocation in line["discountAllocations"]), Decimal("0"))
        lines.append(OrderLine(
            numeric_id(line["id"]), numeric_id(product["id"]) if product else None,
            [numeric_id(item["id"]) for item in product["collections"]["nodes"]] if product else [],
            line["isGiftCard"], line["quantity"], total - discounts if total is not None else None,
            sum((amount(tax["priceSet"]) for tax in line["taxLines"]), Decimal("0")),
        ))
    def tax_basis(subtotal, tax):
        # Refund subtotal tax inclusion follows the order basis; see step5-revoke.md.
        if raw["taxesIncluded"] and not include_tax:
            return max(Decimal("0"), subtotal - tax)
        if not raw["taxesIncluded"] and include_tax:
            return subtotal + tax
        return subtotal

    refunds, summaries = [], []
    refunded_shipping = Decimal("0")
    for refund in raw.get("refunds", []):
        refund_lines = refund["refundLineItems"]["nodes"]
        shipping_lines = refund.get("refundShippingLines", {}).get("nodes", [])
        if refund.get("id"):
            summaries.append(RefundSummary(numeric_id(refund["id"]), bool(refund_lines), bool(shipping_lines)))
        for line in refund_lines:
            subtotal, tax = amount(line["subtotalSet"]), amount(line["totalTaxSet"])
            refunds.append(RefundLine(numeric_id(line["lineItem"]["id"]), line["quantity"],
                                      tax_basis(subtotal, tax)))
        refunded_shipping += sum((tax_basis(amount(item["subtotalAmountSet"]), amount(item["taxAmountSet"]))
                                  for item in shipping_lines), Decimal("0"))
    shipping_before_refunds = sum((tax_basis(amount(line["discountedPriceSet"]),
        sum((amount(tax["priceSet"]) for tax in line["taxLines"]), Decimal("0")))
        for line in raw["shippingLines"]["nodes"]), Decimal("0"))
    # Cap shipping refunds at discounted shipping; never deduct them twice from a net field.
    shipping = max(Decimal("0"), shipping_before_refunds - refunded_shipping)
    gift_paid = Decimal("0")
    for payment in raw.get("transactions", []):
        if payment["status"] == "SUCCESS" and payment["gateway"].lower() == "gift_card":
            if payment["kind"] in ("SALE", "CAPTURE"):
                gift_paid += amount(payment["amountSet"])
            elif payment["kind"] == "REFUND":
                gift_paid -= amount(payment["amountSet"])
    return OrderSnapshot(
        numeric_id(raw["id"]), raw["test"], raw["cancelledAt"] is not None,
        raw["displayFinancialStatus"], raw["taxesIncluded"],
        numeric_id(raw["customer"]["id"]) if raw.get("customer") else None,
        raw.get("sourceName") or "", lines, shipping, max(Decimal("0"), gift_paid), refunds, currency, summaries,
        created_at=created_at,
        custom_attributes={item["key"]: item.get("value", "") for item in raw.get("customAttributes", [])},
        customer_email=(raw.get("customer") or {}).get("email"),
        customer_created_at=customer_created_at,
    )


def _discount_graphql(document, variables):
    # Do not propagate remote messages that might echo a private discount code.
    try:
        return graphql(document, variables)
    except ShopifyTransientError:
        raise ShopifyTransientError("SHOPIFY_TRANSIENT: Shopify tạm thời không sẵn sàng.") from None
    except ShopifyError:
        raise ShopifyError("SHOPIFY_ERROR: Không thể xử lý mã giảm giá trên Shopify.") from None


def find_discount_by_code(code):
    from .discounts import FIND_DISCOUNT, map_discount
    return map_discount(_discount_graphql(FIND_DISCOUNT, {"code": code})["codeDiscountNodeByCode"])


def create_discount_code(code, settings):
    from .discounts import CREATE_DISCOUNT, discount_input
    result = _discount_graphql(CREATE_DISCOUNT, {"input": discount_input(code, settings)})["discountCodeBasicCreate"]
    errors = result.get("userErrors", [])
    if errors:
        if any(error.get("code") in ("TAKEN", "DUPLICATE")
               and "code" in error.get("field", []) for error in errors):
            raise DiscountCodeTaken("CODE_TAKEN: Mã giảm giá đã tồn tại.")
        raise ShopifyError("DISCOUNT_REJECTED: Shopify từ chối tạo mã giảm giá.")
    if not result.get("codeDiscountNode"):
        raise ShopifyTransientError("SHOPIFY_TRANSIENT: Chưa nhận được mã giảm giá đã tạo.")
    return result["codeDiscountNode"]["id"]


def deactivate_discount(discount_id):
    from .discounts import DEACTIVATE_DISCOUNT, DISCOUNT_EXISTS
    if not _discount_graphql(DISCOUNT_EXISTS, {"id": discount_id})["codeDiscountNode"]:
        return True
    result = _discount_graphql(DEACTIVATE_DISCOUNT, {"id": discount_id})["discountCodeDeactivate"]
    errors = result.get("userErrors", [])
    if errors or not result.get("codeDiscountNode"):
        # DiscountErrorCode has no NOT_FOUND variant. Re-query handles a concurrent deletion.
        if not _discount_graphql(DISCOUNT_EXISTS, {"id": discount_id})["codeDiscountNode"]:
            return True
        if errors:
            raise ShopifyError("DEACTIVATE_FAILED: Không thể vô hiệu hóa mã giảm giá.")
        raise ShopifyTransientError("DEACTIVATE_UNCONFIRMED: Chưa xác nhận vô hiệu hóa mã.")
    return True
