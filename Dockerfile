FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

WORKDIR /app

# Instalamos las librerías gráficas (CORREGIDO EL GUION QUE FALTABA)
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

CMD ["sh", "-c", "python manage.py migrate && python manage.py shell -c \"from django.contrib.auth import get_user_model; User = get_user_model(); not User.objects.filter(username='admin').exists() and User.objects.create_superuser('admin', 'admin@example.com', 'admin123')\" && gunicorn core_erp.wsgi:application --bind 0.0.0.0:8080"]