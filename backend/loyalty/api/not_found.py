from rest_framework.exceptions import NotFound

from .common import AdminView
from .proxy import ProxyView


class AdminNotFound(AdminView):
    def get(self, request, **kwargs):
        raise NotFound()

    post = patch = delete = put = get
    http_method_names = ["get", "post", "patch", "put", "delete", "head", "options"]


class ProxyNotFound(ProxyView):
    def get(self, request, **kwargs):
        raise NotFound()

    post = get
