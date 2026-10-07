"""
Webhook entrante de WhatsApp Cloud API (Issue #346).

GET  /api/whatsapp/webhook/ → verificación que hace Meta al dar de alta el webhook.
POST /api/whatsapp/webhook/ → mensajes de clientes (`messages`) y respuestas que
     el propietario manda desde la app (`smb_message_echoes`, coexistencia).

Toda entrega se valida con la firma `X-Hub-Signature-256` (HMAC-SHA256 del
cuerpo con el App Secret). Sin `WA_APP_SECRET` configurado se rechaza todo:
fail-closed, mismo criterio que el feed iCal. Se responde 200 en cuanto el
mensaje queda guardado; el agente contesta en segundo plano, porque Meta
reintenta una entrega que tarda.
"""
import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core_erp.ratelimit import rate_limit

from . import services_agente

logger = logging.getLogger(__name__)


def _firma_valida(request) -> bool:
    secreto = getattr(settings, 'WA_APP_SECRET', '') or ''
    firma = request.headers.get('X-Hub-Signature-256', '')
    if not secreto or not firma.startswith('sha256='):
        return False
    esperada = hmac.new(secreto.encode(), request.body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(firma[len('sha256='):], esperada)


def _texto(mensaje: dict) -> str:
    """Texto legible de un mensaje de WhatsApp; el agente solo entiende texto."""
    tipo = mensaje.get('type')
    if tipo == 'text':
        return (mensaje.get('text') or {}).get('body', '')
    if tipo == 'button':
        return (mensaje.get('button') or {}).get('text', '')
    if tipo == 'interactive':
        interactivo = mensaje.get('interactive') or {}
        respuesta = interactivo.get('button_reply') or interactivo.get('list_reply') or {}
        if respuesta.get('id') in dict(services_agente.BOTONES_CONSENTIMIENTO):
            return f"[Tocó el botón «{respuesta.get('title', '')}» del mensaje de autorización]"
        return respuesta.get('title', '')
    return f'[El cliente envió un mensaje de tipo «{tipo}»; el asistente solo puede leer texto]'


def _procesar_cambio(cambio: dict) -> set:
    """Guarda lo que trae un `change` y devuelve las conversaciones a contestar."""
    valor = cambio.get('value') or {}
    por_contestar = set()

    if cambio.get('field') == 'messages':
        nombres = {c.get('wa_id'): (c.get('profile') or {}).get('name', '')
                   for c in valor.get('contacts') or []}
        for mensaje in valor.get('messages') or []:
            conv = services_agente.recibir_mensaje(
                telefono=mensaje.get('from', ''),
                nombre=nombres.get(mensaje.get('from'), ''),
                texto=_texto(mensaje),
                wamid=mensaje.get('id'),
            )
            if conv:
                boton = ((mensaje.get('interactive') or {}).get('button_reply') or {}).get('id', '')
                if boton:
                    services_agente.registrar_consentimiento(
                        telefono=conv.telefono, boton_id=boton, wamid=mensaje.get('id'))
                por_contestar.add(conv.pk)

    elif cambio.get('field') == 'smb_message_echoes':
        for eco in valor.get('message_echoes') or []:
            services_agente.registrar_respuesta_humana(
                telefono=eco.get('to', ''), texto=_texto(eco), wamid=eco.get('id'),
            )
    return por_contestar


@rate_limit(key='whatsapp_webhook', limit=300, window=60)
@csrf_exempt
@require_http_methods(['GET', 'POST'])
def whatsapp_webhook(request):
    if request.method == 'GET':
        token = getattr(settings, 'WA_WEBHOOK_VERIFY_TOKEN', '') or ''
        if (token and request.GET.get('hub.mode') == 'subscribe'
                and hmac.compare_digest(request.GET.get('hub.verify_token', ''), token)):
            return HttpResponse(request.GET.get('hub.challenge', ''), content_type='text/plain')
        return HttpResponseForbidden()

    if not _firma_valida(request):
        logger.warning("Webhook de WhatsApp con firma inválida o sin WA_APP_SECRET")
        return HttpResponseForbidden()

    try:
        datos = json.loads(request.body)
    except ValueError:
        return HttpResponse(status=400)

    por_contestar = set()
    for entrada in datos.get('entry') or []:
        for cambio in entrada.get('changes') or []:
            try:
                por_contestar |= _procesar_cambio(cambio)
            except Exception:
                # Un cambio mal formado no debe hacer que Meta reintente el
                # lote completo (y duplique los que sí se guardaron).
                logger.exception("Webhook de WhatsApp: no se pudo procesar un cambio")

    for conv_id in por_contestar:
        transaction.on_commit(lambda cid=conv_id: services_agente.lanzar_procesamiento(cid))
    if por_contestar:
        logger.info("Webhook de WhatsApp: %s conversación(es) por contestar", len(por_contestar))
    return HttpResponse('ok')

