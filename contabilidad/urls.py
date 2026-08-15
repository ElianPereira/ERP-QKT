from django.urls import path

from . import views

app_name = 'contabilidad'

urlpatterns = [
    path('balanza/', views.balanza_comprobacion, name='balanza'),
    path('estado-resultados/', views.estado_resultados, name='estado_resultados'),
    path(
        'autocomplete/asiento-bancario/',
        views.autocomplete_asiento_bancario,
        name='autocomplete_asiento_bancario',
    ),
    path('cerrar-historico/', views.cerrar_historico_view, name='cerrar_historico'),
]
