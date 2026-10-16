from django.urls import path

from . import views

app_name = 'indicadores'

urlpatterns = [
    path('tablero/', views.tablero, name='tablero'),
]
