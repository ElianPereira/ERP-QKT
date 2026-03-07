# Módulo Airbnb - ERP Quinta Ko'ox Tanil

## Descripción

Módulo de integración con Airbnb para:
- Sincronización de calendario vía iCal
- Detección automática de conflictos con eventos de la quinta
- Gestión de pagos con régimen fiscal de Plataformas Tecnológicas
- Reportes contables separados para el contador

## Estructura

```
airbnb/
├── __init__.py
├── apps.py
├── models.py            # AnuncioAirbnb, ReservaAirbnb, PagoAirbnb, ConflictoCalendario
├── admin.py             # Panel de administración
├── views.py             # Dashboard, calendario, reportes
├── urls.py              # Rutas del módulo
├── services.py          # Lógica de negocio (sincronización, importación CSV)
├── migrations/
│   └── __init__.py
├── management/
│   └── commands/
│       ├── setup_airbnb.py        # Configuración inicial
│       └── sincronizar_airbnb.py  # Comando de sincronización
└── templates/
    └── admin/
        └── airbnb/
            ├── dashboard.html
            ├── calendario_unificado.html
            ├── reporte_pagos.html
            └── importar_csv.html
```

## Instalación

### Paso 1: Copiar archivos

Copia la carpeta `airbnb/` completa a la raíz de tu proyecto Django (al mismo nivel que `comercial/`, `nomina/`, etc.)

### Paso 2: Registrar la app en settings.py

Edita `core_erp/settings.py` y agrega `'airbnb'` a `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    'jazzmin',
    'django.contrib.admin',
    # ... otras apps ...
    'comercial',
    'nomina',
    'facturacion',
    'airbnb',  # <-- AGREGAR ESTA LÍNEA
    # ...
]
```

### Paso 3: Agregar URLs

Edita `core_erp/urls.py` y agrega las rutas del módulo:

```python
# Al inicio, agregar el import
try:
    from airbnb.views import dashboard_airbnb, calendario_unificado, reporte_pagos_airbnb
except ImportError:
    dashboard_airbnb = calendario_unificado = reporte_pagos_airbnb = None

# En urlpatterns, agregar estas rutas (antes de path('admin/', admin.site.urls)):
    # --- MÓDULO AIRBNB ---
    path('admin/airbnb/dashboard/', dashboard_airbnb, name='dashboard_airbnb'),
    path('admin/airbnb/calendario/', calendario_unificado, name='calendario_unificado'),
    path('admin/airbnb/reportes/pagos/', reporte_pagos_airbnb, name='reporte_pagos_airbnb'),
```

### Paso 4: Agregar icono en Jazzmin (opcional)

En `core_erp/settings.py`, dentro de `JAZZMIN_SETTINGS["icons"]`, agregar:

```python
"icons": {
    # ... iconos existentes ...
    "airbnb.AnuncioAirbnb": "fas fa-home",
    "airbnb.ReservaAirbnb": "fas fa-calendar-check",
    "airbnb.PagoAirbnb": "fas fa-money-bill-wave",
    "airbnb.ConflictoCalendario": "fas fa-exclamation-triangle",
},
```

### Paso 5: Ejecutar migraciones

```bash
python manage.py makemigrations airbnb
python manage.py migrate
```

### Paso 6: Configurar anuncios iniciales

```bash
python manage.py setup_airbnb
```

Esto creará los 3 anuncios con sus URLs de iCal:
- Habitación 1 - Quinta (afecta eventos)
- Habitación 2 - Quinta (afecta eventos)
- Casa Completa (NO afecta eventos)

### Paso 7: Primera sincronización

```bash
python manage.py sincronizar_airbnb
```

### Paso 8: Subir a producción

```bash
git add .
git commit -m "feat: Agregar módulo Airbnb con sincronización iCal y reportes"
git push origin main
```

## Uso

### Panel de Administración

Después de la instalación, en el admin verás la sección "Airbnb - Hospedaje" con:

- **Anuncios Airbnb**: Gestión de listings
- **Reservas Airbnb**: Reservaciones sincronizadas
- **Pagos Airbnb**: Ingresos con retenciones calculadas
- **Conflictos de Calendario**: Alertas de fechas que chocan

### Dashboard

Accede a `/admin/airbnb/dashboard/` para ver:
- Estadísticas del mes
- Próximas reservas
- Conflictos pendientes
- Gráfico de ingresos

### Calendario Unificado

Accede a `/admin/airbnb/calendario/` para ver eventos de la quinta + reservas de Airbnb en una sola vista.

### Reportes para Contador

Accede a `/admin/airbnb/reportes/pagos/` para:
- Ver desglose de pagos con retenciones (ISR 4%, IVA 8%)
- Filtrar por mes/año
- Exportar a Excel

### Importar Pagos desde CSV

1. Ve a Admin > Pagos Airbnb
2. Click en "Importar CSV"
3. Sube el archivo descargado de Airbnb > Ganancias

### Sincronización Automática (Opcional)

Para sincronizar automáticamente cada 6 horas, agrega al cron:

```bash
0 */6 * * * cd /ruta/proyecto && python manage.py sincronizar_airbnb >> /var/log/airbnb_sync.log 2>&1
```

## Régimen Fiscal

El módulo está configurado para **Actividad Empresarial - Plataformas Tecnológicas** (Art. 113-A LISR):

- Retención ISR: 4%
- Retención IVA: 8%

Las retenciones se calculan automáticamente al registrar pagos.

## Notas

- Las habitaciones 1 y 2 están marcadas como "afecta eventos" - generarán alertas de conflicto
- La casa completa NO afecta eventos de la quinta
- Los conflictos se detectan automáticamente al sincronizar
