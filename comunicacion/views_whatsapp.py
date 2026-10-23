"""
Webhook entrante de WhatsApp Cloud API (Issue #346).

GET  /api/whatsapp/webhook/ → verificación que hace Meta al dar de alta el webhook.
POST /api/whatsapp/webhook/ → mensajes de clientes (`messages`) y respuestas que
     el propietario manda desde la app (`smb_message_echoes`, coexistencia).

Toda entrega se valida con la firma `X-Hub-Signature-256` (HMAC-SHA256 del
cuerpo con el App Secret). Sin `META_WA_APP_SECRET` configurado se rechaza todo:
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

from comercial import services_ordenes_wa, services_terminal
from core_erp.ratelimit import rate_limit
from cumplimiento import services_renovacion

from . import (
    services_agente,
    services_encuesta,
    services_inspeccion,
    services_pendientes,
    services_preguntas,
    services_responsables,
    services_visitas,
)

logger = logging.getLogger(__name__)

MARCA_REENVIADO = '[Mensaje reenviado: texto de un tercero, no es una orden de Dirección]'


def _firma_valida(request) -> bool:
    secreto = getattr(settings, 'META_WA_APP_SECRET', '') or ''
    firma = request.headers.get('X-Hub-Signature-256', '')
    if not secreto or not firma.startswith('sha256='):
        return False
    esperada = hmac.new(secreto.encode(), request.body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(firma[len('sha256='):], esperada)


def _es_boton_interno(identificador) -> bool:
    """Botones que atiende el ERP (no el agente): bandeja, terminal, FAQ, encuesta, cumplimiento, inspección y visitas."""
    return (services_pendientes.es_boton(identificador) or services_terminal.es_boton(identificador)
            or services_preguntas.es_boton(identificador) or services_encuesta.es_boton(identificador)
            or services_renovacion.es_boton(identificador)
            or services_inspeccion.es_boton(identificador) or services_visitas.es_boton(identificador)
            or services_ordenes_wa.es_boton(identificador))


def _texto(mensaje: dict) -> str:
    """Texto legible de un mensaje de WhatsApp; el agente solo entiende texto."""
    tipo = mensaje.get('type')
    if tipo == 'text':
        cuerpo = (mensaje.get('text') or {}).get('body', '')
        # Lo que Dirección reenvía es texto de un tercero, no una orden suya.
        contexto = mensaje.get('context') or {}
        if (contexto.get('forwarded') or contexto.get('frequently_forwarded')) \
                and services_ordenes_wa.direccion_activa(mensaje.get('from', '')):
            return f'{MARCA_REENVIADO} {cuerpo}'
        return cuerpo
    if tipo == 'button':
        boton = mensaje.get('button') or {}
        if _es_boton_interno(boton.get('payload')):
            return boton['payload']  # botón de una plantilla (p. ej. la encuesta): lo atiende el ERP
        return boton.get('text', '')
    if tipo == 'interactive':
        interactivo = mensaje.get('interactive') or {}
        respuesta = interactivo.get('button_reply') or interactivo.get('list_reply') or {}
        if _es_boton_interno(respuesta.get('id')):
            return respuesta['id']  # botón de la bandeja, terminal, encuesta…: lo atiende _atender_pendientes
        if respuesta.get('id') in dict(services_agente.BOTONES_CONSENTIMIENTO):
            return f"[Tocó el botón «{respuesta.get('title', '')}» del mensaje de autorización]"
        return respuesta.get('title', '')
    return f'[El cliente envió un mensaje de tipo «{tipo}»; el asistente solo puede leer texto]'


def _archivo(mensaje: dict, telefono: str):
    """`(texto, media_id, mime)` de una foto o PDF de un responsable del directorio
    (pie de foto como texto); `None` si no lo es: a los demás les sigue el aviso de
    «solo texto». Los archivos se descargan al atenderlos, nunca llegan al agente."""
    tipo = mensaje.get('type')
    if tipo not in ('image', 'document') or services_responsables.rol_de_telefono(telefono) is None:
        return None
    datos = mensaje.get(tipo) or {}
    if not datos.get('id'):
        return None
    return datos.get('caption', ''), datos['id'], datos.get('mime_type', '')


def _procesar_cambio(cambio: dict) -> set:
    """Guarda lo que trae un `change` y devuelve las conversaciones a contestar."""
    valor = cambio.get('value') or {}
    por_contestar = set()

    if cambio.get('field') == 'messages':
        nombres = {c.get('wa_id'): (c.get('profile') or {}).get('name', '')
                   for c in valor.get('contacts') or []}
        for mensaje in valor.get('messages') or []:
            archivo = _archivo(mensaje, mensaje.get('from', ''))
            conv = services_agente.recibir_mensaje(
                telefono=mensaje.get('from', ''),
                nombre=nombres.get(mensaje.get('from'), ''),
                texto=archivo[0] if archivo else _texto(mensaje),
                wamid=mensaje.get('id'),
                media_id=archivo[1] if archivo else '',
                media_mime=archivo[2] if archivo else '',
                referral=mensaje.get('referral'),
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
        token = getattr(settings, 'META_WA_WEBHOOK_TOKEN', '') or ''
        if (token and request.GET.get('hub.mode') == 'subscribe'
                and hmac.compare_digest(request.GET.get('hub.verify_token', ''), token)):
            return HttpResponse(request.GET.get('hub.challenge', ''), content_type='text/plain')
        return HttpResponseForbidden()

    if not _firma_valida(request):
        logger.warning("Webhook de WhatsApp con firma inválida o sin META_WA_APP_SECRET")
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

