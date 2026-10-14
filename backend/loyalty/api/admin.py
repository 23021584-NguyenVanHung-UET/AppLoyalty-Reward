"""Thin HTTP adapters for the embedded admin application."""
from django.db.models import Q
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response

from loyalty.models import AuditLog, CustomerProfile, PointsLot, PointsTransaction, ProgramConfig, Redemption, Reward
from loyalty.services import administration, reporting
from loyalty.services.adjustments import adjust_points
from webhooks.models import WebhookEvent
from webhooks.services import retry_event
from .common import AdminView, bool_filter, choice_filter, ordered, validated
from .serializers import (AdjustmentInput, AuditSerializer, BlockInput, ConfigInput, ConfigSerializer,
    ConfigHistorySerializer, DetailedLedgerSerializer, LedgerSerializer, LotSerializer, ProfileSerializer, RedemptionDetailSerializer,
    RedemptionSerializer, RewardInput, RewardSerializer, StrictInteger)


def profile_for(request, pk):
    return get_object_or_404(CustomerProfile.objects.select_related("tier", "shop"), pk=pk, shop=request.shop)


def query_int(request, name, default, low, high):
    return StrictInteger(min_value=low, max_value=high).run_validation(request.query_params.get(name, default))


class Me(AdminView):
    def get(self, request):
        config = ProgramConfig.get_effective(request.shop)
        return Response(dict(shop=request.shop.domain, currency=request.shop.currency,
            program_config=ConfigSerializer(config).data if config else None, server_time=timezone.now(),
            limits={"max_adjust_points": settings.LOYALTY_MAX_ADJUST_POINTS}))


class Dashboard(AdminView):
    def get(self, request):
        return Response(reporting.dashboard(request.shop, query_int(request, "days", 30, 1, 3650)))


class Customers(AdminView):
    def get(self, request):
        query = reporting.customer_query(request.shop, request.query_params.get("search", "")[:255],
            **{name: bool_filter(request.query_params, name) for name in ("has_balance", "in_debt", "blocked")})
        query = ordered(query, request, {"balance": "available_balance", "lifetime": "lifetime_earned",
            "created": "created_at"}, "-created")
        return self.page(query, ProfileSerializer)


class CustomerDetail(AdminView):
    def get(self, request, pk):
        profile = profile_for(request, pk)
        from loyalty.services.tiers import tier_state
        from .earning import TierHistorySerializer
        return Response({**ProfileSerializer(profile).data, "lots_counts": reporting.lot_counts(profile),
            "tier_status": tier_state(profile), "tier_history": TierHistorySerializer(
                profile.tier_history.select_related("from_tier", "to_tier").order_by("-created_at", "-pk")[:20], many=True).data})


class CustomerLots(AdminView):
    def get(self, request, pk):
        profile = profile_for(request, pk)
        query = choice_filter(profile.points_lots.all(), request.query_params, "status", PointsLot.Status.values)
        return self.page(query.order_by("-created_at", "-pk"), LotSerializer)


class CustomerLedger(AdminView):
    def get(self, request, pk):
        profile = profile_for(request, pk)
        query = choice_filter(profile.points_transactions.all(), request.query_params, "type", PointsTransaction.Type.values)
        serializer = DetailedLedgerSerializer if request.query_params.get("detail") == "1" else LedgerSerializer
        return self.page(query.order_by("-created_at", "-pk"), serializer)


class CustomerBlock(AdminView):
    def post(self, request, pk, blocked=True):
        data = validated(BlockInput, request.data) if blocked else {}
        row = administration.set_blocked(profile_for(request, pk), blocked, data.get("reason", ""), self.actor)
        return Response(ProfileSerializer(row).data)


class CustomerAdjust(AdminView):
    def post(self, request, pk):
        data = validated(AdjustmentInput, request.data)
        return Response(adjust_points(profile_for(request, pk), actor=self.actor, **data))


class Rewards(AdminView):
    def get(self, request):
        query = Reward.objects.filter(shop=request.shop).order_by("sort_order", "pk")
        return self.page(query, RewardSerializer)

    def post(self, request):
        data = validated(RewardInput, request.data)
        row = administration.save_reward(request.shop, data, self.actor)
        return Response(RewardSerializer(row).data, status=201)


