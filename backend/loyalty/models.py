from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .base import ImmutableModel, NoDeleteQuerySet, TimeStampedModel


class ProgramConfig(ImmutableModel, TimeStampedModel):
    """Versioned settings; insert a new version instead of updating a row."""

    shop = models.ForeignKey("shops.Shop", verbose_name="Cửa hàng", on_delete=models.CASCADE,
                             related_name="program_configs")
    version = models.PositiveIntegerField("Phiên bản")
    effective_from = models.DateTimeField("Có hiệu lực từ")
    is_enabled = models.BooleanField("Bật chương trình", default=True)
    spend_per_point = models.DecimalField("Số tiền cho một điểm", max_digits=18,
                                          decimal_places=2, default=Decimal("10000"))
    include_tax = models.BooleanField("Tính cả thuế", default=False)
    include_shipping = models.BooleanField("Tính cả phí vận chuyển", default=False)
    excluded_product_ids = models.JSONField("Sản phẩm loại trừ", default=list, blank=True)
    excluded_collection_ids = models.JSONField("Bộ sưu tập loại trừ", default=list, blank=True)
    excluded_channels = models.JSONField("Kênh bán loại trừ", default=list, blank=True)
    pending_days = models.PositiveIntegerField("Số ngày chờ", default=7,
                                               help_text="0 là khả dụng ngay.")
    expiry_days = models.PositiveIntegerField("Số ngày đến khi hết hạn", default=30,
                                              help_text="Tính từ ngày cấp điểm; 0 là không hết hạn.")
    expiry_reminder_days = models.PositiveIntegerField("Nhắc trước hạn bao nhiêu ngày", default=5)
    max_redemptions_per_day = models.PositiveIntegerField("Số lượt đổi tối đa mỗi ngày", default=3)
    max_unused_codes_per_reward = models.PositiveIntegerField("Số mã chưa dùng tối đa mỗi phần thưởng", default=1)
    refund_points_on_expired_code = models.BooleanField("Hoàn điểm khi mã hết hạn", default=True)
    refund_points_on_cancelled_order = models.BooleanField("Hoàn điểm khi đơn bị hủy", default=True)
    allow_negative_balance = models.BooleanField("Cho phép số dư âm", default=True)
    negative_balance_alert_threshold = models.BigIntegerField("Ngưỡng cảnh báo số dư âm", default=-500)
    welcome_points = models.PositiveIntegerField("Điểm chào mừng", default=0)
    welcome_trigger = models.CharField(max_length=20, choices=[("SIGNUP", "Signup"), ("FIRST_ORDER", "First earned order")], default="FIRST_ORDER")
    birthday_enabled = models.BooleanField(default=False)
    birthday_points = models.PositiveIntegerField(default=0)
    referral_enabled = models.BooleanField(default=False)
    referral_referrer_points = models.PositiveIntegerField(default=0)
    referral_referee_points = models.PositiveIntegerField(default=0)
    referral_max_per_referrer_per_month = models.PositiveIntegerField(default=10)
    referral_min_order_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    tiers_enabled = models.BooleanField(default=False)
    tier_window_months = models.PositiveSmallIntegerField(default=0)
    tier_downgrade_grace_days = models.PositiveSmallIntegerField(default=30)
    created_by = models.CharField("Người tạo", max_length=150, blank=True,
                                  help_text="Tên người dùng hoặc system.")

    class Meta:
        verbose_name = "Cấu hình chương trình"
        verbose_name_plural = "Cấu hình chương trình"
        constraints = [
            models.UniqueConstraint(fields=["shop", "version"], name="loyalty_config_shop_version_uniq"),
            models.CheckConstraint(condition=models.Q(spend_per_point__gt=0),
                                   name="loyalty_config_spend_positive"),
        ]
        indexes = [models.Index(fields=["shop", "-effective_from"], name="loyalty_config_effective_idx")]

    @classmethod
    def get_effective(cls, shop, at=None):
        timestamp = timezone.now() if at is None else at
        return (
            cls.objects.filter(shop=shop, effective_from__lte=timestamp, deletion__isnull=True)
            .order_by("-effective_from", "-version")
            .first()
        )

    def __str__(self):
        return f"{self.shop_id}:v{self.version}"


