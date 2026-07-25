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

Un grupo de MODEL_SUBGROUPS puede además traer 'children': una lista de
grupos anidados con la misma forma (ej. "Clientes" dentro de "Ventas"). El
template soporta un tercer nivel de treeview para esto.
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
            'name': 'Ventas',
            'icon': 'fas fa-shopping-bag',
            'model_strs': {
                'comercial.cotizacion',
                'comercial.producto',
                'comercial.subproducto',
                'comercial.insumo',
                'comercial.plantillabarra',
            },
            'children': [
                {
                    'name': 'Clientes',
                    'icon': 'fas fa-user-friends',
                    'model_strs': {
                        'comercial.cliente',
                        'comercial.portalcliente',
                        'comercial.contratoservicio',
                    },
                },
            ],
        },
        {
            'name': 'Pagos',
            'icon': 'fas fa-credit-card',
            'model_strs': {
                'comercial.pago',
                'comercial.planpago',
                'comercial.recordatoriopago',
                'comercial.openpaytransaccion',
            },
        },
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


def _extract_group(models_pool, group):
    """
    Separa de `models_pool` los modelos de `group` (y recursivamente los de
    sus 'children'). Devuelve (grupo_armado, modelos_que_sobraron).
    """
    pool = models_pool
    child_groups = []
    for child in group.get('children', []):
        child_result, pool = _extract_group(pool, child)
        if child_result['models'] or child_result.get('sub_apps'):
            child_groups.append(child_result)

    own_model_strs = group.get('model_strs', set())
    own_matched, remaining = [], []
    for model in pool:
        (own_matched if model.get('model_str') in own_model_strs else remaining).append(model)

    result = {'name': group['name'], 'icon': group['icon'], 'models': own_matched}
    if child_groups:
        result['sub_apps'] = child_groups
    return result, remaining


def _extract_model_subgroups(app):
    subgroups = MODEL_SUBGROUPS.get(app['app_label'])
    if not subgroups:
        return

    pool = app['models']
    for group in subgroups:
        result, pool = _extract_group(pool, group)
        if result['models'] or result.get('sub_apps'):
            app.setdefault('sub_apps', []).append(result)
    app['models'] = pool


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
