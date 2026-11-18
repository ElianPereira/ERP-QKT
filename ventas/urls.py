from django.urls import path

from . import views

app_name = 'ventas'

urlpatterns = [
    path('tablero/', views.tablero, name='tablero'),
    path('mis-comisiones/', views.mis_comisiones, name='mis_comisiones'),
    path('mis-comisiones/corte/<int:pk>/', views.mis_comisiones_estado, name='mis_comisiones_estado'),
]
