from rest_framework.response import Response
from .common import AdminView
from loyalty.services.reports import report, export_csv


class Report(AdminView):
    def get(self, request, kind):
        return Response(report(request.shop, kind, request.query_params))


class Export(AdminView):
    def get(self, request, kind):
        return export_csv(request.shop, kind, request.query_params, self.actor)
