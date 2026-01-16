from django.contrib import admin
from django.urls import path

# IMPORTAMOS TODAS LAS VISTAS (Incluida la nueva calculadora)
from comercial.views import (
    generar_pdf_cotizacion, 
    enviar_cotizacion_email, 
    ver_calendario, 
    ver_dashboard_kpis,
    calculadora_insumos # <--- Agregada
)

urlpatterns = [
    # 1. Rutas para Cotizaciones (PDF y Email)
    path('cotizacion/<int:cotizacion_id>/pdf/', generar_pdf_cotizacion, name='cotizacion_pdf'),
    path('cotizacion/<int:cotizacion_id>/email/', enviar_cotizacion_email, name='cotizacion_email'),

    # 2. Rutas de Herramientas Admin
    path('admin/calendario/', ver_calendario, name='admin_calendario'),
    path('admin/calculadora/', calculadora_insumos, name='admin_calculadora'), # <--- NUEVA RUTA

    # 3. Ruta del Dashboard (El Puente) - ¡ESTA VA ANTES DEL ADMIN!
    path('admin/', ver_dashboard_kpis, name='admin_dashboard_custom'),

    # 4. Admin normal de Django
    path('admin/', admin.site.urls),
]