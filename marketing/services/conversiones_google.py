"""Conversiones offline hacia Google Ads por clic (Issue #468, fase 8).

Misma bandeja que la Conversions API de Meta (`EventoConversion`, `plataforma`=GOOGLE): mismo criterio de
consentimiento MARKETING y de baja (se evalúa al ENVIAR), mismos reintentos con espera creciente y el mismo
programador (`enviar_eventos_conversion`). Solo se suben dos eventos:

- ``Lead``: la solicitud del cotizador web llegó con un clic de Google Ads (gclid, o gbraid/wbraid de iOS).
- ``Purchase``: la venta se confirmó; valor = cobrado sin IVA neto de reembolsos, MXN (`kpis.cobrado_sin_iva`).

Cada evento necesita su acción de conversión en Google Ads (``GOOGLE_ADS_CONVERSION_LEAD`` / ``..._COMPRA``);
sin ella ese evento no se encola. El clic vive en el prospecto (``Lead.gclid/gbraid/wbraid``) y se lee al enviar,
no se copia a la bandeja; al anonimizar al prospecto se borra y lo pendiente falla sin enviarse.

Envío: POST ``customers/{id}:uploadClickConversions`` con ``partialFailure``; ``orderId`` = ``event_id`` para que
un reintento no cuente doble. El valor se escribe exacto desde el Decimal (nunca float). Los tokens no salen en
mensajes ni en logs, y el clic nunca se loguea.

Verificación: NO se pudo contrastar con la documentación vigente (el contenedor no alcanza developers.google.com).
Por confirmar con una conversión de prueba antes de producción: los nombres de los códigos de error de la carga
(``TRANSITORIOS`` y ``YA_EXISTE``) y la versión de la API.
"""
import json
import logging
import re
from datetime import timezone as dt_timezone

import requests
from django.conf import settings

from ..models import EventoConversion, Lead
from . import conversiones
from .proveedores import ProveedorError, TokenVencidoError, proveedor_google

logger = logging.getLogger(__name__)

TIMEOUT = 20
MARCADOR_VALOR = '@@VALOR@@'
RE_CLIC = re.compile(r'^[A-Za-z0-9_-]{10,300}$')
# Errores de la carga que se reintentan solos (la acción de conversión recién creada tarda horas en aceptar cargas).
TRANSITORIOS = {'TOO_RECENT_CONVERSION_ACTION', 'TOO_RECENT_CLICK', 'INTERNAL_ERROR', 'TRANSIENT_ERROR',
                'RESOURCE_EXHAUSTED', 'DEADLINE_EXCEEDED', 'UNAVAILABLE'}
# Ya estaba cargada con ese orderId: el reintento tuvo éxito antes, es un éxito idempotente.
YA_EXISTE = {'DUPLICATE_ORDER_ID', 'CONVERSION_ALREADY_EXISTS'}


def accion_de(nombre_evento: str) -> str:
    """ID de la acción de conversión de Google Ads para el evento ('' si no se subirá)."""
    ajuste = {EventoConversion.LEAD: settings.GOOGLE_ADS_CONVERSION_LEAD,
              EventoConversion.PURCHASE: settings.GOOGLE_ADS_CONVERSION_COMPRA}
    return ''.join(c for c in (ajuste.get(nombre_evento) or '') if c.isdigit())


def configurado() -> bool:
    return proveedor_google().configurado() and bool(accion_de(EventoConversion.LEAD)
                                                      or accion_de(EventoConversion.PURCHASE))


def identificador_de_clic(lead) -> tuple:
    """(campo, valor) del clic del prospecto; Google admite uno solo por conversión. ('', '') si no hay."""
    for campo in ('gclid', 'gbraid', 'wbraid'):
        valor = getattr(lead, campo)
        if valor:
            return campo, valor
    return '', ''


# --- Encolado -----------------------------------------------------------------

def _encolar(lead, nombre, *, valor=None):
    if not configurado() or not accion_de(nombre) or not identificador_de_clic(lead)[0]:
        return None
    if lead.anonimizado_en:
        return None
    return conversiones._crear(lead, nombre, {'origen': EventoConversion.WEB}, valor=valor, plataforma=EventoConversion.GOOGLE)


def encolar_etapa(lead_id, etapa):
    """El prospecto llegó a `etapa`: solo GANADO genera una compra en Google."""
    if etapa != Lead.GANADO:
        return None
    from .kpis import cobrado_sin_iva
    lead = Lead.objects.select_related('cotizacion').get(pk=lead_id)
    if lead.cotizacion is None:
        return None
    valor = cobrado_sin_iva(lead.cotizacion)
    if valor <= 0:
        return None
    return _encolar(lead, EventoConversion.PURCHASE, valor=valor)


