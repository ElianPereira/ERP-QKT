from django.contrib import admin
from django.urls import path
# CORRECCIÓN: Importamos el nombre real de tu función
from comercial.views import generar_pdf_cotizacion 

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # Ruta para el PDF
    path('cotizacion/<int:cotizacion_id>/pdf/', generar_pdf_cotizacion, name='cotizacion_pdf'),
]