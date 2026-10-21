"""Utilidades compartidas por los tests de comunicacion."""
import json

from django.test import override_settings

from comunicacion import services

# Números ficticios: el rango 555 está reservado y nunca corresponde a una
# persona real. Nunca poner aquí un teléfono del negocio ni de un cliente.
TEL_CLIENTE = '525555550001'
TEL_NEGOCIO = '525555550002'
TEL_EMISOR = '525555550003'

WA_SETTINGS = dict(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='test@qkt.mx',
    COMUNICACION_SIGNALS_ENABLED=True,
    ERP_PORTAL_URL='https://portal.test',
    META_WA_KOOXI_ID='PHONE_ID_TEST',
    META_WA_KOOXI_TOKEN='TOKEN_TEST',
    META_WA_API_VERSION='v20.0',
    ERP_WA_NEGOCIO_NUMERO=TEL_NEGOCIO,
    META_WA_KOOXI_NUMERO=TEL_NEGOCIO,
    META_WA_PLANTILLA_IDIOMA='es_MX',
    META_WA_PLANTILLA_COTIZACION='qkt_cotizacion_lista',
    META_WA_PLANTILLA_PAGO='qkt_pago_recibido',
    META_WA_PLANTILLA_RECORDATORIO='qkt_recordatorio_pago',
    META_WA_PLANTILLA_ALERTA_INTERNA='',
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