def registrar_clic_web(*, cotizacion, gclid='', gbraid='', wbraid=''):
    """La solicitud del cotizador web trae el clic de Google Ads: se guarda en el prospecto, la fuente pasa a
    GOOGLE_ANUNCIO si no tenía una mejor y se encola el Lead. Nunca lanza."""
    try:
        clics = {k: v for k, v in (('gclid', gclid), ('gbraid', gbraid), ('wbraid', wbraid)) if RE_CLIC.match(v or '')}
        if not clics:
            return None
        from . import pipeline
        cliente = cotizacion.cliente
        lead = pipeline.leads_de(cliente.telefono, cliente).order_by('-fecha_primer_contacto').first()
        if lead is None or lead.anonimizado_en:
            return None
        cambios = [k for k, v in clics.items() if not getattr(lead, k)]
        for k in cambios:
            setattr(lead, k, clics[k])
        anterior = lead.fuente
        if lead.fuente in (*Lead.FUENTES_GENERICAS, 'GOOGLE'):
            lead.fuente = 'GOOGLE_ANUNCIO'
        if cambios or lead.fuente != anterior:
            lead.save(update_fields=[*cambios, 'fuente', 'updated_at'])
        if lead.fuente != anterior:
            pipeline._evento(lead, 'FUENTE', detalle={'antes': anterior, 'despues': lead.fuente})
        _encolar(lead, EventoConversion.LEAD)
        if lead.etapa == Lead.GANADO:
            encolar_etapa(lead.pk, Lead.GANADO)
    except Exception:
        logger.exception('Marketing: no se pudo registrar el clic de Google Ads de una solicitud')
    return None


# --- Envío --------------------------------------------------------------------

def construir_conversion(ev, proveedor=None) -> dict:
    """Un elemento de ``conversions``. El valor va como marcador (ver `_cuerpo`)."""
    proveedor = proveedor or proveedor_google()
    campo, valor_clic = identificador_de_clic(ev.lead)
    conversion = {
        'conversionAction': f'customers/{proveedor.customer_id}/conversionActions/{accion_de(ev.nombre_evento)}',
        'conversionDateTime': ev.event_time.astimezone(dt_timezone.utc).strftime('%Y-%m-%d %H:%M:%S+00:00'),
        'orderId': str(ev.event_id),
        campo: valor_clic,
    }
    if ev.valor is not None:
        conversion.update(conversionValue=MARCADOR_VALOR, currencyCode=ev.moneda)
    return conversion


def _cuerpo(ev, proveedor) -> str:
    texto = json.dumps({'conversions': [construir_conversion(ev, proveedor)], 'partialFailure': True,
                        'validateOnly': False})
    if ev.valor is not None:
        texto = texto.replace(json.dumps(MARCADOR_VALOR), format(ev.valor, 'f'))
    return texto


def _codigos(error: dict) -> set:
    """Códigos (valores de ``errorCode``) dentro de un ``partialFailureError``."""
    codigos = set()
    for detalle in error.get('details', []) or []:
        for e in detalle.get('errors', []) or []:
            codigos.update(str(v) for v in (e.get('errorCode') or {}).values())
    return codigos


def enviar(ev, proveedor=None):
    """(ok, transitorio, error). El error nunca lleva tokens, el clic ni datos del cliente."""
    proveedor = proveedor or proveedor_google()
    if not identificador_de_clic(ev.lead)[0]:
        return False, False, 'El prospecto ya no tiene el clic de Google Ads'
    if not accion_de(ev.nombre_evento):
        return False, False, 'Falta la acción de conversión en las variables GOOGLE_ADS_CONVERSION_*'
    try:
        r = requests.post(
            f'{proveedor.HOST}/{proveedor.version}/customers/{proveedor.customer_id}:uploadClickConversions',
            headers=proveedor.cabeceras(), data=_cuerpo(ev, proveedor).encode('utf-8'), timeout=TIMEOUT)
    except TokenVencidoError as exc:
        return False, False, str(exc)[:300]
    except ProveedorError as exc:
        return False, True, str(exc)[:300]
    except requests.RequestException as exc:
        return False, True, f'Sin respuesta de Google Ads ({type(exc).__name__})'
    try:
        cuerpo = r.json()
    except ValueError:
        cuerpo = {}
    if isinstance(cuerpo, list):
        cuerpo = cuerpo[0] if cuerpo else {}
    if r.status_code == 401:
        return False, False, 'Google Ads rechazó la credencial (401)'
    if r.status_code != 200:
        estado = (cuerpo.get('error') or {}).get('status', '')
        transitorio = r.status_code >= 500 or r.status_code == 429 or estado in TRANSITORIOS
        return False, transitorio, f'HTTP {r.status_code} {estado}'[:300]
    parcial = cuerpo.get('partialFailureError')
    if not parcial:
        return True, False, ''
    codigos = _codigos(parcial)
    if codigos & YA_EXISTE:
        return True, False, ''
    detalle = ', '.join(sorted(codigos)) or str(parcial.get('code', 'sin detalle'))
    return False, bool(codigos & TRANSITORIOS), f'Google Ads rechazó la carga: {detalle}'[:300]
