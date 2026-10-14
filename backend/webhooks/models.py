from django.db import models

from loyalty.base import TimeStampedModel


class WebhookEvent(TimeStampedModel):
    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Đã nhận"
        PROCESSING = "PROCESSING", "Đang xử lý"
        PROCESSED = "PROCESSED", "Đã xử lý"
        SKIPPED = "SKIPPED", "Bỏ qua"
        FAILED = "FAILED", "Lỗi"

    shop = models.ForeignKey("shops.Shop", verbose_name="Cửa hàng", on_delete=models.PROTECT,
                             null=True, blank=True, related_name="webhook_events")
    shop_domain = models.CharField("Tên miền cửa hàng", max_length=255)
    webhook_id = models.CharField("Mã lần giao webhook", max_length=100,
                                   help_text="Header X-Shopify-Webhook-Id dùng chống trùng.")
    event_id = models.CharField("Mã sự kiện", max_length=100, blank=True,
                                help_text="Header X-Shopify-Event-Id.")
    topic = models.CharField("Chủ đề", max_length=100)
    payload = models.JSONField("Nội dung webhook")
    status = models.CharField("Trạng thái", max_length=20, choices=Status.choices,
                              default=Status.RECEIVED)
    attempts = models.PositiveSmallIntegerField("Số lần thử", default=0)
    last_attempt_at = models.DateTimeField("Lần thử gần nhất", null=True, blank=True)
    skip_reason = models.CharField("Lý do bỏ qua", max_length=255, blank=True)
    error = models.TextField("Thông báo lỗi", blank=True)
    received_at = models.DateTimeField("Ngày nhận", auto_now_add=True)
    processed_at = models.DateTimeField("Ngày xử lý", null=True, blank=True)

    class Meta:
        verbose_name = "Sự kiện webhook"
        verbose_name_plural = "Sự kiện webhook"
        constraints = [models.UniqueConstraint(fields=["webhook_id"], name="webhooks_event_webhook_id_uniq")]
        indexes = [
            models.Index(fields=["event_id"], name="webhooks_event_event_id_idx"),
            models.Index(fields=["topic"], name="webhooks_event_topic_idx"),
            models.Index(fields=["status", "received_at"], name="webhooks_event_status_time_idx"),
            models.Index(fields=["topic", "received_at"], name="webhooks_event_topic_time_idx"),
        ]

    def __str__(self):
        return f"{self.topic}: {self.webhook_id} ({self.status})"
