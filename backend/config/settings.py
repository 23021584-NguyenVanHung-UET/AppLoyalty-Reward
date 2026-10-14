from pathlib import Path
 
import environ
 

BASE_DIR = Path(__file__).resolve().parent.parent
 
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")
 

SECRET_KEY = env("SECRET_KEY")
DEBUG = env.bool("DEBUG", False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
 
SHOPIFY_CLIENT_ID = env("SHOPIFY_CLIENT_ID", default="")
SHOPIFY_CLIENT_SECRET = env("SHOPIFY_CLIENT_SECRET", default="")
SHOPIFY_SHOP_DOMAIN = env("SHOPIFY_SHOP_DOMAIN", default="")
SHOPIFY_API_VERSION = env("SHOPIFY_API_VERSION", default="")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_q",
    "shops",
    "webhooks",
    "loyalty",
]
 
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "shops.customer_cors.CustomerCORSMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "shops.middleware.ShopifyFrameMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
 
ROOT_URLCONF = "config.urls"
 
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
 
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
 

DATABASES = {"default": env.db("DATABASE_URL")}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
 

Q_CLUSTER = {
    "name": "loyalty",
    "workers": 2,
    "timeout": 60,
    "retry": 120,       
    "orm": "default",   
    "save_limit": 500,  
}
 

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": ["shops.auth.ShopifySessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["shops.auth.IsShopifyAdmin"],
    "EXCEPTION_HANDLER": "loyalty.api.common.exception_handler",
}

LOYALTY_MAX_ADJUST_POINTS = env.int("LOYALTY_MAX_ADJUST_POINTS", default=100000)
LOYALTY_PROXY_REDEEM_PER_MINUTE = env.int("LOYALTY_PROXY_REDEEM_PER_MINUTE", default=10)
# Development opt-in: earning also requires DEBUG=True before accepting test orders.
LOYALTY_ALLOW_TEST_ORDERS = env.bool("LOYALTY_ALLOW_TEST_ORDERS", default=False)
LOYALTY_INTEGRITY_MAX_PROFILES = env.int("LOYALTY_INTEGRITY_MAX_PROFILES", default=25)
LOYALTY_INTEGRITY_TIMEOUT_MS = env.int("LOYALTY_INTEGRITY_TIMEOUT_MS", default=5000)
LOYALTY_REPORT_MAX_DAYS = env.int("LOYALTY_REPORT_MAX_DAYS", default=366)
LOYALTY_EXPORT_MAX_ROWS = env.int("LOYALTY_EXPORT_MAX_ROWS", default=200000)
WEBHOOK_PAYLOAD_RETENTION_DAYS = env.int("WEBHOOK_PAYLOAD_RETENTION_DAYS", default=30)
LOYALTY_IMPORT_MAX_WINDOW_DAYS = env.int("LOYALTY_IMPORT_MAX_WINDOW_DAYS", default=366)
LOYALTY_IMPORT_POLL_SECONDS = env.int("LOYALTY_IMPORT_POLL_SECONDS", default=5)
LOYALTY_IMPORT_POLL_TIMEOUT = env.int("LOYALTY_IMPORT_POLL_TIMEOUT", default=3600)
LOYALTY_PUBLIC_URL = env("LOYALTY_PUBLIC_URL", default="https://loyalty-dev.hungvandomain.dpdns.org").rstrip("/")
LOYALTY_TEST_EMAIL_INTERVAL_SECONDS = env.int("LOYALTY_TEST_EMAIL_INTERVAL_SECONDS", default=60)
# Django 6.1 rejects mixing MAILERS with deprecated EMAIL_* Django settings.
# Keep EMAIL_* environment variable names and map them into MAILERS options.
_loyalty_mail_backend = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="loyalty@localhost")
MAILERS = {"default": {
    "BACKEND": "loyalty.email_backend.RedactedConsoleBackend" if _loyalty_mail_backend == "django.core.mail.backends.console.EmailBackend" else _loyalty_mail_backend,
    "OPTIONS": {
        "host": env("EMAIL_HOST", default="localhost"),
        "port": env.int("EMAIL_PORT", default=587),
        "username": env("EMAIL_HOST_USER", default=""),
        "password": env("EMAIL_HOST_PASSWORD", default=""),
        "use_tls": env.bool("EMAIL_USE_TLS", default=True),
        "use_ssl": env.bool("EMAIL_USE_SSL", default=False),
        "timeout": env.int("EMAIL_TIMEOUT", default=15),
    },
}}

 

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
 

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
 

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
FRONTEND_DIST_DIR = BASE_DIR.parent / "frontend" / "dist"
