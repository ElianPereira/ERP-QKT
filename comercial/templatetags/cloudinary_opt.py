"""
Ex-filtro de optimización de entrega de imágenes de Cloudinary.

Cloudinary insertaba transformaciones (`f_auto,q_auto,w_<ancho>`) en la URL
de entrega para servir la misma imagen más ligera. Desde la migración del
storage a Cloudflare R2, R2 no ofrece esa optimización al vuelo, así que el
filtro queda como no-op explícito (devuelve la URL intacta) en vez de
borrarse, para no romper las 7 referencias existentes en
templates/landing/index.html. Optimizar el peso de esas imágenes queda como
deuda técnica aceptada, no se resuelve aquí.

Uso en plantilla (sin cambios):
    {% load cloudinary_opt %}
    background-image:url('{{ img.hero.imagen.url|cldn:1920 }}')
"""
from django import template

register = template.Library()


@register.filter
def cldn(url, width=None):
    return str(url or '')