class ProgramConfigDeletion(ImmutableModel):
    """Append-only tombstone preserving the original version and its number."""

    config = models.OneToOneField(ProgramConfig, on_delete=models.PROTECT, related_name="deletion")
    actor = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Phiên bản cấu hình đã xóa"
        verbose_name_plural = "Phiên bản cấu hình đã xóa"


BALANCE_HELP = "Số dư chỉ là bộ nhớ đệm; sổ cái PointsTransaction là nguồn dữ liệu chuẩn."


class CustomerProfile(TimeStampedModel):
    shop = models.ForeignKey("shops.Shop", verbose_name="Cửa hàng", on_delete=models.PROTECT,
                             related_name="customer_profiles")
    shopify_customer_id = models.BigIntegerField("Mã khách hàng Shopify")
    email = models.EmailField("Email", blank=True)
    email_marketing_opt_in = models.BooleanField(default=False)
    email_opt_out = models.BooleanField(default=False)
    locale = models.CharField(max_length=20, blank=True)
    shopify_created_at = models.DateTimeField(null=True, blank=True)
    birthday_month = models.PositiveSmallIntegerField(null=True, blank=True)
    birthday_day = models.PositiveSmallIntegerField(null=True, blank=True)
    birthday_set_at = models.DateTimeField(null=True, blank=True)
    first_name = models.CharField("Tên", max_length=100, blank=True)
    last_name = models.CharField("Họ", max_length=100, blank=True)
    is_anonymized = models.BooleanField("Đã ẩn danh", default=False)
    is_blocked = models.BooleanField("Đã chặn", default=False)
    blocked_reason = models.CharField("Lý do chặn", max_length=255, blank=True)
    available_balance = models.BigIntegerField("Số dư khả dụng", default=0, help_text=BALANCE_HELP)
    pending_balance = models.BigIntegerField("Số dư chờ", default=0, help_text=BALANCE_HELP)
    lifetime_earned = models.BigIntegerField("Tổng điểm từng được cấp", default=0)
    tier = models.ForeignKey("Tier", null=True, blank=True, on_delete=models.PROTECT, related_name="members")
    tier_since = models.DateTimeField(null=True, blank=True)
    tier_drop_since = models.DateTimeField(null=True, blank=True)
    tier_last_qualifying_points = models.PositiveBigIntegerField(default=0)
    tier_evaluated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Hồ sơ khách hàng"
        verbose_name_plural = "Hồ sơ khách hàng"
        constraints = [models.UniqueConstraint(fields=["shop", "shopify_customer_id"],
                                               name="loyalty_profile_shop_cust_uniq"),
            models.CheckConstraint(condition=(models.Q(birthday_month__isnull=True, birthday_day__isnull=True, birthday_set_at__isnull=True)
                | models.Q(birthday_month__isnull=False, birthday_day__isnull=False, birthday_set_at__isnull=False,
                    birthday_month__gte=1, birthday_month__lte=12, birthday_day__gte=1, birthday_day__lte=31)),
                name="profile_birthday_consistent")]
        indexes = [models.Index(fields=["shop", "email"], name="loyalty_profile_email_idx")]

    def __str__(self):
        return f"{self.shop_id}:{self.shopify_customer_id}"


