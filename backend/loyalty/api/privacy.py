from django.http import JsonResponse
from rest_framework import serializers
from rest_framework.response import Response
from loyalty.models import ImportRun
from loyalty.services.privacy import anonymize_customer, customer_export
from .admin import profile_for
from .common import AdminView, choice_filter, validated
from .serializers import ProfileSerializer, StrictInputMixin


class AnonymizeInput(StrictInputMixin, serializers.Serializer):
    confirm = serializers.BooleanField()

    def validate_confirm(self, value):
        if value is not True:
            raise serializers.ValidationError("Confirm irreversible anonymization.")
        return value


class CustomerExport(AdminView):
    def get(self, request, pk):
        payload = customer_export(profile_for(request, pk), self.actor)
        response = JsonResponse(payload)
        response["Content-Disposition"] = f'attachment; filename="customer-{pk}-data.json"'
        response["X-Content-Type-Options"] = "nosniff"
        return response


class Anonymize(AdminView):
    def post(self, request, pk):
        validated(AnonymizeInput, request.data)
        row = anonymize_customer(profile_for(request, pk), self.actor, "merchant_request")
        return Response(ProfileSerializer(row).data)


class ImportSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImportRun
        fields = ("id", "status", "options", "counts", "last_order_id", "last_error", "created_at", "updated_at", "finished_at")


class Imports(AdminView):
    def get(self, request):
        query = choice_filter(ImportRun.objects.filter(shop=request.shop), request.query_params, "status", ImportRun.Status.values)
        return self.page(query.order_by("-created_at", "-pk"), ImportSerializer)
