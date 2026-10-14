from django.db import transaction
from loyalty.models import Campaign, EarnRule, Tier
from shops.models import Shop
from .administration import audit, snapshot
from .errors import LoyaltyError

FIELDS = {
    EarnRule: ("name", "rule_type", "target_ids", "multiplier", "starts_at", "ends_at", "is_active", "priority"),
    Campaign: ("name", "multiplier", "min_eligible_spend", "audience_tier_ids", "starts_at", "ends_at", "is_active"),
    Tier: ("name", "rank", "min_qualifying_points", "multiplier", "perks_text", "is_active"),
}


def save_definition(model, shop, data, actor, instance=None):
    with transaction.atomic():
        Shop.objects.select_for_update().get(pk=shop.pk)
        row = model.objects.select_for_update().get(pk=instance.pk, shop=shop) if instance else model(shop=shop)
        before = snapshot(row, FIELDS[model]) if instance else None
        for key, value in data.items():
            setattr(row, key, value)
        if model in (EarnRule, Campaign):
            if row.ends_at is not None and row.ends_at <= row.starts_at:
                raise LoyaltyError("INVALID_WINDOW", "Ngày kết thúc phải sau ngày bắt đầu.", 422)
        if model is Campaign:
            audience = row.audience_tier_ids
            if len(set(audience)) != len(audience) or Tier.objects.filter(shop=shop, pk__in=audience).count() != len(audience):
                raise LoyaltyError("INVALID_AUDIENCE", "Hạng khách hàng phải thuộc cửa hàng này và không trùng nhau.", 422)
        if model is EarnRule and len(set(row.target_ids)) != len(row.target_ids):
            raise LoyaltyError("INVALID_TARGETS", "Mã sản phẩm hoặc bộ sưu tập không được trùng nhau.", 422)
        if model is Tier and row.is_active:
            others = list(Tier.objects.filter(shop=shop, is_active=True).exclude(pk=row.pk))
            definitions = sorted([*others, row], key=lambda tier: tier.rank)
            if any(a.rank == b.rank or a.min_qualifying_points >= b.min_qualifying_points for a,b in zip(definitions, definitions[1:])):
                raise LoyaltyError("INVALID_THRESHOLDS", "Điểm xét hạng phải tăng nghiêm ngặt theo rank; không trùng ngưỡng.", 422)
        row.full_clean()
        row.save()
        audit(shop.pk, actor, f"{model.__name__.lower()}.{'update' if before else 'create'}", row,
              before, snapshot(row, FIELDS[model]))
        return row
