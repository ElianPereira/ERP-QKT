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

COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY . .
# collectstatic solo compila assets, no necesita secretos reales — pero
# settings.py exige SECRET_KEY sin default (a propósito) y ninguna variable
# de Railway llega a este paso del build. El "2>/dev/null || true" que había
# aquí escondía ese fallo en cada build: /app/staticfiles/ nunca se generaba
# y la app servía en producción sin CSS ni imágenes (confirmado en Railway:
# "UserWarning: No directory at: /app/staticfiles/" y 404 en cada archivo
# de /static/). El valor de abajo es exclusivo de este RUN (no queda en el
# historial de capas ni en el contenedor final) — Railway sigue inyectando
# el SECRET_KEY real como variable de entorno en tiempo de ejecución, que
# es la que usa la app cuando corre de verdad.
RUN SECRET_KEY=build-only-placeholder-nunca-usado-en-runtime python manage.py collectstatic --noinput

# El proceso de la app corre sin privilegios: una RCE (WeasyPrint, un
# parser de XML/Excel, lo que sea) no debería salir con root dentro del
# contenedor. --system evita el UID/GID interactivo por defecto (con home,
# shell, etc.) que no hace falta aquí. chown de /app completo porque
# collectstatic y el checkout ya escribieron como root.
RUN groupadd --system appuser && useradd --system --gid appuser --no-create-home appuser \
    && chown -R appuser:appuser /app
USER appuser

# El seed es idempotente y no degrada una versión publicada desde el admin,
# así que es seguro correrlo en cada arranque. Nunca debe tumbar el deploy.
CMD python manage.py migrate --noinput && \
    (python manage.py seed_documentos_legales --publicar || true) && \
    gunicorn core_erp.wsgi:application \
        --bind 0.0.0.0:${PORT:-8080} \
        --workers 2 \
        --timeout 120 \
        --max-requests 1000 \
        --max-requests-jitter 100 \
        --preload \
        --access-logfile -
