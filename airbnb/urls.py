"""
URLs del módulo Airbnb
======================
Rutas para dashboard, calendario unificado y reportes.
"""
from django.urls import path

from . import views

urlpatterns = [
    path('dashboard/', views.dashboard_airbnb, name='dashboard_airbnb'),
    path('calendario/', views.calendario_unificado, name='calendario_unificado'),
    path('reportes/pagos/', views.reporte_pagos_airbnb, name='reporte_pagos_airbnb'),
]
