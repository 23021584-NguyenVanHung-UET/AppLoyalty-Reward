"""Pure earning rules; no Django, database or network imports."""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, localcontext
from datetime import datetime

ZERO = Decimal("0")


@dataclass
class OrderLine:
    id: int
    product_id: int | None
    collection_ids: list[int]
    is_gift_card: bool
    quantity: int
    total_after_discounts: Decimal
    tax_amount: Decimal = ZERO


@dataclass
class RefundLine:
    line_id: int
    quantity: int
    subtotal: Decimal


@dataclass
class RefundSummary:
    refund_id: int
    has_line_items: bool
    has_shipping: bool


@dataclass
class OrderSnapshot:
    order_id: int
    is_test: bool
    cancelled: bool
    financial_status: str
    taxes_included: bool
    customer_id: int | None
    channel: str
    lines: list[OrderLine]
    shipping_after_discounts: Decimal = ZERO
    gift_card_paid: Decimal = ZERO
    refund_lines: list[RefundLine] = field(default_factory=list)
    currency: str = "VND"
    refund_summaries: list[RefundSummary] = field(default_factory=list)
    created_at: datetime | None = None
    custom_attributes: dict = field(default_factory=dict)
    customer_email: str | None = None
    customer_created_at: datetime | None = None


@dataclass
class EarnRules:
    spend_per_point: Decimal
    include_tax: bool = False
    include_shipping: bool = False
    excluded_product_ids: list = field(default_factory=list)
    excluded_collection_ids: list = field(default_factory=list)
    excluded_channels: list = field(default_factory=list)
    tier_multiplier: Decimal = Decimal("1")
    allow_test_orders: bool = False
    tier_id: int | None = None
    campaign_id: int | None = None
    campaign_multiplier: Decimal = Decimal("1")
    line_multipliers: dict = field(default_factory=dict)
    candidate_rules: list = field(default_factory=list)
    candidate_campaigns: list = field(default_factory=list)
    frozen: bool = False


def multiplier(value, minimum=ZERO):
    if isinstance(value, (float, bool)):
        raise ValueError("Multipliers must use Decimal, never float.")
    result = Decimal(value)
    if not result.is_finite() or result < minimum or result <= 0 or result > 100 or result.as_tuple().exponent < -2:
        raise ValueError("Invalid multiplier (maximum 100, two decimals).")
    return result


