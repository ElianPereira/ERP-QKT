"""
Servicios de Facturación
=========================
Envío de solicitudes de factura al contador (PDF, email, WhatsApp) — un solo
lugar que sabe generar el PDF y hablar con los dos canales, compartido entre
los botones del admin, el auto-envío al registrar un pago (facturacion.signals)
y el cron de recordatorios (management command). Nunca lanza: cada función
reporta éxito/fracaso por canal para que el llamador decida qué hacer.
"""
import io
import logging
import os
from decimal import Decimal

import requests
from decouple import config
from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from weasyprint import HTML

from core_erp import impuestos

logger = logging.getLogger(__name__)


def get_usuario_sistema():
    """Usuario del sistema para envíos automáticos (auto-envío, recordatorios) —
    mismo patrón que contabilidad.signals.get_usuario_sistema, pero propio de
    este módulo para no acoplar facturación a contabilidad."""
    usuario, _ = User.objects.get_or_create(
        username='sistema_facturacion',
        defaults={'first_name': 'Sistema', 'last_name': 'Facturación', 'is_active': False},
    )
    return usuario


def generar_pdf_solicitud(solicitud):
    """Genera el PDF de la solicitud de factura y retorna los bytes."""
    cliente = solicitud.cliente

    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    if os.name == 'nt':
        logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}"
    else:
        logo_url = f"file://{ruta_logo}"

    total = Decimal(str(solicitud.monto))
    _d = impuestos.desglosar(
        total, con_retencion_isr=(getattr(cliente, 'tipo_persona', None) == 'MORAL'),
    )
    subtotal, iva, ret_isr = _d['base'], _d['iva'], _d['ret_isr']

    context = {
        'solicitud':    solicitud,
        'cliente':      cliente,
        'folio':        f"SOL-{int(solicitud.id):03d}",
        'logo_url':     logo_url,
        'calc_subtotal':subtotal,
        'calc_iva':     iva,
        'calc_ret_isr': ret_isr,
        'calc_total':   total,
    }
    html_string = render_to_string('facturacion/solicitud_pdf.html', context)
    return HTML(string=html_string).write_pdf()


def _enviar_pdf_whatsapp(pdf_bytes, filename, telefono, folio, cliente_nombre):
    """
    Envía el PDF de la solicitud al contador via WhatsApp Cloud API.
    1. Sube el PDF al Media API → obtiene media_id
    2. Envía el mensaje con ese media_id — como plantilla aprobada si
       WA_TEMPLATE_SOLICITUD_FACTURA está configurada, o como mensaje
       'document' directo si no (comportamiento de siempre).
    Retorna (True, '') o (False, 'mensaje de error').

    Nota: el mensaje 'document' directo no es una plantilla aprobada — fuera
    de la ventana de 24h de conversación de Meta, esto puede fallar. Igual
    que con la guía pre-evento, someter una plantilla es trabajo aparte
    (ver docs/whatsapp_plantilla_solicitud_factura.md); mientras no esté
    aprobada, el fallo queda registrado sin bloquear el email.
    """
    wa_token    = config('WA_CLOUD_API_TOKEN', default='')
    wa_phone_id = config('WA_PHONE_NUMBER_ID', default='')

    if not wa_token or not wa_phone_id:
        return False, "WA_CLOUD_API_TOKEN o WA_PHONE_NUMBER_ID no configurados."

    headers_auth = {"Authorization": f"Bearer {wa_token}"}

    # 1. Subir PDF
    try:
        resp_upload = requests.post(
            f"https://graph.facebook.com/v19.0/{wa_phone_id}/media",
            headers=headers_auth,
            files={
                'file':               (filename, io.BytesIO(pdf_bytes), 'application/pdf'),
                'messaging_product':  (None, 'whatsapp'),
                'type':               (None, 'application/pdf'),
            },
            timeout=30,
        )
    except Exception as e:
        return False, f"Error al subir PDF: {e}"

    if resp_upload.status_code != 200:
        return False, f"Error upload ({resp_upload.status_code}): {resp_upload.text[:200]}"

    media_id = resp_upload.json().get('id')
    if not media_id:
        return False, f"No se obtuvo media_id: {resp_upload.text[:200]}"

    template_name = getattr(settings, 'WA_TEMPLATE_SOLICITUD_FACTURA', '')
    if template_name:
        payload = _payload_plantilla_solicitud_factura(template_name, media_id, filename, folio, cliente_nombre)
    else:
        payload = {
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "document",
            "document": {
                "id": media_id,
                "filename": filename,
                "caption": f"Solicitud de Factura {folio} — {cliente_nombre}",
            },
        }
    payload["to"] = telefono

    # 2. Enviar mensaje
    try:
        resp_send = requests.post(
            f"https://graph.facebook.com/v19.0/{wa_phone_id}/messages",
            headers={**headers_auth, "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
    except Exception as e:
        return False, f"Error al enviar documento: {e}"

    if resp_send.status_code == 200:
        return True, ''
    return False, f"Error envío ({resp_send.status_code}): {resp_send.text[:200]}"


def _payload_plantilla_solicitud_factura(template_name, media_id, filename, folio, cliente_nombre):
    """
    Arma el payload de la plantilla aprobada para la solicitud de factura:
    cabecera tipo documento (el PDF adjunto) + cuerpo con dos variables de
    texto ({{1}}=folio, {{2}}=cliente). El texto exacto que hay que someter
    a Meta con estos mismos dos placeholders vive en
    docs/whatsapp_plantilla_solicitud_factura.md — si el texto real
    aprobado por Meta usa más o menos variables, este payload hay que
    ajustarlo para que coincida.
    """
    idioma = getattr(settings, 'WA_TEMPLATE_LANGUAGE', 'es_MX')
    return {
        "messaging_product": "whatsapp",
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": idioma},
            "components": [
                {
                    "type": "header",
                    "parameters": [
                        {"type": "document", "document": {"id": media_id, "filename": filename}},
                    ],
                },
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": folio},
                        {"type": "text", "text": cliente_nombre},
                    ],
                },
            ],
        },
    }


