"""
Servicios de envío unificado de comunicaciones.
Cada función registra automáticamente en ComunicacionCliente.

Esta capa es solo transporte (email + WhatsApp). La lógica de negocio —qué se
notifica, con qué contenido y con qué clave de idempotencia— vive en
`services_notificaciones.py`.
"""
import logging
import re
from typing import Optional

import requests
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags

from .models import ComunicacionCliente

logger = logging.getLogger(__name__)

# Segundos de espera por llamada externa. El cotizador público encadena varias
# dentro del mismo request, así que el presupuesto total importa.
WA_TIMEOUT = 8

# Códigos de error de la Graph API que conviene traducir en la auditoría: sin
# esto un fallo de WhatsApp queda como un HTTP 400 opaco imposible de diagnosticar.
WA_ERRORES_META = {
    100: 'Parámetro inválido o número no registrado en la cuenta',
    131021: 'El destinatario es el mismo número emisor',
    131026: 'El destinatario no tiene WhatsApp o no puede recibir el mensaje',
    131030: 'El destinatario no está en la lista de números permitidos',
    131047: 'Fuera de la ventana de 24 h: se requiere una plantilla aprobada',
    131056: 'Demasiados mensajes al mismo destinatario (límite de pares)',
    132000: 'Número de parámetros distinto al que espera la plantilla',
    132001: 'La plantilla no existe o no está aprobada en ese idioma',
    132005: 'Un parámetro excede el largo permitido por la plantilla',
    132012: 'Formato de parámetro inválido (revisa saltos de línea y espacios)',
    # Errores de registro del número emisor: el número existe en la WABA pero no
    # está dado de alta en la capa de mensajería de la Cloud API. Se resuelve
    # fijándole el PIN de verificación en dos pasos (POST /{phone_id}/register),
    # no tocando el código ni el token.
    133010: 'El número emisor no está registrado en la Cloud API: fíjale el PIN '
            'de verificación en dos pasos',
    133005: 'PIN de verificación en dos pasos incorrecto',
    133016: 'El número está bloqueado temporalmente por demasiados intentos de PIN',
    190: 'Token inválido o expirado (usa un token permanente de usuario del sistema)',
}

# Número emisor resuelto contra Meta, cacheado por proceso: es un dato fijo y
# resolverlo en cada cotización metería una llamada HTTP extra en el request.
_EMISOR_CACHE: dict = {}

# Remitente por tipo de correo (Issue #221). Cualquier tipo no listado —incluido
# 'OTRO', que son siempre alertas internas, nunca al cliente— cae a
# EMAIL_FROM_NOTIFICACIONES.
_REMITENTE_POR_TIPO = {
    'COTIZACION': 'EMAIL_FROM_RESERVAS',
    'CONFIRMACION_PAGO': 'EMAIL_FROM_PAGOS',
    'REEMBOLSO': 'EMAIL_FROM_PAGOS',
    'RECORDATORIO_PAGO': 'EMAIL_FROM_PAGOS',
}


def remitente_por_tipo(tipo: str) -> str:
    """Resuelve el `from_email` que le toca a un `tipo` de ComunicacionCliente."""
    setting = _REMITENTE_POR_TIPO.get(tipo, 'EMAIL_FROM_NOTIFICACIONES')
    return getattr(settings, setting)


# ─────────────────────────────── Utilidades ────────────────────────────────

def wa_graph_version() -> str:
    return getattr(settings, 'WA_GRAPH_VERSION', 'v20.0')


def normalizar_telefono_wa(telefono) -> str:
    """
    Normaliza un teléfono al formato que espera WhatsApp Cloud API: dígitos sin
    '+', con código de país. Devuelve '' si no se puede validar.

    Solo resuelve números mexicanos (`521` + 10 dígitos). Un número extranjero
    ya normalizado se devuelve tal cual si trae entre 11 y 15 dígitos.
    """
    if not telefono:
        return ''
    digitos = ''.join(filter(str.isdigit, str(telefono)))
    if not digitos:
        return ''

    # México: 521XXXXXXXXXX es la forma canónica para celulares.
    if digitos.startswith('521') and len(digitos) == 13:
        return digitos
    if digitos.startswith('52') and len(digitos) == 12:
        return '521' + digitos[2:]
    if len(digitos) == 10:
        return '521' + digitos

    # Otros países: se acepta lo que ya venga con código de país (E.164 sin '+').
    if 11 <= len(digitos) <= 15:
        return digitos
    return ''


