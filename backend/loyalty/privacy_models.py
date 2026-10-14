from django.db import models
from django.db.models import Q
from django.utils import timezone


class CustomerDataExport(models.Model):
    profile = models.ForeignKey("loyalty.CustomerProfile", on_delete=models.PROTECT, related_name="data_exports")
    request_key = models.CharField(max_length=200, unique=True)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)


class ImportRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "RUNNING", "RUNNING"
        COMPLETED = "COMPLETED", "COMPLETED"
        FAILED = "FAILED", "FAILED"
        LIMITED = "LIMITED", "LIMITED"

    shop = models.ForeignKey("shops.Shop", on_delete=models.PROTECT, related_name="import_runs")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RUNNING)
    options = models.JSONField(default=dict)
    bulk_operation_ids = models.JSONField(default=dict)
    counts = models.JSONField(default=dict)
    last_order_id = models.BigIntegerField(default=0)
    last_error = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["shop"], condition=Q(status="RUNNING"), name="import_one_running_shop")]
