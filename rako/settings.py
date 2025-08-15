# rako/settings.py
from pathlib import Path
import os
import urllib.parse
from celery.schedules import crontab  # only used if you prefer cron syntax

# -----------------------------------------------------------------------------
# Core
# -----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() == "true"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "change-me")

# When DEBUG, allow everything. In prod, read comma-separated env.
ALLOWED_HOSTS = ["*"] if DEBUG else [h for h in os.getenv("ALLOWED_HOSTS", "").split(",") if h]

# -----------------------------------------------------------------------------
# Apps / Middleware
# -----------------------------------------------------------------------------
INSTALLED_APPS = [
    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Local
    "donations",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "rako.urls"
WSGI_APPLICATION = "rako.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# -----------------------------------------------------------------------------
# Database (SQLite by default)
# -----------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.getenv("SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
        "OPTIONS": {"timeout": 10},  # help with occasional SQLite locks
    }
}

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL and (DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")):
    urllib.parse.uses_netloc.append("postgres")
    url = urllib.parse.urlparse(DATABASE_URL)
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": url.path.lstrip("/"),
        "USER": url.username,
        "PASSWORD": url.password,
        "HOST": url.hostname,
        "PORT": url.port or 5432,
        "CONN_MAX_AGE": 60,
        "OPTIONS": {"sslmode": "disable"},
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# -----------------------------------------------------------------------------
# i18n / tz
# -----------------------------------------------------------------------------
LANGUAGE_CODE = "de-de"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

# -----------------------------------------------------------------------------
# Static files (nginx should serve STATIC_ROOT in prod)
# -----------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = os.getenv("STATIC_ROOT", str(BASE_DIR / "staticfiles"))

# -----------------------------------------------------------------------------
# Security (reverse proxy + TLS via nginx/certbot)
# -----------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = (not DEBUG) and (os.getenv("SECURE_SSL_REDIRECT", "true").lower() == "true")
X_FRAME_OPTIONS = "DENY"

# Include your HTTPS origin for CSRF in prod (comma-separated)
_csrf_origins = [o for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o]
CSRF_TRUSTED_ORIGINS = _csrf_origins if not DEBUG else []

# -----------------------------------------------------------------------------
# Celery / Redis
# -----------------------------------------------------------------------------
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True

# Run the IMAP pull every 5 minutes
CELERY_BEAT_SCHEDULE = {
    "pull-paypal-emails-every-5-min": {
        "task": "donations.pull_paypal_emails_task",
        # "schedule": crontab(minute="*/5"),  # alt: cron syntax
        "schedule": 300.0,  # seconds
        "kwargs": {
            "dry_run": False,
            "limit": int(os.getenv("IMAP_LIMIT", "50")),
            "folder": os.getenv("IMAP_FOLDER", "INBOX"),
            "mark_seen": os.getenv("IMAP_MARK_SEEN", "true").lower() == "true",
        },
    }
}

# -----------------------------------------------------------------------------
# PayPal (optional – if you have Business creds)
# -----------------------------------------------------------------------------
PAYPAL_ENV = os.getenv("PAYPAL_ENV", "sandbox").lower()
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_SECRET = os.getenv("PAYPAL_SECRET", "")

# -----------------------------------------------------------------------------
# Logging (keep it simple; logs to stdout for Docker)
# -----------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "[{levelname}] {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "celery": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
