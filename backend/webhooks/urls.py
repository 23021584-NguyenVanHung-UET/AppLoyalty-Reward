from django.urls import path

from .views import shopify_webhook

app_name = "webhooks"
urlpatterns = [path("shopify/", shopify_webhook, name="shopify")]
