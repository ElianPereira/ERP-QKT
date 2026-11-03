"""Código de verificación por WhatsApp para quien no tiene correo registrado.

El código nunca se guarda en claro ni en la bitácora de comunicaciones: el
registro lleva solo el motivo. Dentro de la ventana de 24 h sale como texto
(el cliente acaba de escribir, p. ej. por Kooxi); fuera de ella, con la
plantilla de autenticación `META_WA_PLANTILLA_CODIGO`.
"""
import logging

from django.conf import settings

from .formato_wa import bloques
from .services import _enviar_wa, normalizar_telefono_wa, reservar_comunicacion
from .services_pendientes import ventana_abierta

logger = logging.getLogger(__name__)


def telefono_valido(cliente) -> str:
    """Teléfono normalizado del cliente, o '' si no sirve para WhatsApp."""
    tel = normalizar_telefono_wa(getattr(cliente, 'telefono', '') or '')
    return tel if len(tel) >= 12 else ''


def enmascarar_telefono(telefono: str) -> str:
    digitos = ''.join(filter(str.isdigit, telefono or ''))
    return f'WhatsApp terminado en {digitos[-4:]}' if digitos else 'WhatsApp'


def puede_enviar_codigo_whatsapp(cliente) -> bool:
    return bool(telefono_valido(cliente))


def enviar_codigo_whatsapp(*, cotizacion, codigo: str, minutos: int, tipo: str = 'OTRO') -> bool:
    """Manda el código al WhatsApp del cliente. Devuelve True si Meta lo aceptó."""
    telefono = telefono_valido(cotizacion.cliente)
    if not telefono:
        return False
    plantilla = getattr(settings, 'META_WA_PLANTILLA_CODIGO', '')
    abierta = ventana_abierta(telefono)
    if not abierta and not plantilla:
        logger.warning("Código por WhatsApp sin ventana abierta y sin META_WA_PLANTILLA_CODIGO")
        return False
    comm = reservar_comunicacion(
        cotizacion=cotizacion, canal='WHATSAPP', tipo=tipo, trigger='MANUAL', destinatario=telefono,
        asunto='Código de verificación', estado='PENDIENTE',
        cuerpo='Código de verificación (no se guarda).',
    )
    if comm is None:
        return False
    if abierta:
        texto = bloques(
            f'Tu código de verificación es *{codigo}*.',
            f'Vence en {minutos} minutos. No lo compartas con nadie; si no lo pediste, ignora este mensaje.')
        payload = {'type': 'text', 'text': {'body': texto}}
    else:
        idioma = getattr(settings, 'META_WA_PLANTILLA_IDIOMA', 'es_MX')
        payload = {'type': 'template', 'template': {
            'name': plantilla, 'language': {'code': idioma},
            'components': [
                {'type': 'body', 'parameters': [{'type': 'text', 'text': codigo}]},
                {'type': 'button', 'sub_type': 'url', 'index': '0',
                 'parameters': [{'type': 'text', 'text': codigo}]},
            ]}}
    comm = _enviar_wa(comm, payload, telefono)
    return comm is not None and comm.estado != 'FALLIDO'