def active_at(row, at):
    if at is None or at.tzinfo is None:
        raise ValueError("Order created_at must have a timezone for earning rules.")
    def timestamp(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    start, end = timestamp(row["starts_at"]), timestamp(row.get("ends_at"))
    return row.get("is_active", True) and start <= at and (end is None or at < end)


@dataclass
class EarnResult:
    points: int
    eligible_spend: Decimal
    skipped_reason: str | None
    breakdown: dict


SKIP_REASONS = {
    "test_order": "Đơn hàng thử nghiệm",
    "cancelled_order": "Đơn hàng đã hủy",
    "financial_status_ineligible": "Trạng thái thanh toán không đủ điều kiện",
    "guest": "Đơn hàng không có khách hàng",
    "channel_excluded": "Kênh bán bị loại trừ",
    "zero_points": "Số điểm tính được bằng 0",
}


def nonnegative_amount(value, notes, field_name):
    try:
        # Floats must never participate in monetary arithmetic.
        if value is None or isinstance(value, (float, bool)):
            raise ValueError
        amount = Decimal(value)
        if not amount.is_finite() or amount < ZERO:
            raise ValueError
        return amount
    except (ValueError, TypeError, InvalidOperation):
        notes[field_name] = notes.get(field_name, 0) + 1
        return ZERO


def normalized_ids(values):
    return {str(value).rsplit("/", 1)[-1] for value in values}


def compute_earn(order: OrderSnapshot, rules: EarnRules) -> EarnResult:
    if isinstance(rules.spend_per_point, float):
        raise ValueError("Số tiền cho một điểm phải dùng Decimal.")
    rate = Decimal(rules.spend_per_point)
    if not rate.is_finite() or rate <= ZERO:
        raise ValueError("Số tiền cho một điểm phải lớn hơn 0.")
    tier_multiplier = multiplier(rules.tier_multiplier, Decimal("1"))
    breakdown = {"code": "earn", "lines_considered": 0, "gift_card_products_excluded": 0,
                 "products_excluded": 0, "collections_excluded": 0, "tax_adjustment": "0",
                 "shipping_included": rules.include_shipping, "shipping_amount": "0",
                 "gift_card_deduction": "0", "refund_deduction": "0",
                 "eligible_spend": "0", "spend_per_point": str(rate), "invalid_amounts": {}}

    def skipped(code):
        breakdown["code"] = code
        return EarnResult(0, ZERO, SKIP_REASONS[code], breakdown)

    if order.is_test and not rules.allow_test_orders:
        return skipped("test_order")
    if order.cancelled:
        return skipped("cancelled_order")
    if order.financial_status not in ("PAID", "PARTIALLY_REFUNDED"):
        return skipped("financial_status_ineligible")
    if order.customer_id is None:
        return skipped("guest")
    if order.channel in rules.excluded_channels:
        return skipped("channel_excluded")
    notes = breakdown["invalid_amounts"]
    products, collections = normalized_ids(rules.excluded_product_ids), normalized_ids(rules.excluded_collection_ids)
    # A wide Decimal context preserves cents on large Shopify amounts without floats.
    with localcontext() as context:
        context.prec = 100
        refunds = {}
        for refund in order.refund_lines:
            refunds[refund.line_id] = refunds.get(refund.line_id, ZERO) + nonnegative_amount(
                refund.subtotal, notes, "refund_subtotal")
        eligible, weighted, tax_adjustment, refund_deduction = ZERO, ZERO, ZERO, ZERO
        applied_lines = {}
        for line in order.lines:
            breakdown["lines_considered"] += 1
            if line.is_gift_card:
                breakdown["gift_card_products_excluded"] += 1
                continue
            if str(line.product_id) in products:
                breakdown["products_excluded"] += 1
                continue
            if normalized_ids(line.collection_ids) & collections:
                breakdown["collections_excluded"] += 1
                continue
            amount = nonnegative_amount(line.total_after_discounts, notes, "line_total")
            tax = nonnegative_amount(line.tax_amount, notes, "line_tax")
            if order.taxes_included and not rules.include_tax:
                adjustment = -min(amount, tax)
            elif not order.taxes_included and rules.include_tax:
                adjustment = tax
            else:
                adjustment = ZERO
            amount += adjustment
            tax_adjustment += adjustment
            deduction = min(amount, refunds.get(line.id, ZERO))
            refund_deduction += deduction
            net = amount - deduction
            selected = rules.line_multipliers.get(str(line.id), {"rule_id": None, "multiplier": "1"})
            if not rules.frozen:
                matches = [row for row in rules.candidate_rules if active_at(row, order.created_at) and (
                    (row["rule_type"] == "PRODUCT" and str(line.product_id) in normalized_ids(row["target_ids"])) or
                    (row["rule_type"] == "COLLECTION" and normalized_ids(line.collection_ids) & normalized_ids(row["target_ids"])))]
                if matches:
                    best = max(matches, key=lambda row: (multiplier(row["multiplier"]), row.get("priority", 0), -row["id"]))
                    selected = {"rule_id": best["id"], "multiplier": str(best["multiplier"])}
            factor = multiplier(selected["multiplier"])
            applied_lines[str(line.id)] = {"rule_id": selected["rule_id"], "multiplier": str(factor)}
            eligible += net
            weighted += net * factor
        shipping = nonnegative_amount(order.shipping_after_discounts, notes, "shipping") if rules.include_shipping else ZERO
        eligible += shipping
        weighted += shipping
        gross_eligible, gross_weighted = eligible, weighted
        gift_card = min(eligible, nonnegative_amount(order.gift_card_paid, notes, "gift_card_paid"))
        if eligible:
            weighted *= (eligible - gift_card) / eligible
        eligible = max(ZERO, eligible - gift_card)
        campaign_id, campaign_multiplier = rules.campaign_id, multiplier(rules.campaign_multiplier)
        if not rules.frozen:
            campaigns = [row for row in rules.candidate_campaigns if active_at(row, order.created_at)
                and (not row.get("audience_tier_ids") or rules.tier_id in row["audience_tier_ids"])
                and (row.get("min_eligible_spend") is None or eligible >= Decimal(row["min_eligible_spend"]))]
            if campaigns:
                best = max(campaigns, key=lambda row: (multiplier(row["multiplier"]), -row["id"]))
                campaign_id, campaign_multiplier = best["id"], multiplier(best["multiplier"])
        # Multiply finite Decimals before the single division, so proportional
        # gift-card fractions such as 1/3 cannot cause an artificial point loss.
        points = int((gross_weighted * eligible * campaign_multiplier * tier_multiplier
            / (gross_eligible * rate)).to_integral_value(rounding=ROUND_FLOOR)) if gross_eligible else 0
    breakdown.update(tax_adjustment=str(tax_adjustment), refund_deduction=str(refund_deduction),
                     shipping_amount=str(shipping), gift_card_deduction=str(gift_card), eligible_spend=str(eligible),
                     effective_spend=str(weighted), line_multipliers=applied_lines,
                     campaign_id=campaign_id, campaign_multiplier=str(campaign_multiplier),
                     tier_id=rules.tier_id, tier_multiplier=str(tier_multiplier))
    if points == 0:
        breakdown["code"] = "zero_points"
        return EarnResult(0, eligible, SKIP_REASONS["zero_points"], breakdown)
    return EarnResult(points, eligible, None, breakdown)
