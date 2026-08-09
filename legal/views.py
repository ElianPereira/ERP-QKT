import mimetypes
from pathlib import Path

from django.contrib.auth.decorators import login_required, permission_required
from django.core.cache import cache
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string

from .models import AccesoIdentificacionARCO, SolicitudARCO, TipoDocumento
from .services import LegalService

CACHE_SEGUNDOS = 60 * 60


@login_required
@permission_required('legal.ver_identificacion_arco', raise_exception=True)
def descargar_identificacion_arco(request, solicitud_id):
    """Sirve la identificación sin revelar una URL directa del storage."""
    solicitud = get_object_or_404(SolicitudARCO, pk=solicitud_id)
    if not solicitud.identificacion:
        raise Http404

    archivo = solicitud.identificacion.open('rb')
    AccesoIdentificacionARCO.objects.create(
        solicitud=solicitud,
        usuario=request.user,
        ip=LegalService.obtener_ip(request),
    )

    nombre = Path(solicitud.identificacion.name).name
    content_type, _ = mimetypes.guess_type(nombre)
    response = FileResponse(
        archivo,
        as_attachment=False,
        filename=nombre,
        content_type=content_type or 'application/octet-stream',
    )
    response['Cache-Control'] = 'private, no-store'
    return response


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
        html = render_to_string(
            'legal/documento.html',
            {'documento': documento, 'cuerpo': documento.render_html()},
            request=request,
        )
        cache.set(clave, html, CACHE_SEGUNDOS)
    return HttpResponse(html)