def telefono_seguro(telefono) -> str:
    """Últimos 4 dígitos, para poder loguear sin exponer el número completo."""
    digitos = ''.join(filter(str.isdigit, str(telefono or '')))
    return f"…{digitos[-4:]}" if len(digitos) >= 4 else '…'


def texto_plano_wa(valor) -> str:
    """
    Aplana un valor para usarlo como parámetro de plantilla.

    Meta rechaza los parámetros que contienen saltos de línea, tabuladores o
    más de cuatro espacios seguidos, así que el texto se colapsa a una línea.
    """
    return re.sub(r'\s+', ' ', str(valor if valor is not None else '')).strip()


def _wa_credenciales():
    return (
        getattr(settings, 'WA_PHONE_NUMBER_ID', '') or '',
        getattr(settings, 'WA_CLOUD_API_TOKEN', '') or '',
    )


def numero_emisor_wa() -> str:
    """
    Número propio de la cuenta, normalizado, o '' si no se puede resolver.

    Sirve para no intentar un envío condenado a fallar con 131021 (WhatsApp no
    permite que emisor y destinatario coincidan). Los fallos no se cachean para
    que un problema puntual de red no deje la guarda desactivada para siempre.
    """
    phone_id, token = _wa_credenciales()
    if not phone_id or not token:
        return ''
    if phone_id in _EMISOR_CACHE:
        return _EMISOR_CACHE[phone_id]
    try:
        resp = requests.get(
            f'https://graph.facebook.com/{wa_graph_version()}/{phone_id}',
            params={'fields': 'display_phone_number'},
            headers={'Authorization': f'Bearer {token}'},
            timeout=WA_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning("No se pudo resolver el número emisor: HTTP %s", resp.status_code)
            return ''
        numero = normalizar_telefono_wa((resp.json() or {}).get('display_phone_number', ''))
    except Exception:
        logger.warning("No se pudo resolver el número emisor de WhatsApp", exc_info=True)
        return ''
    _EMISOR_CACHE[phone_id] = numero
    return numero


def _describir_error_meta(resp) -> str:
    """Convierte la respuesta de error de Meta en una línea auditable."""
    try:
        error = (resp.json() or {}).get('error') or {}
    except ValueError:
        error = {}
    partes = [f"HTTP {resp.status_code}"]
    codigo = error.get('code')
    if codigo is not None:
        partes.append(f"Meta {codigo}")
        traduccion = WA_ERRORES_META.get(codigo)
        if traduccion:
            partes.append(traduccion)
    mensaje = error.get('message') or ''
    if mensaje:
        partes.append(mensaje)
    trace = error.get('fbtrace_id')
    if trace:
        partes.append(f"fbtrace_id={trace}")
    if not error:
        partes.append(resp.text[:300])
    return ' · '.join(str(p) for p in partes)[:1000]


def reservar_comunicacion(**campos) -> Optional[ComunicacionCliente]:
    """
    Crea el registro de la comunicación reservando su clave de idempotencia.

    La reserva es el propio INSERT: si otro proceso ya escribió esa clave, el
    índice único lanza IntegrityError y aquí se traduce a "ya se envió". El
    `atomic()` no es opcional — en PostgreSQL un IntegrityError sin savepoint
    aborta la transacción envolvente y se llevaría por delante el request
    completo (en SQLite no se reproduce, por eso hay un test dedicado).
    """
    clave = campos.get('clave_idempotencia')
    try:
        with transaction.atomic():
            return ComunicacionCliente.objects.create(**campos)
    except IntegrityError:
        if clave:
            logger.info("Comunicación duplicada omitida (clave=%s)", clave)
            return None
        raise


# ───────────────────────────────── Email ───────────────────────────────────

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
    clave_idempotencia: Optional[str] = None,
) -> Optional[ComunicacionCliente]:
    """
    Renderiza un template HTML, lo envía y registra la comunicación.

    Devuelve None si la clave de idempotencia ya estaba tomada.

    Args:
        adjuntos: lista de tuplas (filename, content_bytes, mimetype)
    """
    comm = reservar_comunicacion(
        cotizacion=cotizacion,
        pago=pago,
        canal='EMAIL',
        tipo=tipo,
        trigger=trigger,
        destinatario=destinatario,
        asunto=asunto,
        estado='PENDIENTE',
        clave_idempotencia=clave_idempotencia,
    )
    if comm is None:
        return None
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
            from_email=remitente_por_tipo(tipo),
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