class PointsLot(TimeStampedModel):
    class SourceType(models.TextChoices):
        REFERRAL = "REFERRAL", "Giới thiệu"
        BIRTHDAY = "BIRTHDAY", "Sinh nhật"
        ORDER = "ORDER", "Đơn hàng"
        ADJUSTMENT = "ADJUSTMENT", "Điều chỉnh"
        WELCOME = "WELCOME", "Chào mừng"
        REFUND_BACK = "REFUND_BACK", "Hoàn điểm"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Chờ"
        AVAILABLE = "AVAILABLE", "Khả dụng"
        EXPIRED = "EXPIRED", "Hết hạn"
        CONSUMED = "CONSUMED", "Đã dùng hết"
        VOIDED = "VOIDED", "Đã hủy"

    profile = models.ForeignKey(CustomerProfile, verbose_name="Hồ sơ khách hàng",
                                on_delete=models.PROTECT, related_name="points_lots")
    source_type = models.CharField("Nguồn điểm", max_length=20, choices=SourceType.choices)
    source_order_id = models.BigIntegerField("Mã đơn nguồn", null=True, blank=True)
    points_total = models.PositiveBigIntegerField("Tổng điểm của lô")
    points_remaining = models.PositiveBigIntegerField("Điểm còn lại")
    status = models.CharField("Trạng thái", max_length=20, choices=Status.choices)
    available_at = models.DateTimeField("Ngày khả dụng", null=True, blank=True)
    expires_at = models.DateTimeField("Ngày hết hạn", null=True, blank=True,
                                      help_text="Để trống nếu không hết hạn; cố định khi tạo lô.")

    class Meta:
        verbose_name = "Lô điểm"
        verbose_name_plural = "Lô điểm"
        constraints = [models.CheckConstraint(condition=models.Q(points_remaining__lte=models.F("points_total")),
                                              name="loyalty_lot_remaining_lte_total")]
        indexes = [
            models.Index(fields=["status", "available_at"], name="loyalty_lot_available_idx"),
            models.Index(fields=["status", "expires_at"], name="loyalty_lot_expiry_idx"),
            models.Index(fields=["profile", "status", "expires_at"], name="loyalty_lot_profile_exp_idx"),
            models.Index(fields=["source_order_id"], name="loyalty_lot_order_idx"),
        ]

    def __str__(self):
        return f"{self.pk}: {self.points_remaining}/{self.points_total} ({self.status})"


class PointsTransaction(ImmutableModel):
    """Append-only ledger with globally unique, store-namespaced event keys.

    Event suffix conventions: earn:{order_id}, revoke:order:{order_id},
    revoke:refund:{refund_id}, expire:lot:{lot_id}, redeem:{client_key},
    redeem_refund:{redemption_id}, adjust:{client_key}.
    Prefix each suffix with shop:{shop_id}: to prevent cross-store collisions.
    """

    class Type(models.TextChoices):
        EARN = "EARN", "Tích điểm"
        REVOKE = "REVOKE", "Thu hồi"
        REDEEM = "REDEEM", "Đổi thưởng"
        REDEEM_REFUND = "REDEEM_REFUND", "Hoàn điểm đổi thưởng"
        EXPIRE = "EXPIRE", "Hết hạn"
        ADJUST = "ADJUST", "Điều chỉnh"

    class ReasonCode(models.TextChoices):
        INCIDENT_COMPENSATION = "INCIDENT_COMPENSATION", "Bù đắp sự cố"
        GIFT = "GIFT", "Quà tặng"
        CORRECTION = "CORRECTION", "Sửa sai"
        COMPLAINT = "COMPLAINT", "Khiếu nại"
        OTHER = "OTHER", "Khác"

    profile = models.ForeignKey(CustomerProfile, verbose_name="Hồ sơ khách hàng",
                                on_delete=models.PROTECT, related_name="points_transactions")
    type = models.CharField("Loại giao dịch", max_length=20, choices=Type.choices)
    delta = models.BigIntegerField("Điểm thay đổi", help_text="Số có dấu, khác 0.")
    balance_after = models.BigIntegerField("Số dư khả dụng sau giao dịch")
    lot = models.ForeignKey(PointsLot, verbose_name="Lô điểm", on_delete=models.PROTECT,
                            null=True, blank=True, related_name="transactions")
    order_id = models.BigIntegerField("Mã đơn hàng", null=True, blank=True)
    refund_id = models.BigIntegerField("Mã hoàn tiền", null=True, blank=True)
    redemption = models.ForeignKey("loyalty.Redemption", verbose_name="Lượt đổi thưởng",
                                   on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="points_transactions")
    idempotency_key = models.CharField("Khóa chống trùng", max_length=200)
    reason_code = models.CharField("Mã lý do", max_length=30, choices=ReasonCode.choices, blank=True)
    note = models.TextField("Ghi chú", blank=True)
    actor = models.CharField("Người thực hiện", max_length=150, default="system")
    rule_snapshot = models.JSONField("Bản chụp quy tắc", default=dict, blank=True)
    created_at = models.DateTimeField("Ngày tạo", auto_now_add=True)

    class Meta:
        verbose_name = "Giao dịch điểm"
        verbose_name_plural = "Sổ cái điểm"
        constraints = [
            models.CheckConstraint(condition=~models.Q(delta=0), name="loyalty_tx_delta_nonzero"),
            models.UniqueConstraint(fields=["idempotency_key"], name="loyalty_tx_idem_key_uniq"),
        ]
        indexes = [
            models.Index(fields=["created_at"], name="loyalty_tx_created_at_idx"),
            models.Index(fields=["profile", "-created_at"], name="loyalty_tx_profile_time_idx"),
            models.Index(fields=["order_id"], name="loyalty_tx_order_idx"),
            models.Index(fields=["type", "created_at"], name="loyalty_tx_type_time_idx"),
        ]

    def __str__(self):
        return f"{self.type} {self.delta:+d} ({self.profile_id})"


