from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView
from django.contrib.auth import views as auth_views  # <--- IMPORTANTE: Necesario para el fix del Logout

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
    descargar_ficha_producto  # <--- Vista para el PDF de venta rápida (Brochure)
)

# Importamos vistas de otros módulos (Nómina y Facturación)
# Usamos try/except por si el módulo aún no está listo, para evitar errores 500
try:
    from nomina.views import cargar_nomina
except ImportError:
    cargar_nomina = None

try:
    from facturacion.views import crear_solicitud
except ImportError:
    crear_solicitud = None

urlpatterns = [
    # --- RUTA DE UTILIDAD PARA MIGRAR DB DESDE LA WEB ---
    path('admin/ajustes/migrar-ahora/', forzar_migracion),

    # 1. EL DASHBOARD (Sobrescribe la vista principal del admin)
    path('admin/', ver_dashboard_kpis, name='admin_dashboard'),

    # 2. Rutas del Sistema Comercial (Cotizaciones y Productos)
    path('cotizacion/<int:cotizacion_id>/pdf/', generar_pdf_cotizacion, name='cotizacion_pdf'),
    path('cotizacion/<int:cotizacion_id>/email/', enviar_cotizacion_email, name='cotizacion_email'),
    path('cotizacion/<int:cotizacion_id>/lista-compras/', descargar_lista_compras_pdf, name='cotizacion_lista_compras'),
    
    # --- RUTA PARA FICHA DE PRODUCTO (Brochure de Ventas) ---
    path('producto/<int:producto_id>/ficha/', descargar_ficha_producto, name='producto_ficha_pdf'),

    # --- Calendario, Reportes y Compras ---
    path('admin/calendario/', ver_calendario, name='ver_calendario'),
    
    # Reportes Financieros
    path('admin/exportar-cotizaciones/', exportar_reporte_cotizaciones, name='exportar_reporte_cotizaciones'),
    path('admin/reporte-pagos/', exportar_reporte_pagos, name='reporte_pagos'),
    
    # Herramientas
    path('admin/lista-compras/', generar_lista_compras, name='generar_lista_compras'),
    path('admin/calculadora/', calculadora_insumos, name='admin_calculadora'),
    path('admin/exportar-cierre/', exportar_cierre_excel, name='exportar_cierre_excel'),
    
    # 3. Rutas de Nómina y Facturación (Si existen)
    path('admin/nomina/cargar/', cargar_nomina if cargar_nomina else admin.site.urls, name='cargar_nomina'),
    path('admin/facturacion/nueva/', crear_solicitud if crear_solicitud else admin.site.urls, name='crear_solicitud'),

    # --- FIX PARA EL BOTÓN DE CERRAR SESIÓN ---
    # Esto permite que el logout funcione con un clic simple (GET) y redirija al login
    # Es vital colocarlo ANTES de admin.site.urls
    path('admin/logout/', auth_views.LogoutView.as_view(http_method_names=['get', 'post'], next_page='/admin/'), name='logout'),

    # 4. ADMIN DE DJANGO (Resto de funcionalidades)
    path('admin/', admin.site.urls),

    # 5. RUTA RAÍZ (Redirección automática al Login/Admin)
    path('', RedirectView.as_view(url='/admin/', permanent=False)), 
]

# Configuración para servir archivos media (imágenes) en modo DEBUG
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)