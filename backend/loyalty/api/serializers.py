from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from loyalty.models import AuditLog, CustomerProfile, PointsLot, PointsTransaction, ProgramConfig, Redemption, Reward
from loyalty.services.administration import CONFIG_FIELDS, REWARD_FIELDS


class StrictInteger(serializers.IntegerField):
    def to_internal_value(self, data):
        if isinstance(data, bool) or isinstance(data, float):
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictInputMixin:
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise serializers.ValidationError({"non_field_errors": ["Invalid fields"]})
        return super().to_internal_value(data)


class StrictInput(StrictInputMixin, serializers.Serializer):
    pass


class AdjustmentInput(StrictInput):
    delta = StrictInteger()
    reason_code = serializers.ChoiceField(choices=PointsTransaction.ReasonCode.values)
    note = serializers.CharField(min_length=5, max_length=500)
    client_key = serializers.CharField(max_length=193)


class BlockInput(StrictInput):
    reason = serializers.CharField(max_length=255)


class ProfileSerializer(serializers.ModelSerializer):
    tier = serializers.SerializerMethodField()

    def get_tier(self, obj):
        return {"id": obj.tier_id, "name": obj.tier.name, "rank": obj.tier.rank,
            "multiplier": str(obj.tier.multiplier)} if obj.tier_id else None
    class Meta:
        model = CustomerProfile
        fields = ("id", "shopify_customer_id", "email", "first_name", "last_name", "is_anonymized",
                  "is_blocked", "blocked_reason", "available_balance", "pending_balance", "lifetime_earned", "created_at", "tier", "tier_since")


class LotSerializer(serializers.ModelSerializer):
    class Meta:
        model = PointsLot
        fields = ("id", "source_type", "source_order_id", "points_total", "points_remaining", "status",
                  "available_at", "expires_at", "created_at")


class LedgerSerializer(serializers.ModelSerializer):
    class Meta:
        model = PointsTransaction
        fields = ("id", "type", "delta", "balance_after", "order_id", "reason_code", "note", "actor", "created_at")


class DetailedLedgerSerializer(LedgerSerializer):
    class Meta(LedgerSerializer.Meta):
        fields = (*LedgerSerializer.Meta.fields, "rule_snapshot")


class RewardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reward
        fields = ("id", *REWARD_FIELDS, "created_at", "updated_at")


class RewardInput(StrictInputMixin, serializers.ModelSerializer):
    points_cost = StrictInteger(min_value=1, max_value=2**31-1)
    code_valid_days = StrictInteger(min_value=1, max_value=3650, required=False)
    stock_limit = StrictInteger(min_value=0, max_value=2**31-1, required=False, allow_null=True)
    per_customer_limit = StrictInteger(min_value=0, max_value=2**31-1, required=False, allow_null=True)
    discount_value = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))
    min_order_amount = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0"), required=False, allow_null=True)
    max_discount_amount = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"), required=False, allow_null=True)
    description = serializers.CharField(max_length=5000, required=False, allow_blank=True)

    class Meta:
        model = Reward
        fields = REWARD_FIELDS


class ConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProgramConfig
        fields = ("id", "version", "effective_from", "created_by", *CONFIG_FIELDS, "created_at")


class ConfigHistorySerializer(ConfigSerializer):
    can_delete = serializers.SerializerMethodField()

    def get_can_delete(self, obj):
        return obj.effective_from > timezone.now()

    class Meta(ConfigSerializer.Meta):
        fields = (*ConfigSerializer.Meta.fields, "can_delete")


class ConfigInput(StrictInputMixin, serializers.ModelSerializer):
    effective_from = serializers.DateTimeField(required=False)
    spend_per_point = serializers.DecimalField(max_digits=18, decimal_places=2,
                                               min_value=Decimal("0.01"), required=False)
    pending_days = StrictInteger(min_value=0, max_value=365, required=False)
    expiry_days = StrictInteger(min_value=0, max_value=3650, required=False)
    expiry_reminder_days = StrictInteger(min_value=0, max_value=3650, required=False)
    max_redemptions_per_day = StrictInteger(min_value=0, max_value=1000000, required=False)
    max_unused_codes_per_reward = StrictInteger(min_value=0, max_value=1000000, required=False)
    welcome_points = StrictInteger(min_value=0, max_value=1000000, required=False)
    birthday_points = StrictInteger(min_value=0, max_value=1000000, required=False)
    referral_referrer_points = StrictInteger(min_value=0, max_value=1000000, required=False)
    referral_referee_points = StrictInteger(min_value=0, max_value=1000000, required=False)
    referral_max_per_referrer_per_month = StrictInteger(min_value=0, max_value=1000000, required=False)
    referral_min_order_amount = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0"), required=False, allow_null=True)
    negative_balance_alert_threshold = StrictInteger(min_value=-(10**12), max_value=0, required=False)
    excluded_product_ids = serializers.ListField(child=StrictInteger(min_value=1, max_value=2**63-1), max_length=1000, required=False)
    excluded_collection_ids = serializers.ListField(child=StrictInteger(min_value=1, max_value=2**63-1), max_length=1000, required=False)
    excluded_channels = serializers.ListField(child=serializers.CharField(max_length=100), max_length=100, required=False)
    tier_window_months = StrictInteger(min_value=0, max_value=120, required=False)
    tier_downgrade_grace_days = StrictInteger(min_value=0, max_value=3650, required=False)

    class Meta:
        model = ProgramConfig
        fields = ("effective_from", *CONFIG_FIELDS)


class RedemptionSerializer(serializers.ModelSerializer):
    code_last4 = serializers.SerializerMethodField()

    def get_code_last4(self, obj):
        return obj.discount_code[-4:] if obj.discount_code else None

    class Meta:
        model = Redemption
        fields = ("id", "profile_id", "reward_id", "points_spent", "status", "code_last4", "code_expires_at", "created_at")


class RedemptionDetailSerializer(RedemptionSerializer):
    last_error = serializers.SerializerMethodField()

    def get_last_error(self, obj):
        from .diagnostics import redemption_error
        return redemption_error(obj.last_error)

    class Meta(RedemptionSerializer.Meta):
        fields = (*RedemptionSerializer.Meta.fields, "discount_code", "used_order_id", "attempts", "updated_at", "last_error")


class AuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = ("id", "actor", "action", "target_type", "target_id", "before", "after", "created_at")


class RedeemInput(serializers.Serializer):
    # Body identity fields are deliberately ignored; only the signed query identifies a customer.
    reward_id = StrictInteger(min_value=1, max_value=2**63-1)
    client_key = serializers.CharField(max_length=170)
