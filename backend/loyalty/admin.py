from django.contrib import admin
from loyalty.privacy_models import CustomerDataExport, ImportRun

from .models import AuditLog, CustomerProfile, PointsLot, PointsTransaction, ProgramConfig, ProgramConfigDeletion, Redemption, Reward, EarnRule, Campaign, Tier, TierHistory, EarningRevision
from .models import NotificationLog, NotificationSettings, NotificationExpiryLot
from .models import ReferralCode, Referral


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)


@admin.register(NotificationLog)
class NotificationLogAdmin(ReadOnlyAdmin):
    list_display = ("id", "profile_id", "type", "status", "skip_reason", "attempts", "created_at", "sent_at")
    list_filter = ("profile__shop", "type", "status")


admin.site.register(NotificationSettings, ReadOnlyAdmin)
admin.site.register(NotificationExpiryLot, ReadOnlyAdmin)
admin.site.register(ReferralCode, ReadOnlyAdmin)
admin.site.register(Referral, ReadOnlyAdmin)


@admin.register(ProgramConfig)
class ProgramConfigAdmin(admin.ModelAdmin):
    list_display = ("shop", "version", "effective_from", "is_enabled", "spend_per_point",
                    "pending_days", "expiry_days", "allow_negative_balance")
    list_filter = ("shop", "is_enabled", "allow_negative_balance")
    search_fields = ("shop__domain", "created_by")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        if obj is not None:
            return tuple(field.name for field in self.model._meta.fields)
        return ("created_at", "updated_at")


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = ("shop", "shopify_customer_id", "email", "available_balance",
                    "pending_balance", "is_blocked", "is_anonymized")
    list_filter = ("shop", "is_blocked", "is_anonymized")
    search_fields = ("=shopify_customer_id", "email", "first_name", "last_name")
    readonly_fields = ("available_balance", "pending_balance", "lifetime_earned",
                       "tier", "tier_since", "tier_drop_since", "tier_last_qualifying_points", "tier_evaluated_at",
                       "created_at", "updated_at")


for model in (TierHistory, EarningRevision, EarnRule, Campaign, Tier):
    admin.site.register(model, ReadOnlyAdmin)


@admin.register(ProgramConfigDeletion)
class ProgramConfigDeletionAdmin(ReadOnlyAdmin):
    list_display = ("config", "actor", "created_at")


@admin.register(PointsLot)
class PointsLotAdmin(admin.ModelAdmin):
    list_display = ("id", "profile", "source_type", "status", "points_total",
                    "points_remaining", "available_at", "expires_at")
    list_filter = ("profile__shop", "source_type", "status")
    search_fields = ("=source_order_id", "=profile__shopify_customer_id", "profile__email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PointsTransaction)
class PointsTransactionAdmin(ReadOnlyAdmin):
    list_display = ("id", "profile", "type", "delta", "balance_after", "actor", "created_at")
    list_filter = ("profile__shop", "type", "reason_code")
    search_fields = ("idempotency_key", "=order_id", "=refund_id", "profile__email", "actor")


@admin.register(Reward)
class RewardAdmin(admin.ModelAdmin):
    list_display = ("name", "shop", "points_cost", "discount_type", "discount_value", "is_active")
    list_filter = ("shop", "is_active", "discount_type")
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Redemption)
class RedemptionAdmin(admin.ModelAdmin):
    list_display = ("id", "profile", "reward", "status", "points_spent", "discount_code", "created_at")
    list_filter = ("profile__shop", "status", "reward")
    search_fields = ("idempotency_key", "discount_code", "profile__email", "shopify_discount_id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdmin):
    list_display = ("shop", "actor", "action", "target_type", "target_id", "created_at")
    list_filter = ("shop", "action", "target_type")
    search_fields = ("actor", "target_id", "action")


@admin.register(ImportRun)
class ImportRunAdmin(ReadOnlyAdmin):
    list_display = ("id", "shop", "status", "last_order_id", "created_at")
    list_filter = ("shop", "status")


@admin.register(CustomerDataExport)
class CustomerDataExportAdmin(ReadOnlyAdmin):
    list_display = ("id", "profile", "created_at")
