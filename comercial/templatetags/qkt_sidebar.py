"""
Agrupa apps del sidebar de Jazzmin como submenú de otra app.

Jazzmin solo soporta un nivel (app -> modelos). Aquí se reutiliza su propio
`get_side_menu` y se anidan ciertas apps dentro de otras (ej. catalogo dentro
de comercial) agregándolas como `sub_apps` de la app padre. El template
`templates/admin/base.html` sabe renderizar ese nivel extra.
"""
from django import template
from jazzmin.templatetags.jazzmin import get_side_menu as _jazzmin_get_side_menu

register = template.Library()

# app_label (en minúsculas) -> app_label del padre bajo el que debe anidarse.
SUBMENU_PARENTS = {
    'catalogo': 'comercial',
    'nomina': 'contabilidad',
    'facturacion': 'contabilidad',
}


@register.simple_tag(takes_context=True)
def get_side_menu_grouped(context):
    menu = _jazzmin_get_side_menu(context)
    by_label = {app['app_label']: app for app in menu}

    top_level = []
    for app in menu:
        parent_label = SUBMENU_PARENTS.get(app['app_label'])
        parent = by_label.get(parent_label) if parent_label else None
        if parent is not None and parent is not app:
            parent.setdefault('sub_apps', []).append(app)
        else:
            top_level.append(app)

    return top_level
