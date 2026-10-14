from django.db import models

from loyalty.base import TimeStampedModel


class Shop(TimeStampedModel):
    domain = models.CharField("Tên miền Shopify", max_length=255)
    currency = models.CharField("Tiền tệ cửa hàng", max_length=3)
    is_active = models.BooleanField("Đang hoạt động", default=True)
    iana_timezone = models.CharField(max_length=100, blank=True, default="")
    uninstalled_at = models.DateTimeField("Ngày gỡ ứng dụng", null=True, blank=True)

    class Meta:
        verbose_name = "Cửa hàng"
        verbose_name_plural = "Cửa hàng"
        constraints = [models.UniqueConstraint(fields=["domain"], name="shops_shop_domain_uniq")]

    def __str__(self):
        return self.domain
