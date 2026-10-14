import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("loyalty", "0011_birthday_constraints")]
    operations = [
        migrations.CreateModel(name="CustomerDataExport", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("request_key", models.CharField(max_length=200, unique=True)),
            ("payload", models.JSONField(default=dict)),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("profile", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="data_exports", to="loyalty.customerprofile")),
        ]),
        migrations.CreateModel(name="ImportRun", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("status", models.CharField(choices=[("RUNNING", "RUNNING"), ("COMPLETED", "COMPLETED"), ("FAILED", "FAILED"), ("LIMITED", "LIMITED")], default="RUNNING", max_length=12)),
            ("options", models.JSONField(default=dict)),
            ("bulk_operation_ids", models.JSONField(default=dict)),
            ("counts", models.JSONField(default=dict)),
            ("last_order_id", models.BigIntegerField(default=0)),
            ("last_error", models.CharField(blank=True, max_length=100)),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("finished_at", models.DateTimeField(null=True)),
            ("shop", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="import_runs", to="shops.shop")),
        ], options={"constraints": [models.UniqueConstraint(condition=models.Q(status="RUNNING"), fields=("shop",), name="import_one_running_shop")]}),
    ]
