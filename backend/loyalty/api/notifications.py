from datetime import timedelta
import logging
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_q.tasks import async_task
from rest_framework import serializers
from rest_framework.response import Response
from loyalty.models import NotificationSettings, NotificationLog, AuditLog
from loyalty.services.administration import snapshot
from loyalty.services.errors import LoyaltyError
from loyalty.services.notifications import unsubscribe
from shops.models import Shop
from .common import AdminView, validated, choice_filter
from .serializers import StrictInputMixin

SETTING_FIELDS = ("enabled_types", "sender_display_name", "reply_to", "default_language")
logger = logging.getLogger(__name__)


def queue_test(shop_id, recipient):
    try:
        async_task("loyalty.tasks.send_test_email", shop_id, recipient, save=False)
    except Exception:
        logger.warning("notification_test_queue_failed shop_id=%s", shop_id)


class SettingsInput(StrictInputMixin, serializers.ModelSerializer):
    enabled_types = serializers.ListField(child=serializers.ChoiceField(choices=NotificationLog.Type.choices), max_length=len(NotificationLog.Type.choices))
    class Meta:
        model = NotificationSettings
        fields = SETTING_FIELDS
        extra_kwargs = {field: {"required": False} for field in SETTING_FIELDS if field != "enabled_types"}

    def validate_sender_display_name(self, value):
        if "\r" in value or "\n" in value:
            raise serializers.ValidationError("Single line required.")
        return value


def settings_snapshot(obj):
    values = snapshot(obj, SETTING_FIELDS)
    values["reply_to_configured"] = bool(values.pop("reply_to"))
    return values


class Settings(AdminView):
    def get(self, request):
        config, _ = NotificationSettings.objects.get_or_create(shop=request.shop)
        return Response(SettingsInput(config).data | {"admin_email": getattr(request.auth, "email", "")})

    def patch(self, request):
        data = validated(SettingsInput, request.data, partial=True)
        with transaction.atomic():
            Shop.objects.select_for_update().get(pk=request.shop.pk)
            config, _ = NotificationSettings.objects.get_or_create(shop=request.shop)
            before = settings_snapshot(config)
            for key, value in data.items():
                setattr(config, key, value)
            config.full_clean()
            config.save()
            after = settings_snapshot(config)
            after["reply_to_changed"] = "reply_to" in data
            AuditLog.objects.create(shop=request.shop, actor=self.actor, action="notifications.settings",
                target_type="NotificationSettings", target_id=str(config.pk), before=before, after=after)
        return Response(SettingsInput(config).data)


class LogSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationLog
        fields = ["id", "profile_id", "type", "status", "skip_reason", "attempts", "created_at", "sent_at", "last_error"]


class Logs(AdminView):
    def get(self, request):
        rows = NotificationLog.objects.filter(profile__shop=request.shop).order_by("-created_at", "-pk")
        rows = choice_filter(rows, request.query_params, "type", NotificationLog.Type.values)
        rows = choice_filter(rows, request.query_params, "status", NotificationLog.Status.values)
        return self.page(rows, LogSerializer)


class TestInput(StrictInputMixin, serializers.Serializer):
    email = serializers.EmailField(required=False)


class TestEmail(AdminView):
    def post(self, request):
        data = validated(TestInput, request.data)
        recipient = getattr(request.auth, "email", "") or data.get("email")
        if not recipient:
            raise LoyaltyError("EMAIL_REQUIRED", "Your Shopify session has no email. Enter your own test address.", 422)
        with transaction.atomic():
            Shop.objects.select_for_update().get(pk=request.shop.pk)
            config, _ = NotificationSettings.objects.get_or_create(shop=request.shop)
            now = timezone.now()
            if config.test_last_sent_at and config.test_last_sent_at > now-timedelta(seconds=settings.LOYALTY_TEST_EMAIL_INTERVAL_SECONDS):
                raise LoyaltyError("RATE_LIMITED", "Wait before sending another test email.", 429)
            config.test_last_sent_at = now
            config.save(update_fields=["test_last_sent_at", "updated_at"])
            AuditLog.objects.create(shop=request.shop, actor=self.actor, action="notifications.test",
                target_type="Shop", target_id=str(request.shop.pk), after={"queued": True})
            transaction.on_commit(lambda: queue_test(request.shop.pk, recipient))
        return Response({"status": "QUEUED"}, status=202)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def unsubscribe_view(request, token):
    valid = unsubscribe(token)
    response = HttpResponse("Email reminders disabled. / Đã tắt email nhắc nhở." if valid else "Invalid unsubscribe link.",
        status=200 if valid else 400, content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response
