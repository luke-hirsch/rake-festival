# Dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# system deps (build, libpq for psycopg if you go Postgres)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# default; compose overrides the command
CMD ["gunicorn", "YOURPROJECT.wsgi:application", "-b", "0.0.0.0:8000", "--workers", "2"]
