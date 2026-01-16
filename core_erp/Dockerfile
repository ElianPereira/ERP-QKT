# Usamos una imagen base oficial de Python
FROM python:3.11-slim

# Evita que Python genere archivos .pyc y guarda logs en tiempo real
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Directorio de trabajo
WORKDIR /app

# 1. Instalamos las dependencias del sistema (LO CRÍTICO para WeasyPrint)
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# 2. Instalamos las dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Copiamos el resto del código
COPY . .

# 4. Comando para iniciar (basado en tus logs anteriores)
CMD ["gunicorn", "core_erp.wsgi:application", "--bind", "0.0.0.0:8080"]