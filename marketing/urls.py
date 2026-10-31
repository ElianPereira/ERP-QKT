from django.urls import path

from . import views

app_name = 'marketing'

urlpatterns = [
    path('tablero/', views.tablero, name='tablero'),
    path('decisiones/<int:pk>/<str:accion>/', views.decidir, name='decidir'),
]