def enviar_solicitud_por_whatsapp(solicitud):
    """Genera el PDF y lo manda al contador por WhatsApp. Devuelve (ok, mensaje).
    No marca la solicitud como enviada — eso es responsabilidad del llamador."""
    from .models import ConfiguracionContador

    contador = ConfiguracionContador.get_activo()
    if not contador:
        return False, "No hay contador configurado."

    telefono = ''.join(filter(str.isdigit, contador.telefono_whatsapp or ''))
    if not telefono:
        return False, "El contador no tiene teléfono WhatsApp configurado."

    try:
        pdf_bytes = generar_pdf_solicitud(solicitud)
    except Exception as e:
        return False, f"Error al generar PDF: {e}"

    folio = f"SOL-{solicitud.id:04d}"
    filename = f"Solicitud_{folio}.pdf"
    return _enviar_pdf_whatsapp(pdf_bytes, filename, telefono, folio, solicitud.cliente.nombre)


def enviar_solicitud_por_email(solicitud):
    """Genera el PDF y lo manda al contador por correo. Devuelve (ok, mensaje)."""
    from .models import ConfiguracionContador

    contador = ConfiguracionContador.get_activo()
    if not contador:
        return False, "No hay contador configurado."

    try:
        pdf_bytes = generar_pdf_solicitud(solicitud)
        folio = f"SOL-{solicitud.id:04d}"
        email = EmailMessage(
            subject=f"Solicitud de Factura {folio} | {solicitud.cliente.nombre}",
            body=solicitud.get_datos_para_contador(),
            from_email=settings.EMAIL_FROM_NOTIFICACIONES,
            to=[contador.email],
        )
        email.attach(f"Solicitud_{folio}.pdf", pdf_bytes, 'application/pdf')
        email.send()
        return True, ''
    except Exception as e:
        return False, str(e)


def enviar_solicitud_al_contador(solicitud, usuario=None):
    """
    Manda la solicitud de factura al contador por los dos canales (email y
    WhatsApp) y marca la solicitud como ENVIADA si al menos uno tuvo éxito.
    Cada canal se intenta de forma independiente: que falle WhatsApp (ej.
    fuera de la ventana de 24h de Meta sin plantilla aprobada) no bloquea
    el email — mismo criterio que el resto del ERP (guía pre-evento,
    recordatorios de pago). Se usa para el primer envío (automático al
    registrar el pago, o manual desde los botones del admin); los
    recordatorios del cron reenvían sin volver a llamar aquí, para no
    reescribir fecha_envio/estado en cada recordatorio.
    """
    usuario = usuario or get_usuario_sistema()

    email_ok, email_error = enviar_solicitud_por_email(solicitud)
    wa_ok, wa_error = enviar_solicitud_por_whatsapp(solicitud)

    if email_ok or wa_ok:
        metodo = 'EMAIL' if email_ok else 'WHATSAPP'
        solicitud.marcar_enviada(usuario, metodo)

    if not email_ok:
        logger.warning("Solicitud de factura #%s: email al contador falló: %s", solicitud.pk, email_error)
    if not wa_ok:
        logger.warning("Solicitud de factura #%s: WhatsApp al contador falló: %s", solicitud.pk, wa_error)

    return email_ok, wa_ok
