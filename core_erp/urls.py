from django.contrib import admin
from django.urls import path

# IMPORTAMOS LAS 4 FUNCIONES QUE DEFINISTE EN VIEWS.PY
from comercial.views import (
    generar_pdf_cotizacion, 
    enviar_cotizacion_email, 
    ver_calendario, 
    ver_dashboard_kpis
)

urlpatterns = [
    # 1. Rutas para Cotizaciones (PDF y Email)
    path('cotizacion/<int:cotizacion_id>/pdf/', generar_pdf_cotizacion, name='cotizacion_pdf'),
    path('cotizacion/<int:cotizacion_id>/email/', enviar_cotizacion_email, name='cotizacion_email'),

    # 2. Ruta del Calendario
    path('admin/calendario/', ver_calendario, name='admin_calendario'),

    # 3. Ruta del Dashboard (El Puente) - ¡ESTA ES CLAVE!
    # Al poner esto ANTES de admin.site.urls, forzamos a usar tu dashboard nuevo
    path('admin/', ver_dashboard_kpis, name='admin_dashboard_custom'),

    # 4. Admin normal de Django
    path('admin/', admin.site.urls),
]