def alertar_equipo_fecha_chocada(cotizacion, mensaje: str) -> None:
    """Notifica internamente al equipo cuando entra una cotización con fecha ocupada."""
    destinatarios = getattr(settings, 'ALERTAS_INTERNAS_EMAIL', None) or [
        getattr(settings, 'DEFAULT_FROM_EMAIL', 'alertas@qkt.mx')
    ]
    asunto = f"⚠️ Cotización con fecha chocada — COT-{cotizacion.id:03d}"
    cuerpo = (
        f"La cotización COT-{cotizacion.id:03d} fue creada con una fecha ocupada.\n\n"
        f"Cliente: {cotizacion.cliente}\n"
        f"Fecha evento: {cotizacion.fecha_evento}\n"
        f"Detalle: {mensaje}\n\n"
        f"Revisa el admin y contacta al cliente para confirmar alternativas."
    )
    try:
        from django.core.mail import send_mail
        send_mail(
            asunto, cuerpo,
            settings.EMAIL_FROM_NOTIFICACIONES,
            destinatarios,
            fail_silently=True,
        )
        ComunicacionCliente.objects.create(
            cotizacion=cotizacion,
            canal='EMAIL',
            tipo='OTRO',
            trigger='SIGNAL',
            destinatario=', '.join(destinatarios),
            asunto=asunto,
            cuerpo=cuerpo[:5000],
            estado='ENVIADO',
            fecha_envio=timezone.now(),
        )
    except Exception as e:
        logger.exception("Error alertando equipo: %s", e)


def alertar_equipo_email(cotizacion, *, asunto: str, cuerpo: str,
                         clave_idempotencia: Optional[str] = None) -> Optional[ComunicacionCliente]:
    """Copia por correo de una alerta interna, a los mismos destinatarios del equipo."""
    destinatarios = getattr(settings, 'ALERTAS_INTERNAS_EMAIL', None) or [
        getattr(settings, 'DEFAULT_FROM_EMAIL', 'alertas@qkt.mx')
    ]
    comm = reservar_comunicacion(
        cotizacion=cotizacion,
        canal='EMAIL',
        tipo='OTRO',
        trigger='SIGNAL',
        destinatario=', '.join(destinatarios),
        asunto=asunto,
        cuerpo=cuerpo[:5000],
        estado='PENDIENTE',
        clave_idempotencia=clave_idempotencia,
    )
    if comm is None:
        return None
    try:
        from django.core.mail import send_mail
        send_mail(asunto, cuerpo, settings.EMAIL_FROM_NOTIFICACIONES, destinatarios, fail_silently=False)
        comm.estado = 'ENVIADO'
        comm.fecha_envio = timezone.now()
    except Exception as e:
        logger.exception("Error enviando alerta interna por email: %s", e)
        comm.estado = 'FALLIDO'
        comm.error = str(e)[:1000]
    comm.save()
    return comm


# ──────────────────────────────── WhatsApp ─────────────────────────────────

