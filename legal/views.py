from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.template.loader import render_to_string

from .models import TipoDocumento
from .services import LegalService

CACHE_SEGUNDOS = 60 * 60


def documento_publico(request, tipo):
    """
    Renderiza siempre la versión vigente. Público, sin autenticación.

    La clave de cache incluye el SHA-256 del contenido, no solo la URL como
    haría `cache_page`: al publicar una versión nueva cambia el hash y por
    tanto la clave, así que la corrección entra en vigor de inmediato en vez
    de quedar tapada por la versión anterior hasta que expire el cache.
    Tratándose de documentos legales, servir contenido obsoleto es justo lo
    que no puede pasar.

    Tampoco se invalida con `cache.clear()`: este mismo cache guarda los
    buckets del rate limiting y el candado de pagos de Openpay.
    """
    if tipo not in TipoDocumento.values:
        raise Http404

    documento = LegalService.documento_vigente(tipo)
    if not documento:
        raise Http404

    clave = f'legal:doc:{tipo}:{documento.hash_contenido}'
    html = cache.get(clave)
    if html is None:
        html = render_to_string('legal/documento.html', {'documento': documento},
                                request=request)
        cache.set(clave, html, CACHE_SEGUNDOS)
    return HttpResponse(html)
