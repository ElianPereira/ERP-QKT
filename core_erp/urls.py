from django.contrib import admin
from django.urls import path
# IMPORTANTE: Agrega 'enviar_cotizacion_email' aquí 👇
from comercial.views import generar_pdf_cotizacion, enviar_cotizacion_email

urlpatterns = [
    path('admin/', admin.site.urls),
    path('cotizacion/<int:cotizacion_id>/pdf/', generar_pdf_cotizacion, name='cotizacion_pdf'),
    
    # 👇 NUEVA RUTA PARA EL EMAIL
    path('cotizacion/<int:cotizacion_id>/email/', enviar_cotizacion_email, name='cotizacion_email'),
]