import os
from celery import Celery

# Point Celery at your Django settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "rako.settings")

app = Celery("rako")

# Read CELERY_* settings from Django settings
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks.py in installed apps
app.autodiscover_tasks()