class Reward(TimeStampedModel):
    class DiscountType(models.TextChoices):
        FIXED_AMOUNT = "FIXED_AMOUNT", "Số tiền cố định"
        PERCENTAGE = "PERCENTAGE", "Phần trăm"

    objects = NoDeleteQuerySet.as_manager()

    shop = models.ForeignKey("shops.Shop", verbose_name="Cửa hàng", on_delete=models.CASCADE,
                             related_name="rewards")
    name = models.CharField("Tên phần thưởng", max_length=150)
    description = models.TextField("Mô tả", blank=True)
    points_cost = models.PositiveIntegerField("Số điểm cần đổi")
    discount_type = models.CharField("Loại giảm giá", max_length=20, choices=DiscountType.choices)
    discount_value = models.DecimalField("Giá trị giảm", max_digits=18, decimal_places=2)
    max_discount_amount = models.DecimalField("Trần giảm giá", max_digits=18, decimal_places=2,
                                              null=True, blank=True)
    min_order_amount = models.DecimalField("Giá trị đơn tối thiểu", max_digits=18, decimal_places=2,
                                           null=True, blank=True)
    code_valid_days = models.PositiveIntegerField("Số ngày hiệu lực mã", default=30)
    stock_limit = models.PositiveIntegerField("Số lượng tối đa", null=True, blank=True)
    per_customer_limit = models.PositiveIntegerField("Giới hạn mỗi khách", null=True, blank=True)
    is_active = models.BooleanField("Đang hoạt động", default=True)
    active_from = models.DateTimeField("Bắt đầu hiệu lực", null=True, blank=True)
    active_to = models.DateTimeField("Kết thúc hiệu lực", null=True, blank=True)
    sort_order = models.IntegerField("Thứ tự hiển thị", default=0)

    class Meta:
        verbose_name = "Phần thưởng"
        verbose_name_plural = "Phần thưởng"
        constraints = [
            models.CheckConstraint(condition=models.Q(points_cost__gt=0), name="loyalty_reward_cost_positive"),
            models.CheckConstraint(condition=models.Q(discount_value__gt=0), name="loyalty_reward_value_positive"),
            models.CheckConstraint(condition=~models.Q(discount_type="PERCENTAGE") | models.Q(discount_value__lte=100),
                                   name="loyalty_reward_pct_lte_100"),
        ]

    def delete(self, **kwargs):
        raise ValidationError("Hãy vô hiệu hóa phần thưởng thay vì xóa.")

    def __str__(self):
        return self.name


