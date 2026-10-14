"""Discount GraphQL documents and isolated input/output mapping."""
from decimal import Decimal

CREATE_DISCOUNT = """
mutation LoyaltyDiscountCreate($input: DiscountCodeBasicInput!) {
  discountCodeBasicCreate(basicCodeDiscount: $input) {
    codeDiscountNode { id }
    userErrors { field code }
  }
}
"""
FIND_DISCOUNT = """
query LoyaltyDiscountByCode($code: String!) {
  codeDiscountNodeByCode(code: $code) {
    id
    codeDiscount {
      __typename
      ... on DiscountCodeBasic {
        title startsAt endsAt usageLimit appliesOncePerCustomer
        combinesWith { orderDiscounts productDiscounts shippingDiscounts }
        customerSelection { __typename ... on DiscountCustomers { customers { id } } }
        customerGets {
          appliesOnOneTimePurchase appliesOnSubscription
          items { __typename ... on AllDiscountItems { allItems } }
          value {
            __typename
            ... on DiscountAmount { amount { amount currencyCode } appliesOnEachItem }
            ... on DiscountPercentage { percentage }
          }
        }
        minimumRequirement {
          __typename
          ... on DiscountMinimumSubtotal { greaterThanOrEqualToSubtotal { amount currencyCode } }
        }
      }
    }
  }
}
"""
DISCOUNT_EXISTS = """
query LoyaltyDiscountExists($id: ID!) { codeDiscountNode(id: $id) { id } }
"""
DEACTIVATE_DISCOUNT = """
mutation LoyaltyDiscountDeactivate($id: ID!) {
  discountCodeDeactivate(id: $id) {
    codeDiscountNode { id }
    userErrors { field code }
  }
}
"""


def discount_input(code, settings):
    value = ({"discountAmount": {"amount": settings["discount_value"], "appliesOnEachItem": False}}
             if settings["discount_type"] == "FIXED_AMOUNT" else
             {"percentage": float(Decimal(settings["discount_value"]))})
    # Only the dimensionless GraphQL Float is converted; monetary values stay decimal strings.
    result = {"code": code, "title": settings["title"], "startsAt": settings["starts_at"],
        "endsAt": settings["ends_at"], "usageLimit": 1, "appliesOncePerCustomer": True,
        "customerSelection": {"customers": {"add": [settings["customer_id"]]}},
        "customerGets": {"items": {"all": True}, "value": value,
                         "appliesOnOneTimePurchase": True, "appliesOnSubscription": False},
        "combinesWith": {"orderDiscounts": False, "productDiscounts": False, "shippingDiscounts": False}}
    if settings["min_order_amount"] is not None:
        result["minimumRequirement"] = {"subtotal": {"greaterThanOrEqualToSubtotal": settings["min_order_amount"]}}
    return result


def map_discount(node):
    if node is None:
        return None
    raw = node["codeDiscount"]
    if raw["__typename"] != "DiscountCodeBasic":
        return {"id": node["id"], "settings": None}
    gets, selection = raw["customerGets"], raw["customerSelection"]
    value, minimum = gets["value"], raw.get("minimumRequirement")
    if (selection["__typename"] != "DiscountCustomers" or len(selection["customers"]) != 1
            or gets["items"]["__typename"] != "AllDiscountItems"
            or (minimum and minimum["__typename"] != "DiscountMinimumSubtotal")
            or value["__typename"] not in ("DiscountAmount", "DiscountPercentage")):
        return {"id": node["id"], "settings": None}
    fixed = value["__typename"] == "DiscountAmount"
    settings = {"title": raw["title"], "starts_at": raw["startsAt"], "ends_at": raw["endsAt"],
        "usage_limit": raw["usageLimit"], "applies_once": raw["appliesOncePerCustomer"],
        "combines_with": raw["combinesWith"], "customer_id": selection["customers"][0]["id"],
        "all_items": gets["items"]["allItems"], "one_time": gets["appliesOnOneTimePurchase"],
        "subscription": gets["appliesOnSubscription"], "applies_on_each_item": value.get("appliesOnEachItem", False),
        "discount_type": "FIXED_AMOUNT" if fixed else "PERCENTAGE",
        "discount_value": str(value["amount"]["amount"] if fixed else value["percentage"]),
        "currency": value["amount"]["currencyCode"] if fixed else None,
        "min_order_amount": minimum["greaterThanOrEqualToSubtotal"]["amount"] if minimum else None,
        "minimum_currency": minimum["greaterThanOrEqualToSubtotal"]["currencyCode"] if minimum else None}
    return {"id": node["id"], "settings": settings}
