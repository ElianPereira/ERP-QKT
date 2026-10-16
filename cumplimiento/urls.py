from django.urls import path

from . import views

app_name = 'cumplimiento'

urlpatterns = [
    path('tablero/', views.tablero, name='tablero'),
    path('tablero/catalogo-sugerido/', views.cargar_catalogo, name='cargar_catalogo'),
]
