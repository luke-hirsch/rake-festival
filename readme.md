# Party Donations (Django + HTMX + PayPal(ish) + IMAP + Celery)

Tiny fundraiser site:
- Shows **goal** and **live total**
- People click **PayPal button** (if you have Business creds) or fallback **paypal.me** link
- **IMAP poller** parses PayPal confirmation emails and inserts donations (idempotent by transaction id)
- **Celery Beat** runs the poller every 5 minutes
- **SQLite** for storage (KISS)

> Project name used below: `rako`. If your Django project module is different, replace accordingly.

---

## Stack

- Django 5, HTMX (progress polling), Tailwind CDN
- Celery + Redis (broker/result)
- SQLite (file DB)
- Optional: PayPal REST (Business account)
- Email ingest: IMAP (Gmail app password), regex parser

---

## Env Vars (copy into `.env`)

### Django
DJANGO_DEBUG=True
DJANGO_SECRET_KEY=change-me
ALLOWED_HOSTS=*
CSRF_TRUSTED_ORIGINS=http://localhost

### SQLite (KISS)
SQLITE_PATH=/data/db.sqlite3

### Celery / Redis
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

### IMAP

IMAP_HOST=imap.gmail.com
IMAP_USER=you@gmail.com
IMAP_PASSWORD=your_app_password
IMAP_FOLDER=INBOX


## Docker

### `docker-compose.yml` (SQLite route)

Services:
- `web` (gunicorn)
- `worker` (celery)
- `beat` (celery beat)
- `redis` (broker)

**Run:**
```bash
docker compose up -d --build
docker compose logs -f web

First-time setup (inside containers):
# Migrate DB (web does this on boot too, but run if needed)
docker compose exec web python manage.py migrate

# Create admin
docker compose exec web python manage.py createsuperuser

# (Optional) Create a goal quickly
docker compose exec web python -c \
  "from donations.models import Goal; from decimal import Decimal; Goal.objects.create(title='DJ Spritgeld', target_amount=Decimal('250'))"


Open: http://localhost:8000
Admin (if you care tonight): http://localhost:8000/admin
