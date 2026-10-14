from django.contrib import admin

from .models import Shop


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ("domain", "currency", "is_active", "uninstalled_at")
    list_filter = ("is_active", "currency")
    search_fields = ("domain",)
    readonly_fields = ("created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        return False
