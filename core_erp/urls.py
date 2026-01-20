from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView

# --- IMPORTS PARA EL TRUCO DE CREAR ADMIN (NUEVO) ---
from django.contrib.auth.models import User
from django.http import HttpResponse
# ----------------------------------------------------

# Importamos las vistas de Comercial
from comercial.views import (
    generar_pdf_cotizacion, 
    enviar_cotizacion_email, 
    ver_calendario, 
    ver_dashboard_kpis,
    calculadora_insumos,
    exportar_cierre_excel,
    exportar_reporte_cotizaciones,
    generar_lista_compras
)

# Importamos vistas de otros módulos
from nomina.views import cargar_nomina
from facturacion.views import crear_solicitud

# --- FUNCIÓN TEMPORAL: CREAR SUPERUSUARIO ---
def crear_superusuario_view(request):
    try:
        # Verificamos si ya existe para no duplicar errores
        if not User.objects.filter(username='admin').exists():
            User.objects.create_superuser('admin', 'admin@example.com', 'admin123')
            return HttpResponse("""
                <div style='font-family: sans-serif; text-align: center; margin-top: 50px;'>
                    <h1 style='color: green;'>✅ ¡LISTO!</h1>
                    <p>Usuario creado con éxito.</p>
                    <hr>
                    <p><strong>Usuario:</strong> admin</p>
                    <p><strong>Contraseña:</strong> admin123</p>
                    <br>
                    <a href='/admin/'>👉 Ir al Login</a>
                </div>
            """)
        else:
            return HttpResponse("""
                <div style='font-family: sans-serif; text-align: center; margin-top: 50px;'>
                    <h1 style='color: orange;'>⚠️ El usuario 'admin' ya existe.</h1>
                    <a href='/admin/'>👉 Ir al Login</a>
                </div>
            """)
    except Exception as e:
        return HttpResponse(f"<h1>Error Crítico: {e}</h1>")
# ----------------------------------------------------

urlpatterns = [
    # --- RUTA SECRETA DE EMERGENCIA ---
    path('crear-admin-secreto/', crear_superusuario_view),

    # 1. EL DASHBOARD (Ojo: esto intercepta la raíz de admin)
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

    # 4. ADMIN DE DJANGO (Standard)
    # Nota: Como pusiste 'admin/' arriba para el dashboard, asegúrate de que no choquen.
    # Django revisa en orden. Si 'ver_dashboard_kpis' no maneja sub-rutas, las dejará pasar.
    path('admin/', admin.site.urls),

    # 5. RUTA RAÍZ
    path('', RedirectView.as_view(url='/admin/', permanent=False)), 
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)