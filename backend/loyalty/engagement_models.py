from django.db import models
from django.utils import timezone
from .base import NoDeleteQuerySet


class ReferralCode(models.Model):
    objects = NoDeleteQuerySet.as_manager()
    profile = models.OneToOneField("loyalty.CustomerProfile", on_delete=models.PROTECT, related_name="referral_code")
    code = models.CharField(max_length=8, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)


class Referral(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        REWARDED = "REWARDED"
        REJECTED = "REJECTED"

    class Reason(models.TextChoices):
        UNKNOWN_CODE = "UNKNOWN_CODE"
        INACTIVE_CODE = "INACTIVE_CODE"
        SELF_REFERRAL = "SELF_REFERRAL"
        EXISTING_CUSTOMER = "EXISTING_CUSTOMER"
        PROFILE_TOO_OLD = "PROFILE_TOO_OLD"
        BELOW_MINIMUM = "BELOW_MINIMUM"
        MONTHLY_CAP = "MONTHLY_CAP"
        ALREADY_REFERRED = "ALREADY_REFERRED"
        REFUNDED = "REFUNDED"
        CANCELLED = "CANCELLED"
        NO_POINTS_HELD = "NO_POINTS_HELD"
        PROFILE_INELIGIBLE = "PROFILE_INELIGIBLE"
        ORDER_MISMATCH = "ORDER_MISMATCH"
        EXPIRED_PENDING = "EXPIRED_PENDING"
        MANUAL_REJECT = "MANUAL_REJECT"

    objects = NoDeleteQuerySet.as_manager()
    shop = models.ForeignKey("shops.Shop", on_delete=models.PROTECT)
    referrer_profile = models.ForeignKey("loyalty.CustomerProfile", null=True, on_delete=models.PROTECT, related_name="sent_referrals")
    referee_profile = models.ForeignKey("loyalty.CustomerProfile", on_delete=models.PROTECT, related_name="received_referrals")
    referee_customer_id = models.BigIntegerField()
    referee_order_id = models.BigIntegerField()
    source_code = models.ForeignKey(ReferralCode, null=True, on_delete=models.PROTECT)
    order_lot = models.ForeignKey("loyalty.PointsLot", on_delete=models.PROTECT)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    reject_reason = models.CharField(max_length=30, choices=Reason.choices, blank=True)
    rule_snapshot = models.JSONField(default=dict)
    qualifying_month = models.DateField()
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    rewarded_at = models.DateTimeField(null=True)
    rejected_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["shop", "referee_order_id"], name="referral_order_uniq"),
            models.UniqueConstraint(fields=["shop", "referee_customer_id"], condition=models.Q(status__in=["PENDING", "REWARDED"]), name="referral_referee_uniq"),
        ]
        indexes = [models.Index(fields=["status", "created_at"], name="referral_pending_idx"),
                   models.Index(fields=["referrer_profile", "qualifying_month", "status"], name="referral_month_idx")]
