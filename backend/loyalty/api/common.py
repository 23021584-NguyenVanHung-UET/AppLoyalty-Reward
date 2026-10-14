import logging
from django.core.exceptions import ValidationError as ModelValidationError

from rest_framework import exceptions
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView, exception_handler as drf_exception_handler

from loyalty.services.errors import LoyaltyError
from shops.auth import IsShopifyAdmin, ShopifySessionAuthentication

logger = logging.getLogger(__name__)


def exception_handler(exc, context):
    from shops.shopify import ShopifyError
    if isinstance(exc, ModelValidationError):
        exc = LoyaltyError("INVALID_INPUT", "Dữ liệu không hợp lệ.", 422)
    if isinstance(exc, ShopifyError):
        exc = LoyaltyError("SHOPIFY_UNAVAILABLE", "Chưa kết nối được Shopify. Vui lòng thử lại.", 503)
    if isinstance(exc, LoyaltyError):
        response = Response({"code": exc.code, "message": exc.message}, status=exc.status)
    else:
        response = drf_exception_handler(exc, context)
        codes = {
            400: ("INVALID_INPUT", "Dữ liệu hoặc tham số không hợp lệ."),
            401: ("INVALID_SESSION", "Phiên Shopify không hợp lệ hoặc đã hết hạn."),
            403: ("FORBIDDEN", "Không có quyền hoặc chữ ký Shopify không hợp lệ."),
            404: ("NOT_FOUND", "Không tìm thấy dữ liệu."),
            405: ("METHOD_NOT_ALLOWED", "Phương thức không được hỗ trợ."),
            406: ("NOT_ACCEPTABLE", "API chỉ hỗ trợ JSON."),
            415: ("UNSUPPORTED_MEDIA_TYPE", "Vui lòng gửi dữ liệu JSON."),
            429: ("RATE_LIMITED", "Bạn thao tác quá nhanh. Vui lòng thử lại sau."),
        }
        if response is None:
            # Never log exception text: it may contain provider codes or request data.
            logger.error("api_failure class=%s", type(exc).__name__)
            response = Response(status=500)
            from rest_framework.views import set_rollback
            set_rollback()
        code, message = codes.get(response.status_code,
                                  ("INTERNAL_ERROR", "Chưa thể xử lý yêu cầu. Vui lòng thử lại."))
        response.data = {"code": code, "message": message}
    if isinstance(exc, (exceptions.AuthenticationFailed, exceptions.NotAuthenticated)):
        response["X-Shopify-Retry-Invalid-Session-Request"] = "1"
    response["Cache-Control"] = "no-store"
    return response


class Pagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        # Page numbers avoid reflecting signed URLs or bearer material in links.
        return Response({"count": self.page.paginator.count, "page": self.page.number,
                         "pages": self.page.paginator.num_pages, "results": data})


class AdminView(APIView):
    authentication_classes = [ShopifySessionAuthentication]
    permission_classes = [IsShopifyAdmin]
    http_method_names = ["get", "post", "patch", "head", "options"]

    @property
    def actor(self):
        return f"shopify_user:{self.request.shopify_user_id}"

    def page(self, query, serializer, **kwargs):
        pagination = Pagination()
        rows = pagination.paginate_queryset(query, self.request, view=self)
        return pagination.get_paginated_response(serializer(rows, many=True, **kwargs).data)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


def validated(serializer_class, data, **kwargs):
    serializer = serializer_class(data=data, **kwargs)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def bool_filter(params, name):
    if name not in params:
        return None
    if params[name].lower() in ("true", "1"):
        return True
    if params[name].lower() in ("false", "0"):
        return False
    raise exceptions.ValidationError()


def ordered(query, request, allowed, default):
    ordering = request.query_params.get("ordering", default)
    if ordering.lstrip("-") not in allowed:
        raise exceptions.ValidationError()
    field = allowed[ordering.lstrip("-")]
    return query.order_by(("-" if ordering.startswith("-") else "") + field, "pk")


def choice_filter(query, params, name, choices):
    if name in params:
        if params[name] not in choices:
            raise exceptions.ValidationError()
        query = query.filter(**{name: params[name]})
    return query
