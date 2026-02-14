from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView
from django.contrib.auth import logout
from django.shortcuts import redirect

# --- FUNCIÓN DE LOGOUT MANUAL (Infalible) ---
def custom_logout(request):
    """Fuerza el cierre de sesión y redirige al login"""
    logout(request)
    return redirect('/admin/login/')

# Importamos las vistas de Comercial
from comercial.views import (
    generar_pdf_cotizacion, 
    enviar_cotizacion_email, 
    ver_calendario, 
    ver_dashboard_kpis,
    calculadora_insumos,
    exportar_cierre_excel,
    exportar_reporte_cotizaciones,
    generar_lista_compras,
    forzar_migracion,
    exportar_reporte_pagos,
    descargar_lista_compras_pdf,
    descargar_ficha_producto
)

# Importamos vistas de otros módulos (con manejo de errores)
try:
    from nomina.views import cargar_nomina
except ImportError:
    cargar_nomina = None

try:
    from facturacion.views import crear_solicitud
except ImportError:
    crear_solicitud = None

urlpatterns = [
    # --- RUTA DE UTILIDAD PARA MIGRAR DB ---
    path('admin/ajustes/migrar-ahora/', forzar_migracion),

    # 1. EL DASHBOARD
    path('admin/', ver_dashboard_kpis, name='admin_dashboard'),

    # 2. Rutas del Sistema Comercial
    path('cotizacion/<int:cotizacion_id>/pdf/', generar_pdf_cotizacion, name='cotizacion_pdf'),
    path('cotizacion/<int:cotizacion_id>/email/', enviar_cotizacion_email, name='cotizacion_email'),
    path('cotizacion/<int:cotizacion_id>/lista-compras/', descargar_lista_compras_pdf, name='cotizacion_lista_compras'),
    path('producto/<int:producto_id>/ficha/', descargar_ficha_producto, name='producto_ficha_pdf'),

    # --- Calendario y Reportes ---
    path('admin/calendario/', ver_calendario, name='ver_calendario'),
    path('admin/exportar-cotizaciones/', exportar_reporte_cotizaciones, name='exportar_reporte_cotizaciones'),
    path('admin/reporte-pagos/', exportar_reporte_pagos, name='reporte_pagos'),
    path('admin/lista-compras/', generar_lista_compras, name='generar_lista_compras'),
    path('admin/calculadora/', calculadora_insumos, name='admin_calculadora'),
    path('admin/exportar-cierre/', exportar_cierre_excel, name='exportar_cierre_excel'),
    
    # 3. Módulos Extra
    path('admin/nomina/cargar/', cargar_nomina if cargar_nomina else admin.site.urls, name='cargar_nomina'),
    path('admin/facturacion/nueva/', crear_solicitud if crear_solicitud else admin.site.urls, name='crear_solicitud'),

    # --- FIX DEFINITIVO LOGOUT (Se coloca ANTES de admin.site.urls) ---
    path('admin/logout/', custom_logout, name='logout'),

    # 4. ADMIN DE DJANGO
    path('admin/', admin.site.urls),

    # 5. RUTA RAÍZ
    path('', RedirectView.as_view(url='/admin/', permanent=False)), 
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)