class Redemption(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING_CODE = "PENDING_CODE", "Chờ tạo mã"
        ISSUED = "ISSUED", "Đã phát hành"
        USED = "USED", "Đã sử dụng"
        EXPIRED = "EXPIRED", "Hết hạn"
        CANCELLED = "CANCELLED", "Đã hủy"
        FAILED = "FAILED", "Thất bại"

    profile = models.ForeignKey(CustomerProfile, verbose_name="Hồ sơ khách hàng",
                                on_delete=models.PROTECT, related_name="redemptions")
    reward = models.ForeignKey(Reward, verbose_name="Phần thưởng", on_delete=models.PROTECT,
                               related_name="redemptions")
    points_spent = models.PositiveIntegerField("Điểm đã đổi", help_text="Giá tại lúc xác nhận.")
    status = models.CharField("Trạng thái", max_length=20, choices=Status.choices,
                              default=Status.PENDING_CODE)
    idempotency_key = models.CharField("Khóa chống trùng", max_length=200)
    discount_code = models.CharField("Mã giảm giá", max_length=64, null=True, blank=True)
    shopify_discount_id = models.CharField("Mã giảm giá Shopify", max_length=100, blank=True)
    code_expires_at = models.DateTimeField("Hạn mã giảm giá", null=True, blank=True)
    used_order_id = models.BigIntegerField("Mã đơn đã sử dụng", null=True, blank=True)
    attempts = models.PositiveSmallIntegerField("Số lần thử", default=0)
    last_error = models.TextField("Lỗi gần nhất", blank=True)
    issue_claim = models.CharField(max_length=32, blank=True, default="")
    issue_claim_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Lượt đổi thưởng"
        verbose_name_plural = "Lượt đổi thưởng"
        constraints = [models.UniqueConstraint(fields=["idempotency_key"], name="loyalty_redeem_idem_key_uniq"),
                       models.UniqueConstraint(fields=["discount_code"],
                                               condition=models.Q(discount_code__isnull=False),
                                               name="loyalty_redemption_code_uniq")]
        indexes = [
            models.Index(fields=["status", "created_at"], name="loyalty_redeem_status_time_idx"),
            models.Index(fields=["profile", "status"], name="loyalty_redeem_profile_idx"),
        ]

    def __str__(self):
        return f"{self.pk}: {self.reward_id} ({self.status})"


class AuditLog(ImmutableModel):
    shop = models.ForeignKey("shops.Shop", verbose_name="Cửa hàng", on_delete=models.PROTECT,
                             related_name="audit_logs")
    actor = models.CharField("Người thực hiện", max_length=150)
    action = models.CharField("Hành động", max_length=100)
    target_type = models.CharField("Loại đối tượng", max_length=100)
    target_id = models.CharField("Mã đối tượng", max_length=100)
    before = models.JSONField("Dữ liệu trước", null=True, blank=True)
    after = models.JSONField("Dữ liệu sau", null=True, blank=True)
    created_at = models.DateTimeField("Ngày tạo", auto_now_add=True)

    class Meta:
        verbose_name = "Nhật ký quản trị"
        verbose_name_plural = "Nhật ký quản trị"
        indexes = [
            models.Index(fields=["shop", "-created_at"], name="loyalty_audit_shop_time_idx"),
            models.Index(fields=["target_type", "target_id"], name="loyalty_audit_target_idx"),
        ]

    def __str__(self):
        return f"{self.actor}: {self.action} ({self.target_type}:{self.target_id})"


class EarningQuerySet(NoDeleteQuerySet):
    def update(self, **kwargs):
        raise ValidationError("Use save() to preserve earning rule revisions.")

    def bulk_update(self, *args, **kwargs):
        raise ValidationError("Use save() to preserve earning rule revisions.")

    def bulk_create(self, *args, **kwargs):
        raise ValidationError("Use save() to preserve earning rule revisions.")


class VersionedEarningModel(TimeStampedModel):
    objects = EarningQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, **kwargs):
        from django.db import transaction
        from django.core.serializers.json import DjangoJSONEncoder
        import json
        with transaction.atomic():
            if self.pk:
                type(self).objects.select_for_update().get(pk=self.pk)
            super().save(**kwargs)
            self.refresh_from_db()
            values = {field.name: getattr(self, field.name) for field in self._meta.fields
                      if field.name not in ("shop", "created_at", "updated_at")}
            EarningRevision.objects.create(**{"rule" if isinstance(self, EarnRule) else "campaign": self},
                effective_at=timezone.now(), values=json.loads(json.dumps(values, cls=DjangoJSONEncoder)))

    def delete(self, **kwargs):
        raise ValidationError("Deactivate earning rules and campaigns instead of deleting.")


