"""Tags de la barra de controles de las listas del admin (Issue #322)."""
from django import template
from django.contrib.admin.views.main import SEARCH_VAR

register = template.Library()

FILTROS_VISIBLES_DEFAULT = 4


@register.simple_tag
def qkt_filtros_visibles(cl):
    """Cuántos filtros van en la barra; el resto queda en "Más filtros".
    Cada ModelAdmin puede fijarlo con `filtros_visibles`."""
    return getattr(cl.model_admin, 'filtros_visibles', FILTROS_VISIBLES_DEFAULT)


@register.simple_tag
def qkt_filtros_activos(cl):
    """Filtros aplicados, para pintarlos como chips con su "×".

    En los filtros de Django (y en todo SimpleListFilter) la primera opción
    es "Todos": su query string es la URL actual sin ese filtro, así que
    sirve tal cual como enlace para quitarlo.
    """
    chips = []
    for spec in cl.filter_specs:
        opciones = list(spec.choices(cl))
        if len(opciones) < 2:
            continue
        elegida = next((o for o in opciones[1:] if o.get('selected')), None)
        if elegida is None:
            continue
        chips.append({
            'titulo': spec.title,
            'valor': elegida['display'],
            'quitar': opciones[0]['query_string'],
        })
    if cl.query:
        chips.append({
            'titulo': 'Búsqueda',
            'valor': cl.query,
            'quitar': cl.get_query_string(remove=[SEARCH_VAR]),
        })
    return chips
