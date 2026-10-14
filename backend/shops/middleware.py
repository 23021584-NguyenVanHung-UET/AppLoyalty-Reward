from .auth import configured_domain


class ShopifyFrameMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith(("/app/", "/api/admin/")):
            try:
                domain = configured_domain()
            except ValueError:
                return response
            response.headers.pop("X-Frame-Options", None)
            response["Content-Security-Policy"] = (
                f"frame-ancestors https://admin.shopify.com https://{domain}")
            response["Cache-Control"] = "no-store"
        return response
