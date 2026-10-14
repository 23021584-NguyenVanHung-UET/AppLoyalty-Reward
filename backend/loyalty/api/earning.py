from decimal import Decimal
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from loyalty.models import Campaign, EarnRule, Tier, TierHistory
from loyalty.services.earning_admin import FIELDS, save_definition
from .common import AdminView, bool_filter, validated
from .serializers import StrictInputMixin, StrictInteger


class DefinitionInput(StrictInputMixin, serializers.ModelSerializer):
    multiplier = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=Decimal("0.01"), max_value=Decimal("100"))


class RuleInput(DefinitionInput):
    target_ids = serializers.ListField(child=StrictInteger(min_value=1, max_value=2**63-1), min_length=1, max_length=1000)
    priority = StrictInteger(min_value=-100000, max_value=100000, required=False)

    class Meta:
        model = EarnRule
        fields = FIELDS[EarnRule]


class CampaignInput(DefinitionInput):
    audience_tier_ids = serializers.ListField(child=StrictInteger(min_value=1, max_value=2**63-1), max_length=1000, required=False)
    min_eligible_spend = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0"), allow_null=True, required=False)

    class Meta:
        model = Campaign
        fields = FIELDS[Campaign]


class TierInput(DefinitionInput):
    rank = StrictInteger(min_value=0, max_value=1000000)
    min_qualifying_points = StrictInteger(min_value=0, max_value=2**63-1, required=False)
    multiplier = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=Decimal("1"), max_value=Decimal("100"))
    perks_text = serializers.CharField(max_length=5000, allow_blank=True, required=False)

    class Meta:
        model = Tier
        fields = FIELDS[Tier]
        validators = []


def serializer_for(model):
    meta = type("Meta", (), {"model": model, "fields": ("id", *FIELDS[model], "created_at", "updated_at")})
    return type(f"{model.__name__}Serializer", (serializers.ModelSerializer,), {"Meta": meta})


class Definitions(AdminView):
    def get(self, request):
        query = self.model.objects.filter(shop=request.shop)
        active = bool_filter(request.query_params, "is_active")
        if active is not None:
            query = query.filter(is_active=active)
        search = request.query_params.get("search", "")[:150]
        if search:
            query = query.filter(name__icontains=search)
        return self.page(query.order_by("rank" if self.model is Tier else "-pk"), serializer_for(self.model))

    def post(self, request):
        data = validated(self.input_type, request.data)
        row = save_definition(self.model, request.shop, data, self.actor)
        return Response(serializer_for(self.model)(row).data, status=201)


class DefinitionDetail(AdminView):
    def get(self, request, pk):
        return Response(serializer_for(self.model)(get_object_or_404(self.model, shop=request.shop, pk=pk)).data)

    def patch(self, request, pk):
        data = validated(self.input_type, request.data, partial=True)
        row = save_definition(self.model, request.shop, data, self.actor,
            get_object_or_404(self.model, shop=request.shop, pk=pk))
        return Response(serializer_for(self.model)(row).data)


class DefinitionActive(AdminView):
    def post(self, request, pk, active):
        row = save_definition(self.model, request.shop, {"is_active": active}, self.actor,
            get_object_or_404(self.model, shop=request.shop, pk=pk))
        return Response(serializer_for(self.model)(row).data)


def definition_views(model, input_type):
    props = {"model": model, "input_type": input_type}
    return tuple(type(f"{model.__name__}{base.__name__}", (base,), props).as_view()
                 for base in (Definitions, DefinitionDetail, DefinitionActive))


class TierHistorySerializer(serializers.ModelSerializer):
    from_name = serializers.CharField(source="from_tier.name", default=None)
    to_name = serializers.CharField(source="to_tier.name", default=None)

    class Meta:
        model = TierHistory
        fields = ("id", "from_tier", "from_name", "to_tier", "to_name", "reason", "qualifying_points", "created_at")


class CustomerTierHistory(AdminView):
    def get(self, request, pk):
        from .admin import profile_for
        profile = profile_for(request, pk)
        return self.page(profile.tier_history.select_related("from_tier", "to_tier").order_by("-created_at", "-pk"), TierHistorySerializer)
