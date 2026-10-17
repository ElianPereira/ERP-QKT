from django.urls import path

from . import views

app_name = 'indicadores'

urlpatterns = [
    path('tablero/', views.tablero, name='tablero'),
    path('plan/', views.plan, name='plan'),
    path('exportar/', views.exportar, name='exportar'),
]
