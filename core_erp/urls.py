from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView

# Importamos las vistas de Comercial
from comercial.views import (
    generar_pdf_cotizacion, 
    enviar_cotizacion_email, 
    ver_calendario, 
    ver_dashboard_kpis,
    calculadora_insumos,
    exportar_cierre_excel,
    exportar_reporte_cotizaciones,
    generar_lista_compras # NUEVO IMPORT
)

# Importamos vistas de otros módulos
from nomina.views import cargar_nomina
from facturacion.views import crear_solicitud

urlpatterns = [
    # 1. EL DASHBOARD
    path('admin/', ver_dashboard_kpis, name='admin_dashboard'),

    # 2. Rutas del Sistema Comercial
    path('cotizacion/<int:cotizacion_id>/pdf/', generar_pdf_cotizacion, name='cotizacion_pdf'),
    path('cotizacion/<int:cotizacion_id>/email/', enviar_cotizacion_email, name='cotizacion_email'),
    
    # --- Calendario y Reportes ---
    path('admin/calendario/', ver_calendario, name='ver_calendario'),
    path('admin/exportar-cotizaciones/', exportar_reporte_cotizaciones, name='exportar_reporte_cotizaciones'),
    
    # NUEVA RUTA PARA LISTA DE COMPRAS
    path('admin/lista-compras/', generar_lista_compras, name='generar_lista_compras'),
    
    path('admin/calculadora/', calculadora_insumos, name='admin_calculadora'),
    path('admin/exportar-cierre/', exportar_cierre_excel, name='exportar_cierre_excel'),
    
    # 3. Rutas de Nómina y Facturación
    path('admin/nomina/cargar/', cargar_nomina, name='cargar_nomina'),
    path('admin/facturacion/nueva/', crear_solicitud, name='crear_solicitud'),

    # 4. ADMIN DE DJANGO
    path('admin/', admin.site.urls),

    # 5. RUTA RAÍZ
    path('', RedirectView.as_view(url='/admin/', permanent=False)), 
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)