class EarnRule(VersionedEarningModel):
    shop = models.ForeignKey("shops.Shop", on_delete=models.PROTECT, related_name="earn_rules")
    name = models.CharField(max_length=150)
    rule_type = models.CharField(max_length=20, choices=[("PRODUCT", "Product"), ("COLLECTION", "Collection")])
    target_ids = models.JSONField(default=list)
    multiplier = models.DecimalField(max_digits=5, decimal_places=2)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=0)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(multiplier__gt=0, multiplier__lte=100), name="loyalty_earn_multiplier_range"),
            models.CheckConstraint(condition=models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=models.F("starts_at")), name="loyalty_earn_window")]


class Campaign(VersionedEarningModel):
    shop = models.ForeignKey("shops.Shop", on_delete=models.PROTECT, related_name="campaigns")
    name = models.CharField(max_length=150)
    multiplier = models.DecimalField(max_digits=5, decimal_places=2)
    min_eligible_spend = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    audience_tier_ids = models.JSONField(default=list, blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(multiplier__gt=0, multiplier__lte=100), name="loyalty_campaign_multiplier_range"),
            models.CheckConstraint(condition=models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=models.F("starts_at")), name="loyalty_campaign_window"),
            models.CheckConstraint(condition=models.Q(min_eligible_spend__isnull=True) | models.Q(min_eligible_spend__gte=0), name="loyalty_campaign_minimum")]


class EarningRevision(ImmutableModel):
    rule = models.ForeignKey(EarnRule, null=True, blank=True, on_delete=models.PROTECT, related_name="revisions")
    campaign = models.ForeignKey(Campaign, null=True, blank=True, on_delete=models.PROTECT, related_name="revisions")
    effective_at = models.DateTimeField(db_index=True)
    values = models.JSONField()

    class Meta:
        constraints = [models.CheckConstraint(condition=(models.Q(rule__isnull=False, campaign__isnull=True)
            | models.Q(rule__isnull=True, campaign__isnull=False)), name="loyalty_revision_one_owner")]


class Tier(TimeStampedModel):
    objects = NoDeleteQuerySet.as_manager()
    shop = models.ForeignKey("shops.Shop", on_delete=models.PROTECT, related_name="tiers")
    name = models.CharField(max_length=150)
    rank = models.PositiveIntegerField()
    min_qualifying_points = models.PositiveBigIntegerField(default=0)
    multiplier = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("1.00"))
    perks_text = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["shop", "rank"], name="loyalty_tier_shop_rank"),
            models.CheckConstraint(condition=models.Q(multiplier__gte=1, multiplier__lte=100), name="loyalty_tier_multiplier_range")]

    def delete(self, **kwargs):
        raise ValidationError("Deactivate tiers instead of deleting.")

    def __str__(self):
        return self.name


class TierHistory(ImmutableModel):
    profile = models.ForeignKey(CustomerProfile, on_delete=models.PROTECT, related_name="tier_history")
    from_tier = models.ForeignKey(Tier, on_delete=models.PROTECT, null=True, blank=True, related_name="departures")
    to_tier = models.ForeignKey(Tier, on_delete=models.PROTECT, null=True, blank=True, related_name="arrivals")
    reason = models.CharField(max_length=100)
    qualifying_points = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

from .notification_models import NotificationSettings, NotificationLog, NotificationExpiryLot
from .engagement_models import ReferralCode, Referral
from .privacy_models import CustomerDataExport, ImportRun


class OrderCancellation(models.Model):
    """No customer data; retained even when cancellation precedes the first earn."""
    shop = models.ForeignKey("shops.Shop", on_delete=models.PROTECT, related_name="cancelled_orders")
    order_id = models.BigIntegerField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["shop", "order_id"], name="loyalty_cancel_shop_order")]
