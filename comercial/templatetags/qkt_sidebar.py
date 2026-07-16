"""
Agrupa apps y modelos del sidebar de Jazzmin como submenú de otra app.

Jazzmin solo soporta un nivel (app -> modelos). Aquí se reutiliza su propio
`get_side_menu` para dos tipos de anidación, ambas representadas igual
(`app['sub_apps']`) para que el template `templates/admin/base.html` las
renderice con el mismo treeview de segundo nivel:

1. Apps completas dentro de otra (ej. nomina/facturacion dentro de
   contabilidad) — ver SUBMENU_PARENTS.
2. Modelos sueltos de una misma app agrupados en un submenú propio (ej. los
   modelos de "Página Web" o de "Descuentos" dentro de comercial) — ver
   MODEL_SUBGROUPS. Estos no son apps reales, son grupos sintéticos con la
   misma forma ({'name', 'icon', 'models'}) que espera el template.
"""
from django import template
from jazzmin.templatetags.jazzmin import get_side_menu as _jazzmin_get_side_menu

register = template.Library()

# app_label (en minúsculas) -> app_label del padre bajo el que debe anidarse.
SUBMENU_PARENTS = {
    'nomina': 'contabilidad',
    'facturacion': 'contabilidad',
}

# app_label (en minúsculas) -> lista de submenús sintéticos a extraer de sus
# propios modelos. `model_strs` usa "app_label.nombremodelo" en minúsculas
# (mismo formato que jazzmin arma internamente para cada modelo).
MODEL_SUBGROUPS = {
    'comercial': [
        {
            'name': 'Página Web',
            'icon': 'fas fa-globe',
            'model_strs': {
                'comercial.imagenlanding',
                'comercial.testimoniolanding',
                'comercial.espaciolanding',
                'comercial.preguntafrecuente',
            },
        },
        {
            'name': 'Descuentos',
            'icon': 'fas fa-piggy-bank',
            'model_strs': {
                'comercial.tipoevento',
                'comercial.descuento',
                'comercial.descuentoaplicado',
                'comercial.temporada',
            },
        },
    ],
}


def _extract_model_subgroups(app):
    subgroups = MODEL_SUBGROUPS.get(app['app_label'])
    if not subgroups:
        return

    remaining = []
    matched = [[] for _ in subgroups]
    for model in app['models']:
        for i, group in enumerate(subgroups):
            if model.get('model_str') in group['model_strs']:
                matched[i].append(model)
                break
        else:
            remaining.append(model)

    app['models'] = remaining
    for group, models in zip(subgroups, matched):
        if models:
            app.setdefault('sub_apps', []).append(
                {'name': group['name'], 'icon': group['icon'], 'models': models}
            )


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

    for app in top_level:
        _extract_model_subgroups(app)

    return top_level
