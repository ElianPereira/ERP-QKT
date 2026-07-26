"""
Filtro de optimización de entrega de imágenes de Cloudinary.

Inserta transformaciones (`f_auto,q_auto[,w_<ancho>]`) en la URL de entrega:
  - f_auto: formato moderno automático (WebP/AVIF) según el navegador.
  - q_auto: calidad automática (recorta bytes sin pérdida visible).
  - w_<ancho>: limita el ancho real servido al que necesita el diseño.

Con esto la MISMA imagen (editable desde el admin) se sirve mucho más ligera,
reduciendo el ancho de banda de Cloudinary ~80-90%. Cloudinary genera y cachea
la versión derivada una sola vez.

Uso en plantilla:
    {% load cloudinary_opt %}
    background-image:url('{{ img.hero.imagen.url|cldn:1920 }}')

Si la URL no es de Cloudinary (/upload/) se devuelve intacta, así que es seguro
aplicarlo a cualquier `.url`.
"""
from django import template

register = template.Library()


@register.filter
def cldn(url, width=None):
    url = str(url or '')
    if '/upload/' not in url:
        return url
    # Idempotente: si ya insertamos optimización, no duplicar.
    if '/upload/f_auto' in url:
        return url
    transform = 'f_auto,q_auto'
    if width:
        transform += f',w_{width}'
    return url.replace('/upload/', f'/upload/{transform}/', 1)