class RewardDetail(AdminView):
    def get(self, request, pk):
        return Response(RewardSerializer(get_object_or_404(Reward, pk=pk, shop=request.shop)).data)

    def patch(self, request, pk):
        row = get_object_or_404(Reward, pk=pk, shop=request.shop)
        data = validated(RewardInput, request.data, partial=True)
        return Response(RewardSerializer(administration.save_reward(request.shop, data, self.actor, row)).data)


class RewardActive(AdminView):
    def post(self, request, pk, active):
        row = get_object_or_404(Reward, pk=pk, shop=request.shop)
        return Response(RewardSerializer(administration.save_reward(request.shop,
            {"is_active": active}, self.actor, row)).data)


class Config(AdminView):
    def get(self, request):
        row = ProgramConfig.get_effective(request.shop)
        return Response(ConfigSerializer(row).data if row else None)

    def post(self, request):
        data = validated(ConfigInput, request.data)
        row = administration.create_config(request.shop, data, self.actor)
        return Response(ConfigSerializer(row).data, status=201)


class ConfigHistory(AdminView):
    def get(self, request):
        return self.page(ProgramConfig.objects.filter(shop=request.shop, deletion__isnull=True)
                         .order_by("-version"), ConfigHistorySerializer)


class ConfigDelete(AdminView):
    http_method_names = ["delete", "options"]

    def delete(self, request, pk):
        administration.delete_config(request.shop, pk, self.actor)
        return Response(status=204)


class Redemptions(AdminView):
    def get(self, request):
        query = choice_filter(Redemption.objects.filter(profile__shop=request.shop), request.query_params,
                              "status", Redemption.Status.values)
        search = request.query_params.get("search", "")[:255]
        if search:
            query = query.filter(Q(discount_code__icontains=search) | Q(profile__email__icontains=search)
                | Q(profile__first_name__icontains=search) | Q(profile__last_name__icontains=search)
                | Q(profile__shopify_customer_id__icontains=search))
        return self.page(query.order_by("-created_at", "-pk"), RedemptionSerializer)


class RedemptionDetail(AdminView):
    def get(self, request, pk):
        row = get_object_or_404(Redemption, pk=pk, profile__shop=request.shop)
        return Response(RedemptionDetailSerializer(row).data)


class RedemptionCancel(AdminView):
    def post(self, request, pk):
        row = get_object_or_404(Redemption.objects.select_related("profile"), pk=pk, profile__shop=request.shop)
        row = administration.cancel_redemption(row, self.actor)
        return Response(RedemptionSerializer(row).data)


def event_data(row):
    from .diagnostics import webhook_error
    return dict(id=row.pk, topic=row.topic, status=row.status, attempts=row.attempts,
        received_at=row.received_at, last_attempt_at=row.last_attempt_at, processed_at=row.processed_at,
        error=webhook_error(row.status))


class WebhookEvents(AdminView):
    def get(self, request):
        from .common import Pagination
        query = choice_filter(WebhookEvent.objects.filter(shop=request.shop), request.query_params,
                              "status", WebhookEvent.Status.values)
        if "topic" in request.query_params:
            query = query.filter(topic=request.query_params["topic"])
        pagination = Pagination()
        rows = pagination.paginate_queryset(query.defer("payload", "error").order_by("-received_at", "-pk"), request)
        return pagination.get_paginated_response([event_data(row) for row in rows])


class WebhookDetail(AdminView):
    def get(self, request, pk):
        row = get_object_or_404(WebhookEvent, pk=pk, shop=request.shop)
        return Response({**event_data(row), "payload": reporting.mask_payload(row.payload)})


class WebhookRetry(AdminView):
    def post(self, request, pk):
        row = get_object_or_404(WebhookEvent, pk=pk, shop=request.shop)
        return Response(retry_event(row, self.actor))


class Integrity(AdminView):
    def get(self, request):
        return Response(reporting.integrity_report(request.shop,
            query_int(request, "limit", 25, 1, 100), query_int(request, "after_id", 0, 0, 2**63-1)))


class Audit(AdminView):
    def get(self, request):
        query = AuditLog.objects.filter(shop=request.shop)
        for name in ("action", "target_type", "target_id"):
            if name in request.query_params:
                query = query.filter(**{name: request.query_params[name]})
        return self.page(query.order_by("-created_at", "-pk"), AuditSerializer)
