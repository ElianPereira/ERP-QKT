from django.contrib import admin
from django.urls import path
from comercial.views import ver_recibo  # <--- Importamos la vista que acabamos de crear

urlpatterns = [
    path('admin/', admin.site.urls),
    # Esta es la nueva ruta: ejemplo.com/recibo/1
    path('recibo/<int:id_cotizacion>/', ver_recibo, name='ver_recibo'),
]