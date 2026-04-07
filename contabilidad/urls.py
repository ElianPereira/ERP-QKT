from django.urls import path
from . import views

app_name = 'contabilidad'

urlpatterns = [
    path('balanza/', views.balanza_comprobacion, name='balanza'),
    path('estado-resultados/', views.estado_resultados, name='estado_resultados'),
]
