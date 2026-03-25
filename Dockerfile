FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

WORKDIR /app

# Instalamos las librerías gráficas para WeasyPrint
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python manage.py collectstatic --noinput 2>/dev/null || true

CMD ["sh", "-c", "\
    python manage.py makemigrations --noinput 2>&1 && \
    python manage.py migrate --noinput 2>&1 && \
    python manage.py shell -c \"\
from django.contrib.auth import get_user_model; \
import os; \
User = get_user_model(); \
u = os.environ.get('DJANGO_SUPERUSER_USERNAME', 'admin'); \
e = os.environ.get('DJANGO_SUPERUSER_EMAIL', 'admin@example.com'); \
p = os.environ.get('DJANGO_SUPERUSER_PASSWORD', 'CAMBIAME-AHORA'); \
print('Superuser ya existe' if User.objects.filter(username=u).exists() else 'Creado' if User.objects.create_superuser(u, e, p) or True else '')\" 2>&1; \
    gunicorn core_erp.wsgi:application --bind 0.0.0.0:${PORT:-8080} --timeout 120 \
"]