def _enviar_wa(comm: ComunicacionCliente, payload: dict, destino: str) -> ComunicacionCliente:
    """
    Ejecuta el POST a la Graph API y deja el resultado auditado en `comm`.
    Único punto del proyecto que habla con `/messages`.
    """
    phone_id, token = _wa_credenciales()
    if not phone_id or not token:
        comm.estado = 'FALLIDO'
        comm.error = 'WhatsApp no configurado (falta WA_PHONE_NUMBER_ID o WA_CLOUD_API_TOKEN)'
        comm.save(update_fields=['estado', 'error'])
        return comm
    if not destino:
        comm.estado = 'FALLIDO'
        comm.error = 'Teléfono vacío o con formato no reconocido'
        comm.save(update_fields=['estado', 'error'])
        return comm

    emisor = numero_emisor_wa()
    if emisor and emisor == destino:
        comm.estado = 'FALLIDO'
        comm.error = 'El destinatario coincide con el número emisor (Meta 131021)'
        comm.save(update_fields=['estado', 'error'])
        logger.error(
            "WhatsApp no enviado: el destino %s es el mismo número emisor; "
            "WhatsApp no permite enviarse a sí mismo",
            telefono_seguro(destino),
        )
        return comm

    try:
        resp = requests.post(
            f'https://graph.facebook.com/{wa_graph_version()}/{phone_id}/messages',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            json={'messaging_product': 'whatsapp', 'to': destino, **payload},
            timeout=WA_TIMEOUT,
        )
        if resp.status_code == 200:
            comm.estado = 'ENVIADO'
            comm.fecha_envio = timezone.now()
            data = resp.json()
            comm.proveedor_id = (data.get('messages') or [{}])[0].get('id', '')
        else:
            comm.estado = 'FALLIDO'
            comm.error = _describir_error_meta(resp)
            logger.warning(
                "WhatsApp fallido hacia %s: %s", telefono_seguro(destino), comm.error
            )
    except Exception as e:
        logger.exception("Error WhatsApp hacia %s", telefono_seguro(destino))
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
    clave_idempotencia: Optional[str] = None,
) -> Optional[ComunicacionCliente]:
    """
    Envía un WhatsApp de texto libre vía la API de WhatsApp Cloud (Meta).

    Solo llega si la ventana de servicio de 24 h está abierta (el destinatario
    escribió recientemente). Para mensajes iniciados por el negocio usar
    `enviar_whatsapp_template`.
    """
    destino = normalizar_telefono_wa(telefono)
    comm = reservar_comunicacion(
        cotizacion=cotizacion,
        pago=pago,
        canal='WHATSAPP',
        tipo=tipo,
        trigger=trigger,
        destinatario=destino or str(telefono or ''),
        asunto='',
        cuerpo=mensaje[:5000],
        estado='PENDIENTE',
        clave_idempotencia=clave_idempotencia,
    )
    if comm is None:
        return None
    return _enviar_wa(comm, {'type': 'text', 'text': {'body': mensaje}}, destino)


def enviar_whatsapp_template(
    *,
    cotizacion=None,
    pago=None,
    tipo: str,
    telefono: str,
    template_name: str,
    parametros: Optional[list] = None,
    language_code: Optional[str] = None,
    components: Optional[list] = None,
    trigger: str = 'SIGNAL',
    clave_idempotencia: Optional[str] = None,
) -> Optional[ComunicacionCliente]:
    """
    Envía una plantilla aprobada en Meta. Es la única vía válida para mensajes
    iniciados por el negocio fuera de la ventana de 24 h.

    `parametros` es la forma corta: una lista de valores que se convierten en
    los `{{1}}, {{2}}, …` del cuerpo, aplanados con `texto_plano_wa` porque Meta
    rechaza saltos de línea dentro de un parámetro. `components` permite pasar
    la estructura completa cuando hace falta (cabecera, botones, etc.).
    """
    idioma = language_code or getattr(settings, 'WA_TEMPLATE_LANGUAGE', 'es_MX')
    destino = normalizar_telefono_wa(telefono)
    valores = [texto_plano_wa(v) for v in (parametros or [])]

    if components is None and valores:
        components = [{
            'type': 'body',
            'parameters': [{'type': 'text', 'text': v} for v in valores],
        }]

    cuerpo_auditado = f"[{template_name}/{idioma}] " + ' | '.join(valores)
    comm = reservar_comunicacion(
        cotizacion=cotizacion,
        pago=pago,
        canal='WHATSAPP',
        tipo=tipo,
        trigger=trigger,
        destinatario=destino or str(telefono or ''),
        asunto=template_name,
        cuerpo=cuerpo_auditado[:5000],
        estado='PENDIENTE',
        clave_idempotencia=clave_idempotencia,
    )
    if comm is None:
        return None

    if not template_name:
        comm.estado = 'FALLIDO'
        comm.error = 'Plantilla de WhatsApp no configurada'
        comm.save(update_fields=['estado', 'error'])
        return comm

    plantilla = {'name': template_name, 'language': {'code': idioma}}
    if components:
        plantilla['components'] = components
    return _enviar_wa(comm, {'type': 'template', 'template': plantilla}, destino)
