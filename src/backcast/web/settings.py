import os
import tempfile
from pathlib import Path

from django.utils.csp import CSP

# The fallback key is only for local use; `check --deploy` flags it. Backcast has no sessions or accounts.
SECRET_KEY = os.environ.get("BACKCAST_SECRET_KEY", "django-insecure-local-development-only")
DEBUG = os.environ.get("BACKCAST_DEBUG") == "1"
ALLOWED_HOSTS = os.environ.get("BACKCAST_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = ["django.contrib.staticfiles", "backcast.web"]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "backcast.web.urls"
WSGI_APPLICATION = "backcast.web.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "backcast.web.context_processors.price_source",
            ]
        },
    },
]

DATABASES = {}

# File-based so every server worker shares one result cache.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": os.environ.get("BACKCAST_CACHE_DIR", Path(tempfile.gettempdir()) / "backcast-cache"),
    }
}

# Uncached backtests one client may start per hour. Behind a reverse proxy, name the header it sets with the
# client's address (e.g. X-Forwarded-For), or every visitor shares the proxy's address and its limit.
BACKCAST_RUNS_PER_HOUR = int(os.environ.get("BACKCAST_RUNS_PER_HOUR", "30"))
BACKCAST_CLIENT_IP_HEADER = os.environ.get("BACKCAST_CLIENT_IP_HEADER")

STATIC_URL = "static/"
# WhiteNoise serves static files straight from the package; STATIC_ROOT only matters if you run `collectstatic`.
STATIC_ROOT = os.environ.get("BACKCAST_STATIC_ROOT")
WHITENOISE_USE_FINDERS = True

SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF],
    # Observable Plot writes a <style> element and style attributes into every chart.
    "style-src": [CSP.SELF, CSP.UNSAFE_INLINE],
    "img-src": [CSP.SELF, "data:"],
    "font-src": [CSP.SELF],
    "connect-src": [CSP.SELF],
    "base-uri": [CSP.NONE],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("BACKCAST_LOG_LEVEL", "INFO")},
}
