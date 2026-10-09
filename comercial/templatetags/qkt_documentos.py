from django import template
from django.utils.html import format_html

from core_erp.documentos import cifra, moneda

register = template.Library()

register.filter('moneda', moneda)
register.filter('cifra', cifra)

# Mismos tonos que core_erp/admin_ui.py; un valor no mapeado cae a neutro.
_TONOS = ('exito', 'alerta', 'error', 'info', 'neutro')


@register.filter
def tono_signo(valor):
    """Tono para un resultado: verde si es >= 0, rojo si es negativo."""
    try:
        return 'exito' if valor >= 0 else 'error'
    except TypeError:
        return 'neutro'


@register.simple_tag
def badge_doc(texto, tono='neutro'):
    if tono not in _TONOS:
        tono = 'neutro'
    return format_html('<span class="doc-badge doc-badge--{}">{}</span>', tono, texto)
