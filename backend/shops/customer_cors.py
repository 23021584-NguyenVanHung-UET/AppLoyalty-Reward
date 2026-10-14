"""CORS for worker-based UI extensions; never enables cookie credentials."""
from django.http import HttpResponse, JsonResponse
from django.utils.cache import patch_vary_headers


class CustomerCORSMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith("/api/customer/"):
            return self.get_response(request)
        if request.method == "OPTIONS":
            method = request.headers.get("Access-Control-Request-Method", "GET").upper()
            headers = {h.strip().lower() for h in request.headers.get("Access-Control-Request-Headers", "").split(",") if h.strip()}
            if method not in {"GET", "POST"} or headers - {"authorization", "content-type", "accept"}:
                response = JsonResponse({"code": "FORBIDDEN", "message": "Preflight is not allowed."}, status=403)
            else:
                response = HttpResponse(status=204)
                response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
                response["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Accept"
        else:
            response = self.get_response(request)
        # Shopify explicitly requires *: sandbox workers have no recognizable origin.
        # No Access-Control-Allow-Credentials, and authentication is always Bearer.
        response["Access-Control-Allow-Origin"] = "*"
        response["Cache-Control"] = "no-store"
        patch_vary_headers(response, ["Origin"])
        return response
