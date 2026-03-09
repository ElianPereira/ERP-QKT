# Actualización Módulo Airbnb v3
## Con iCal inverso y bloqueo manual

---

## 📦 Archivos incluidos

```
airbnb_update/
├── admin.py                    → Reemplaza airbnb/admin.py
├── views.py                    → Reemplaza airbnb/views.py
├── services.py                 → Reemplaza airbnb/services.py
├── urls.py                     → NUEVO: Crear airbnb/urls.py
├── validacion_fechas.py        → Copiar a airbnb/
├── templates/admin/airbnb/
│   ├── calendario_unificado.html
│   ├── bloquear_manual.html          → NUEVO
│   ├── pagoairbnb/change_list.html
│   └── anuncioairbnb/change_list.html
└── INSTRUCCIONES_validacion_cotizacion.py  → Leer y aplicar
```

---

## 🔧 Pasos de instalación

### 1. Reemplazar archivos en `airbnb/`

```bash
# Reemplazar estos archivos:
airbnb/admin.py
airbnb/views.py
airbnb/services.py

# Crear estos archivos nuevos:
airbnb/urls.py
airbnb/validacion_fechas.py
airbnb/templates/admin/airbnb/bloquear_manual.html
```

### 2. Actualizar `airbnb/models.py`

Agregar estado PENDIENTE a ReservaAirbnb (línea ~68):

```python
ESTADO_CHOICES = [
    ('CONFIRMADA', 'Confirmada'),
    ('PENDIENTE', 'Pendiente de Aceptar'),  # <-- AGREGAR
    ('CANCELADA', 'Cancelada'),
    ('BLOQUEADA', 'Bloqueado por Host'),
]
```

### 3. Actualizar `core_erp/urls.py`

Agregar al inicio:
```python
from django.urls import path, include

try:
    from airbnb.views import (
        calendario_unificado, 
        reporte_pagos_airbnb, 
        dashboard_airbnb,
        bloquear_en_airbnb,
    )
except ImportError:
    calendario_unificado = reporte_pagos_airbnb = dashboard_airbnb = bloquear_en_airbnb = None
```

Agregar rutas (antes de `admin.site.urls`):
```python
# --- MÓDULO AIRBNB ---
path('airbnb/', include('airbnb.urls')),
path('admin/airbnb/dashboard/', dashboard_airbnb, name='dashboard_airbnb'),
path('admin/airbnb/calendario/', calendario_unificado, name='calendario_unificado'),
path('admin/airbnb/reportes/pagos/', reporte_pagos_airbnb, name='reporte_pagos_airbnb'),
path('admin/airbnb/bloquear/<int:cotizacion_id>/', bloquear_en_airbnb, name='bloquear_en_airbnb'),
```

### 4. Migraciones

```bash
python manage.py makemigrations airbnb
python manage.py migrate
```

### 5. Subir cambios

```bash
git add .
git commit -m "feat: Agregar iCal inverso y bloqueo manual en Airbnb"
git push origin main
```

---

## 🆕 Nuevas funcionalidades

### 1. iCal Inverso (ERP → Airbnb)

**URL pública:** `https://tu-dominio.com/airbnb/ical/eventos/`

**Configurar en Airbnb:**
1. Ir a cada anuncio → Calendario → Disponibilidad
2. Click en "Importar calendario"
3. Pegar la URL del iCal
4. Airbnb sincronizará cada 2-24 horas

### 2. Bloqueo Manual

Cuando confirmas un evento, puedes ir directamente a bloquear en Airbnb:

**URL:** `/admin/airbnb/bloquear/{cotizacion_id}/`

O agregar un botón en el admin de cotizaciones (ver siguiente sección).

---

## 🔘 Agregar botón "Bloquear en Airbnb" en Cotizaciones (Opcional)

En `comercial/admin.py`, dentro de `CotizacionAdmin`, agregar método:

```python
def bloquear_airbnb_btn(self, obj):
    if obj.estado == 'CONFIRMADA':
        url = reverse('bloquear_en_airbnb', args=[obj.pk])
        return format_html(
            '<a href="{}" class="button" style="background:#FF5A5F; color:white; '
            'padding:5px 10px; border-radius:4px; text-decoration:none;" '
            'target="_blank">🔒 Airbnb</a>',
            url
        )
    return '-'
bloquear_airbnb_btn.short_description = "Bloquear"
```

Y agregarlo a `list_display`:
```python
list_display = (..., 'bloquear_airbnb_btn')
```

---

## 📊 Mejoras en importación CSV

El servicio ahora:
- Agrupa filas por código de confirmación
- Ignora filas de "Payout" (sin código)
- Suma correctamente retenciones ISR/IVA
- Soporta formato de Airbnb México

---

## ✅ Resumen de URLs

| URL | Descripción |
|-----|-------------|
| `/airbnb/ical/eventos/` | iCal público para importar en Airbnb |
| `/admin/airbnb/calendario/` | Calendario unificado |
| `/admin/airbnb/reportes/pagos/` | Reportes para contador |
| `/admin/airbnb/bloquear/{id}/` | Bloqueo manual en Airbnb |
