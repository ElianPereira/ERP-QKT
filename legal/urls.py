from django.urls import path

from . import views
from .models import TipoDocumento

app_name = 'legal'

urlpatterns = [
    path('aviso-de-privacidad/', views.documento_publico,
         {'tipo': TipoDocumento.AVISO_PRIVACIDAD}, name='aviso_privacidad'),
    path('terminos-y-condiciones/', views.documento_publico,
         {'tipo': TipoDocumento.TERMINOS}, name='terminos'),
    path('politica-de-cancelacion/', views.documento_publico,
         {'tipo': TipoDocumento.POLITICA_CANCELACION}, name='politica_cancelacion'),
    path('reglamento/', views.documento_publico,
         {'tipo': TipoDocumento.REGLAMENTO}, name='reglamento'),
]
