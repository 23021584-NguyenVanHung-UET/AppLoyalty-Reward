from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET
import mimetypes
import re

from .frontend import manifest_assets


@require_GET
def app_home(request):
    return render(request, "shops/app.html", {"shopify_client_id": settings.SHOPIFY_CLIENT_ID, **manifest_assets()})

@require_GET
def app_diagnostic(request):
    return render(request, "shops/diagnostic.html", {"shopify_client_id": settings.SHOPIFY_CLIENT_ID})


@require_GET
def app_asset(request, asset):
    root = (settings.FRONTEND_DIST_DIR / "assets").resolve()
    path = (root / asset).resolve()
    if (not path.is_relative_to(root) or not path.is_file() or
            not re.fullmatch(r"[A-Za-z0-9_./-]+-[A-Za-z0-9_-]{8,}\.(js|css|svg|png|webp|woff2)", asset)):
        raise Http404()
    response = FileResponse(path.open("rb"), content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    return response
