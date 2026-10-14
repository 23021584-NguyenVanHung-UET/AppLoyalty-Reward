"""Audited settings and a durable, PII-free notification outbox."""
from django.db import models
from django.utils import timezone

from .base import TimeStampedModel


class NotificationSettings(TimeStampedModel):
    shop = models.OneToOneField("shops.Shop", on_delete=models.PROTECT)
    enabled_types = models.JSONField(default=list, blank=True)
    sender_display_name = models.CharField(max_length=100, blank=True)
    reply_to = models.EmailField(blank=True)
    default_language = models.CharField(max_length=2, choices=[("en", "English"), ("vi", "Vietnamese")], default="en")
    test_last_sent_at = models.DateTimeField(null=True, blank=True)


class NotificationLog(models.Model):
    class Type(models.TextChoices):
        POINTS_EARNED = "POINTS_EARNED"
        POINTS_AVAILABLE = "POINTS_AVAILABLE"
        REWARD_CODE_ISSUED = "REWARD_CODE_ISSUED"
        POINTS_EXPIRING = "POINTS_EXPIRING"
        TIER_UPGRADED = "TIER_UPGRADED"
        TIER_GRACE_REMINDER = "TIER_GRACE_REMINDER"
        WELCOME_POINTS = "WELCOME_POINTS"
        BIRTHDAY_POINTS = "BIRTHDAY_POINTS"
        REFERRAL_REWARDED = "REFERRAL_REWARDED"

    class Status(models.TextChoices):
        QUEUED = "QUEUED"
        SENT = "SENT"
        FAILED = "FAILED"
        SKIPPED = "SKIPPED"

    profile = models.ForeignKey("loyalty.CustomerProfile", on_delete=models.PROTECT)
    type = models.CharField(max_length=30, choices=Type.choices)
    dedupe_key = models.CharField(max_length=200, unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    skip_reason = models.CharField(max_length=50, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=100, blank=True)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    queued_until = models.DateTimeField(default=timezone.now)
    lot = models.ForeignKey("loyalty.PointsLot", null=True, on_delete=models.PROTECT)
    redemption = models.ForeignKey("loyalty.Redemption", null=True, on_delete=models.PROTECT)
    tier_history = models.ForeignKey("loyalty.TierHistory", null=True, on_delete=models.PROTECT)
    tier_grace_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="notification_retry_idx")]


class NotificationExpiryLot(models.Model):
    notification = models.ForeignKey(NotificationLog, on_delete=models.PROTECT, related_name="expiry_lots")
    lot = models.ForeignKey("loyalty.PointsLot", on_delete=models.PROTECT)
    threshold_days = models.PositiveIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["lot", "threshold_days"], name="notification_lot_threshold_uniq")]
