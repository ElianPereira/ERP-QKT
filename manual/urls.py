from django.urls import path

from . import views

app_name = 'manual'

# `pdf/` va antes que la sección por nombre: ninguna sección puede llamarse «pdf».
urlpatterns = [
    path('', views.indice, name='indice'),
    path('pdf/', views.pdf, name='pdf'),
    path('<slug:slug>/', views.seccion, name='seccion'),
]
