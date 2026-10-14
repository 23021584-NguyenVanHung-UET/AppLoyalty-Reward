"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path, re_path
from loyalty.api.urls import admin_patterns, proxy_patterns
from shops.views import app_asset, app_diagnostic, app_home
from loyalty.api.notifications import unsubscribe_view

urlpatterns = [
    path("unsubscribe/<str:token>/", unsubscribe_view, name="unsubscribe"),
    path('admin/', admin.site.urls),
    path('webhooks/', include('webhooks.urls')),
    path('app/', app_home, name='app-home'),
    path('app/diagnostic/', app_diagnostic, name='app-diagnostic'),
    path('app-static/assets/<path:asset>', app_asset, name='app-asset'),
    path('api/admin/', include(admin_patterns)),
    path('api/customer/', include('loyalty.api.customer')),
    path('proxy/', include(proxy_patterns)),
    # Vue history routes: direct loads and Shopify sidebar destinations render
    # the same app shell. Keep the explicit diagnostic route above this fallback.
    re_path(r'^app/.*$', app_home),
]
