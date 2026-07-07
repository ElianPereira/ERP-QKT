from django.urls import path
from . import views

urlpatterns = [
    path('catalogo.pdf', views.descargar_catalogo_pdf, name='catalogo_pdf'),
]
