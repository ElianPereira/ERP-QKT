from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView
from django.contrib.auth import logout
from django.shortcuts import redirect

# --- FUNCIÓN DE LOGOUT MANUAL (CORREGIDA) ---
def custom_logout(request):
    """
    Fuerza el cierre de sesión aceptando GET o POST.
    Redirige explícitamente al login del admin.
    """
    print("--- INTENTO DE LOGOUT DETECTADO ---") # Esto saldrá en tu consola
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
        forzar_migracion,
        exportar_reporte_pagos,
        descargar_lista_compras_pdf,
        descargar_ficha_producto
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
    # y lo manda a nuestra función personalizada, ignorando el admin por defecto.
    path('admin/logout/', custom_logout, name='logout'),
    
    # Utilidad de migración
    path('admin/ajustes/migrar-ahora/', forzar_migracion),

    # --- 2. EL DASHBOARD (Tu página principal del admin) ---
    # Nota: Esto reemplaza la página de inicio default de Django Admin
    path('admin/', ver_dashboard_kpis, name='admin_dashboard'),

    # --- 3. RUTAS DEL SISTEMA COMERCIAL ---
    path('cotizacion/<int:cotizacion_id>/pdf/', generar_pdf_cotizacion, name='cotizacion_pdf'),
    path('cotizacion/<int:cotizacion_id>/email/', enviar_cotizacion_email, name='cotizacion_email'),
    path('cotizacion/<int:cotizacion_id>/lista-compras/', descargar_lista_compras_pdf, name='cotizacion_lista_compras'),
    path('producto/<int:producto_id>/ficha/', descargar_ficha_producto, name='producto_ficha_pdf'),

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

    # --- 5. ADMIN DE DJANGO (El resto de las URLs del admin) ---
    # Es importante que esto vaya AL FINAL de las rutas que empiezan con 'admin/'
    path('admin/', admin.site.urls),

    # --- 6. RUTA RAÍZ ---
    path('', RedirectView.as_view(url='/admin/', permanent=False)), 
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)