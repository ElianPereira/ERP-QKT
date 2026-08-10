"""Utilidades compartidas por los tests de comunicacion."""
import json

from django.test import override_settings

from comunicacion import services

# Números ficticios: el rango 555 está reservado y nunca corresponde a una
# persona real. Nunca poner aquí un teléfono del negocio ni de un cliente.
TEL_CLIENTE = '5215555550001'
TEL_NEGOCIO = '5215555550002'
TEL_EMISOR = '5215555550003'

WA_SETTINGS = dict(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='test@qkt.mx',
    COMUNICACION_SIGNALS_ENABLED=True,
    PORTAL_URL='https://portal.test',
    WA_PHONE_NUMBER_ID='PHONE_ID_TEST',
    WA_CLOUD_API_TOKEN='TOKEN_TEST',
    WA_GRAPH_VERSION='v20.0',
    WA_NUMERO_NEGOCIO=TEL_NEGOCIO,
    WA_NUMERO_CONTACTO_PUBLICO=TEL_NEGOCIO,
    WA_TEMPLATE_LANGUAGE='es_MX',
    WA_TEMPLATE_COTIZACION='qkt_cotizacion_lista',
    WA_TEMPLATE_PAGO='qkt_pago_recibido',
    WA_TEMPLATE_RECORDATORIO='qkt_recordatorio_pago',
    WA_TEMPLATE_ALERTA_INTERNA='',
)


def wa_settings(**extra):
    """`override_settings` con la configuración de WhatsApp completa."""
    return override_settings(**{**WA_SETTINGS, **extra})


class RespuestaFalsa:
    """Imita lo justo de `requests.Response` que consume `services`."""

    def __init__(self, status_code=200, payload=None, texto=None):
        self.status_code = status_code
        if payload is None and status_code == 200:
            payload = {'messages': [{'id': 'wamid.TEST'}]}
        self._payload = payload
        self.text = texto if texto is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError('respuesta sin JSON')
        return self._payload


def error_meta(codigo, mensaje='error simulado', status_code=400):
    return RespuestaFalsa(
        status_code=status_code,
        payload={'error': {
            'code': codigo,
            'message': mensaje,
            'fbtrace_id': 'TRACE_TEST',
        }},
    )


def limpiar_cache_emisor():
    services._EMISOR_CACHE.clear()
