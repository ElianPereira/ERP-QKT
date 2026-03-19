"""
URLs del Módulo de Reportes
============================
ERP Quinta Ko'ox Tanil
"""
from django.urls import path
from . import views

app_name = 'reportes'

urlpatterns = [
    # Selector principal
    path('', views.selector_reportes, name='selector'),

    # Contabilidad
    path('balanza/', views.reporte_balanza, name='balanza'),
    path('estado-resultados/', views.reporte_estado_resultados, name='estado_resultados'),
    path('balance-general/', views.reporte_balance_general, name='balance_general'),
    path('libro-mayor/', views.reporte_libro_mayor, name='libro_mayor'),
    path('auxiliar/', views.reporte_auxiliar, name='auxiliar'),

    # Comercial
    path('cxc/', views.reporte_cxc, name='cxc'),
    path('cotizaciones/', views.reporte_cotizaciones, name='cotizaciones'),

    # Airbnb
    path('ocupacion/', views.reporte_ocupacion, name='ocupacion'),
    path('comparativo-airbnb/', views.reporte_comparativo_airbnb, name='comparativo_airbnb'),

    # Facturación
    path('facturas/', views.reporte_facturas, name='facturas'),
]
