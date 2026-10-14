from django.contrib import admin

from loyalty.admin import ReadOnlyAdmin

from .models import WebhookEvent


@admin.register(WebhookEvent)
class WebhookEventAdmin(ReadOnlyAdmin):
    list_display = ("webhook_id", "shop_domain", "topic", "status", "attempts", "received_at")
    list_filter = ("status", "topic", "shop")
    search_fields = ("webhook_id", "event_id", "shop_domain")
