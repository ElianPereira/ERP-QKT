"""
Servicios de envío unificado de comunicaciones.
Cada función registra automáticamente en ComunicacionCliente.
"""
import logging
from typing import Optional

import requests
from decouple import config
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags

from .models import ComunicacionCliente

logger = logging.getLogger(__name__)


def enviar_email(
    *,
    cotizacion=None,
    pago=None,
    tipo: str,
    destinatario: str,
    asunto: str,
    template: str,
    context: dict,
    trigger: str = 'SIGNAL',
    adjuntos: Optional[list] = None,
) -> ComunicacionCliente:
    """
    Renderiza un template HTML, lo envía y registra la comunicación.

    Args:
        adjuntos: lista de tuplas (filename, content_bytes, mimetype)
    """
    comm = ComunicacionCliente.objects.create(
        cotizacion=cotizacion,
        pago=pago,
        canal='EMAIL',
        tipo=tipo,
        trigger=trigger,
        destinatario=destinatario,
        asunto=asunto,
        estado='PENDIENTE',
    )
    if not destinatario:
        comm.estado = 'FALLIDO'
        comm.error = 'Destinatario vacío'
        comm.save(update_fields=['estado', 'error'])
        return comm

    try:
        html = render_to_string(template, context)
        comm.cuerpo = html[:5000]
        msg = EmailMultiAlternatives(
            subject=asunto,
            body=strip_tags(html),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[destinatario],
        )
        msg.attach_alternative(html, 'text/html')
        for nombre, contenido, mime in (adjuntos or []):
            msg.attach(nombre, contenido, mime)
        msg.send(fail_silently=False)
        comm.estado = 'ENVIADO'
        comm.fecha_envio = timezone.now()
    except Exception as e:
        logger.exception("Error enviando email a %s: %s", destinatario, e)
        comm.estado = 'FALLIDO'
        comm.error = str(e)[:1000]
    comm.save()
    return comm


def enviar_whatsapp(
    *,
    cotizacion=None,
    pago=None,
    tipo: str,
    telefono: str,
    mensaje: str,
    trigger: str = 'SIGNAL',
) -> ComunicacionCliente:
    """
    Envía un WhatsApp vía la API de WhatsApp Cloud (Meta).
    Requiere WA_PHONE_ID y WA_TOKEN en settings/.env.
    Si no están configurados, registra como FALLIDO sin reventar.
    """
    comm = ComunicacionCliente.objects.create(
        cotizacion=cotizacion,
        pago=pago,
        canal='WHATSAPP',
        tipo=tipo,
        trigger=trigger,
        destinatario=telefono,
        asunto='',
        cuerpo=mensaje[:5000],
        estado='PENDIENTE',
    )
    phone_id = config('WA_PHONE_ID', default='')
    token = config('WA_TOKEN', default='')
    if not phone_id or not token or not telefono:
        comm.estado = 'FALLIDO'
        comm.error = 'WhatsApp no configurado o teléfono vacío'
        comm.save(update_fields=['estado', 'error'])
        return comm
    try:
        url = f'https://graph.facebook.com/v20.0/{phone_id}/messages'
        resp = requests.post(
            url,
            headers={'Authorization': f'Bearer {token}'},
            json={
                'messaging_product': 'whatsapp',
                'to': telefono,
                'type': 'text',
                'text': {'body': mensaje},
            },
            timeout=10,
        )
        if resp.status_code == 200:
            comm.estado = 'ENVIADO'
            data = resp.json()
            comm.proveedor_id = (data.get('messages') or [{}])[0].get('id', '')
        else:
            comm.estado = 'FALLIDO'
            comm.error = f"HTTP {resp.status_code}: {resp.text[:500]}"
    except Exception as e:
        logger.exception("Error WhatsApp: %s", e)
        comm.estado = 'FALLIDO'
        comm.error = str(e)[:1000]
    comm.save()
    return comm
