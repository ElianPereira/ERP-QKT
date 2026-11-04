"""Webhook que recibe el estado de cuenta del banco desde el Worker de Cloudflare (Issue #401).

El Worker hace POST del correo crudo (RFC 822) con dos cabeceras:
`X-QKT-Timestamp` (epoch, segundos) y `X-QKT-Firma` = HMAC-SHA256 en hex de
`"<timestamp>.<cuerpo>"` con `ERP_ESTADOS_CORREO_SECRET`. Sin secreto
configurado, con firma mala o con timestamp de hace más de 5 minutos: 403.
"""
import hashlib
import hmac
import logging
import time

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core_erp.ratelimit import rate_limit

from . import services_estados_correo as servicio

logger = logging.getLogger(__name__)

MAX_CUERPO = 15 * 1024 * 1024
TOLERANCIA_SEGUNDOS = 300


def _firma_valida(secreto, timestamp, cuerpo, firma):
    esperada = hmac.new(secreto.encode(), timestamp.encode() + b'.' + cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperada, (firma or '').strip().lower())


@csrf_exempt
@require_POST
@rate_limit('estado_cuenta_correo', limit=30, window=60)
def estado_cuenta_correo_webhook(request):
    secreto = getattr(settings, 'ERP_ESTADOS_CORREO_SECRET', '')
    timestamp = request.headers.get('X-QKT-Timestamp', '')
    if (not secreto or not (timestamp.isascii() and timestamp.isdigit())
            or abs(time.time() - int(timestamp)) > TOLERANCIA_SEGUNDOS):
        return HttpResponse('Forbidden', status=403)
    try:
        largo = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        return HttpResponse('Bad request', status=400)
    if largo > MAX_CUERPO:
        return HttpResponse('Payload too large', status=413)
    cuerpo = request.read(MAX_CUERPO + 1)  # no request.body: Django lo corta a 2.5 MB
    if len(cuerpo) > MAX_CUERPO:
        return HttpResponse('Payload too large', status=413)
    if not _firma_valida(secreto, timestamp, cuerpo, request.headers.get('X-QKT-Firma')):
        return HttpResponse('Forbidden', status=403)
    try:
        bitacora = servicio.registrar_correo(cuerpo)
    except Exception:
        logger.exception("Estado de cuenta por correo: no se pudo registrar el correo")
        return HttpResponse('Error', status=500)
    if bitacora.resultado == 'PENDIENTE':
        transaction.on_commit(lambda pk=bitacora.pk: servicio.lanzar_procesamiento(pk))
    return JsonResponse({'estado': 'recibido'}, status=202)
