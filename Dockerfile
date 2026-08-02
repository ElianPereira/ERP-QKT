FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
WORKDIR /app

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

# El seed es idempotente y no degrada una versión publicada desde el admin,
# así que es seguro correrlo en cada arranque. Nunca debe tumbar el deploy.
# TEMPORAL — quitar después de leer el resultado en los logs del deploy.
# auditar_precios_iva es de solo lectura: mide el impacto del refactor de IVA
# sobre el histórico y arma la lista de precios con IVA. Se corre aquí porque
# no hay acceso a una terminal en Railway.
CMD python manage.py migrate --noinput && \
    (python manage.py seed_documentos_legales --publicar || true) && \
    (python manage.py auditar_precios_iva || true) && \
    gunicorn core_erp.wsgi:application \
        --bind 0.0.0.0:${PORT:-8080} \
        --workers 2 \
        --timeout 120 \
        --max-requests 1000 \
        --max-requests-jitter 100 \
        --preload \
        --access-logfile -
