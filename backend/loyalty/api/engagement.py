from rest_framework import serializers
from rest_framework.response import Response
from django.utils import timezone
from loyalty.models import CustomerProfile, Referral
from loyalty.services.customers import upsert_customer_profile
from loyalty.services.engagement import set_birthday
from loyalty.services.referrals import create_code, referral_summary, manual_reject
from loyalty.services.errors import LoyaltyError
from .common import AdminView, validated, choice_filter
from .proxy import ProxyView
from .serializers import StrictInputMixin, StrictInteger


class BirthdayInput(StrictInputMixin, serializers.Serializer):
    month = StrictInteger(min_value=1, max_value=12)
    day = StrictInteger(min_value=1, max_value=31)


class EmptyInput(StrictInputMixin, serializers.Serializer):
    pass


def signed_profile(request):
    # Identity comes only from Shopify's signed logged_in_customer_id, never JSON.
    profile, _ = upsert_customer_profile(request.shop, request.shopify_customer_id)
    return profile


class Birthday(ProxyView):
    def post(self, request):
        data = validated(BirthdayInput, request.data)
        return Response(set_birthday(signed_profile(request), actor="customer", **data))


class AdminBirthday(AdminView):
    def post(self, request, pk):
        data = validated(BirthdayInput, request.data)
        profile = CustomerProfile.objects.filter(pk=pk, shop=request.shop).first()
        if not profile:
            raise LoyaltyError("NOT_FOUND", "Customer not found.", 404)
        return Response(set_birthday(profile, actor=self.actor, admin=True, **data))


class Code(ProxyView):
    def post(self, request):
        validated(EmptyInput, request.data)
        profile = signed_profile(request)
        create_code(profile)
        return Response(referral_summary(profile))


class MyReferrals(ProxyView):
    def get(self, request):
        return Response(referral_summary(signed_profile(request)))


class ReferralSerializer(serializers.ModelSerializer):
    class Meta:
        model = Referral
        fields = ["id", "referrer_profile_id", "referee_customer_id", "referee_order_id", "status", "reject_reason", "created_at", "rewarded_at", "rejected_at"]


class Referrals(AdminView):
    def get(self, request):
        rows = Referral.objects.filter(shop=request.shop).order_by("-created_at", "-pk")
        rows = choice_filter(rows, request.query_params, "status", Referral.Status.values)
        rows = choice_filter(rows, request.query_params, "reject_reason", Referral.Reason.values)
        if request.query_params.get("referrer"):
            referrer = StrictInteger(min_value=1).run_validation(request.query_params["referrer"])
            rows = rows.filter(referrer_profile_id=referrer)
        return self.page(rows, ReferralSerializer)


class RejectReferral(AdminView):
    def post(self, request, pk):
        validated(EmptyInput, request.data)
        return Response(ReferralSerializer(manual_reject(request.shop, pk, self.actor)).data)
