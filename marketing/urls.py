from django.urls import path

from . import views

app_name = 'marketing'

urlpatterns = [
    path('tablero/', views.tablero, name='tablero'),
]
