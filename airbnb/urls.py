"""
URLs del módulo Airbnb
======================
"""
from django.urls import path
from . import views

app_name = 'airbnb'

urlpatterns = [
    # Calendario público iCal (sin autenticación para que Airbnb pueda acceder)
    path('ical/eventos/', views.generar_ical_eventos, name='ical_eventos'),
    
    # Bloqueo manual
    path('bloquear/<int:cotizacion_id>/', views.bloquear_en_airbnb, name='bloquear_en_airbnb'),
]
