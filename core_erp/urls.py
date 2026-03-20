from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView
from django.contrib.auth import logout
from django.shortcuts import redirect
from comercial.views import ver_cartera_cxc
from comercial.views import generar_plan_pagos, descargar_plan_pagos_pdf
from comercial.views import generar_contrato, enviar_contrato_email
from airbnb.views import reporte_fiscal_airbnb

from comercial.views_portal import (
portal_acceso, portal_evento, 
portal_descargar_cotizacion, portal_descargar_plan, portal_descargar_contrato
)

try:
    from airbnb.views import calendario_unificado, reporte_pagos_airbnb, bloquear_en_airbnb
except ImportError:
    calendario_unificado = reporte_pagos_airbnb = bloquear_en_airbnb = None

# --- FUNCIÓN DE LOGOUT MANUAL (CORREGIDA) ---
def custom_logout(request):
    """
    Fuerza el cierre de sesión aceptando GET o POST.
    Redirige explícitamente al login del admin.
    """
    logout(request)
    return redirect('/admin/login/')

# Importamos las vistas de Comercial (Manejo de errores por si falta alguna)
try:
    from comercial.views import (
        generar_pdf_cotizacion, 
        enviar_cotizacion_email, 
        ver_calendario, 
        ver_dashboard_kpis,
        calculadora_insumos,
        exportar_cierre_excel,
        exportar_reporte_cotizaciones,
        generar_lista_compras,
        exportar_reporte_pagos,
        descargar_lista_compras_pdf,
        descargar_ficha_producto,
        webhook_manychat,
        configurar_plantilla_barra,
    )
except ImportError as e:
    print(f"Advertencia de importación en Comercial: {e}")

# Importamos vistas de otros módulos de forma segura
try:
    from nomina.views import cargar_nomina
except ImportError:
    cargar_nomina = None

try:
    from facturacion.views import crear_solicitud
except ImportError:
    crear_solicitud = None

urlpatterns = [
    # --- 1. REGLAS DE ORO (Van primero para interceptar acciones) ---
    
    # FIX LOGOUT: Esta línea intercepta CUALQUIER intento de ir a /admin/logout/
    path('admin/logout/', custom_logout, name='logout'),
    path('airbnb/', include('airbnb.urls')),

    # --- 2. EL DASHBOARD (Tu página principal del admin) ---
    path('admin/', ver_dashboard_kpis, name='admin_dashboard'),

    # --- 3. RUTAS DEL SISTEMA COMERCIAL ---
    path('cotizacion/<int:cotizacion_id>/pdf/', generar_pdf_cotizacion, name='cotizacion_pdf'),
    path('cotizacion/<int:cotizacion_id>/email/', enviar_cotizacion_email, name='cotizacion_email'),
    path('cotizacion/<int:cotizacion_id>/lista-compras/', descargar_lista_compras_pdf, name='cotizacion_lista_compras'),
    path('producto/<int:producto_id>/ficha/', descargar_ficha_producto, name='producto_ficha_pdf'),

    # ASISTENTE DE CONFIGURACIÓN DE PLANTILLA DE BARRA
    path('admin/comercial/configurar-plantilla-barra/', configurar_plantilla_barra, name='configurar_plantilla_barra'),

    # INTEGRACIÓN MANYCHAT
    path('api/webhook-manychat/', webhook_manychat, name='webhook_manychat'),

    # Reportes y Herramientas                                  
    path('admin/calendario/', ver_calendario, name='ver_calendario'),
    path('admin/exportar-cotizaciones/', exportar_reporte_cotizaciones, name='exportar_reporte_cotizaciones'),
    path('admin/reporte-pagos/', exportar_reporte_pagos, name='reporte_pagos'),
    path('admin/lista-compras/', generar_lista_compras, name='generar_lista_compras'),
    path('admin/calculadora/', calculadora_insumos, name='admin_calculadora'),
    path('admin/exportar-cierre/', exportar_cierre_excel, name='exportar_cierre_excel'),
    
    # --- 4. MÓDULOS EXTRA ---
    path('admin/nomina/cargar/', cargar_nomina if cargar_nomina else admin.site.urls, name='cargar_nomina'),
    path('admin/facturacion/nueva/', crear_solicitud if crear_solicitud else admin.site.urls, name='crear_solicitud'),

    # --- 5.MÓDULO AIRBNB ---

    path('admin/airbnb/calendario/', calendario_unificado, name='calendario_unificado'),
    path('admin/airbnb/reportes/pagos/', reporte_pagos_airbnb, name='reporte_pagos_airbnb'),
    path('admin/airbnb/bloquear/<int:cotizacion_id>/', bloquear_en_airbnb, name='bloquear_en_airbnb'),
    
    #---- CXC VISUALIZACION---
    path('admin/cartera/', ver_cartera_cxc, name='cartera_cxc'),

    #--- PLAN DE PAGOS---
    path('cotizacion/<int:cotizacion_id>/plan-pagos/generar/', generar_plan_pagos, name='generar_plan_pagos'),
    path('cotizacion/<int:cotizacion_id>/plan-pagos/pdf/', descargar_plan_pagos_pdf, name='plan_pagos_pdf'),
    
    #---Contrato de prestacion de servicios---
    path('cotizacion/<int:cotizacion_id>/contrato/generar/', generar_contrato,    name='cotizacion_contrato'),
    path('contrato/<int:contrato_id>/email/', enviar_contrato_email, name='contrato_email'),

    #---Reporte contbale airbnb---
    path('admin/airbnb/reporte-fiscal/', reporte_fiscal_airbnb, name='reporte_fiscal_airbnb'),

    # --- MÓDULO REPORTES ---
    path('admin/reportes/', include('reportes.urls')),

    # --- PORTAL DEL CLIENTE (público) ---
    path('mi-evento/', portal_acceso, name='portal_acceso'),
    path('mi-evento/<str:token>/', portal_evento, name='portal_evento'),
    path('mi-evento/<str:token>/cotizacion.pdf', portal_descargar_cotizacion, name='portal_descargar_cotizacion'),
    path('mi-evento/<str:token>/plan-pagos.pdf', portal_descargar_plan, name='portal_descargar_plan'),
    path('mi-evento/<str:token>/contrato.pdf', portal_descargar_contrato, name='portal_descargar_contrato'),

    # --- 6. ADMIN DE DJANGO (El resto de las URLs del admin) ---
    path('admin/', admin.site.urls),

    # --- 7. RUTA RAÍZ ---
    path('', RedirectView.as_view(url='/admin/', permanent=False)), 
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)