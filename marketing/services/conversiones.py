"""Conversions API de Meta: bandeja de salida de eventos (Issue #468, fase 6).

Qué se manda
------------
Cuando un prospecto con atribución publicitaria avanza, se encola (en
`transaction.on_commit`, nunca dentro de la venta) un `EventoConversion`:

- ``Lead``: el prospecto nació de un anuncio (clic a WhatsApp) o de una solicitud
  del cotizador web (mismo ``event_id`` que el Pixel del navegador, para que Meta
  deduplique).
- ``CotizacionEnviada`` (evento personalizado): el prospecto llegó a COTIZADO.
- ``Schedule``: agendó una visita.
- ``Purchase``: la venta se confirmó. El valor es lo cobrado sin IVA, neto de
  reembolsos, en MXN, al momento de confirmarse (el anticipo), de
  `kpis.cobrado_sin_iva`. Si se cae la venta antes de enviarse, se cancela.

Sin atribución (ni clic de anuncio ni solicitud web con el banner de cookies
aceptado o no) no se encola nada: no hay a qué anuncio acreditarlo.

Consentimiento
--------------
El envío exige consentimiento MARKETING vigente (la aceptación más reciente de
`AceptacionLegal` del cliente, o el botón de promociones de la conversación de
WhatsApp) **dado desde `MEDICION_DESDE`** (Aviso v2.7: antes la casilla solo cubría promociones) y que no haya `BajaWhatsApp` sin una aceptación posterior. Se evalúa al
ENVIAR, no al encolar: si falta, el evento queda OMITIDO_SIN_CONSENTIMIENTO.

Datos personales
----------------
La bandeja no guarda teléfono ni correo: los lee del prospecto al enviar y salen
con SHA-256 (``ph``: dígitos con código de país sin «+»; ``em``: minúsculas y sin
espacios). La IP y el agente de usuario del navegador (que Meta exige para
eventos web) viven en ``contexto_web`` solo hasta que el evento llega a un estado
terminal. `limpiar_atribucion` borra además los identificadores al anonimizar.

Envío y reintentos
------------------
`enviar_pendientes` (programador, cada 15 min) reclama cada fila con un UPDATE
condicional, envía y: éxito -> ENVIADO; fallo transitorio (red, 5xx, límite de
tasa) -> reintento con espera de 15 min x 2^(n-1) hasta ``MAX_INTENTOS``;
rechazo definitivo (token, parámetros) o evento de más de 7 días -> FALLIDO. Con
``UMBRAL_FALLIDOS`` fallidos en 7 días se abre un `Pendiente` a Dirección. Un
reintento repite el ``event_id``, así que Meta no cuenta doble. Apagado si falta
`META_CAPI_TOKEN` o dataset: ni se encola ni se envía. Desde la fase 8 la misma bandeja lleva las
conversiones offline de Google (`plataforma`=GOOGLE, ver `conversiones_google.py`), con su propia ventana de 90 días.

Verificación contra la documentación de Meta (2026-10-31)
---------------------------------------------------------
NO se pudo verificar: el contenedor de esta sesión no resuelve
developers.facebook.com. La forma del payload sale de lo conocido de la
Conversions API (POST /{version}/{dataset}/events con ``data`` y el token en el
cuerpo, nunca en la URL; ``event_id`` para deduplicar con el Pixel; ``fbp``/``fbc``,
``client_ip_address`` y ``client_user_agent`` para ``action_source=website``) y de
la variante de mensajería (``action_source=business_messaging``,
``messaging_channel=whatsapp``, ``user_data.whatsapp_business_account_id`` y
``ctwa_clid``). Quedan por confirmar contra la documentación vigente y con
«Probar eventos» (``META_CAPI_TEST_CODE``) antes de producción: que el evento
personalizado ``CotizacionEnviada`` se acepte en el dataset de mensajería (si no,
mapearlo a un estándar), el ``action_source`` de los eventos web posteriores al
Lead (aquí ``system_generated``) y la versión de la API.
"""
import contextlib
import json
import logging
from contextvars import ContextVar
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from hashlib import sha256

import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from comunicacion.services import normalizar_telefono_wa

from ..models import EventoConversion, Lead

logger = logging.getLogger(__name__)

GRAPH = 'https://graph.facebook.com'
TIMEOUT = 20
MAX_INTENTOS = 6
ESPERA_BASE = timedelta(minutes=15)
RECLAMO = timedelta(minutes=10)
VENTANA_META = timedelta(days=7)
VENTANA_GOOGLE = timedelta(days=90)  # Google acepta la importación hasta 90 días después del clic
UMBRAL_FALLIDOS = 3
LOTE = 200
CODIGOS_TRANSITORIOS = {1, 2, 4, 17, 32, 341, 613}
MARCADOR_VALOR = '@@VALOR@@'

_pausado = ContextVar('marketing_conversiones_pausado', default=False)

EVENTO_POR_ETAPA = {
    Lead.VISITA_AGENDADA: EventoConversion.SCHEDULE,
    Lead.COTIZADO: EventoConversion.COTIZACION_ENVIADA,
    Lead.GANADO: EventoConversion.PURCHASE,
}


@contextlib.contextmanager
def pausa():
    """Dentro del bloque no se encola nada (reconstrucción del histórico: sus
    fechas son viejas y Meta rechaza eventos de más de 7 días)."""
    token = _pausado.set(True)
    try:
        yield
    finally:
        _pausado.reset(token)


def dataset_id() -> str:
    return (settings.META_CAPI_DATASET_ID or settings.META_PIXEL_ID or '').strip()


def configurado() -> bool:
    return bool(settings.META_CAPI_TOKEN and dataset_id())


# --- Datos de usuario ---------------------------------------------------------

def _sha256(texto: str) -> str:
    return sha256(texto.encode('utf-8')).hexdigest() if texto else ''


def hash_telefono(telefono) -> str:
    """SHA-256 del teléfono en E.164 sin «+» (México: 52 + 10 dígitos). '' si no es válido."""
    return _sha256(normalizar_telefono_wa(telefono))


def hash_correo(correo) -> str:
    """SHA-256 del correo en minúsculas y sin espacios. '' si no parece un correo."""
    correo = (correo or '').strip().lower()
    return _sha256(correo) if '@' in correo else ''


# Desde el Aviso de Privacidad v2.7 (00:00 de Mérida del 06/11/2026) la casilla MARKETING menciona la medición
# en Meta y Google. Una aceptación anterior solo cubría promociones: no autoriza enviar eventos.
MEDICION_DESDE = datetime(2026, 11, 6, 6, 0, tzinfo=dt_timezone.utc)


def tiene_consentimiento(lead) -> bool:
    """¿Hay MARKETING aceptado con el texto que cubre la medición y ninguna baja sin una aceptación posterior?"""
    from comunicacion.services_bajas import dado_de_baja
    from legal.models import AceptacionLegal

    if lead.anonimizado_en:
        return False
    cliente = lead.cliente
    telefono = lead.telefono or (cliente.telefono if cliente else '')
    if dado_de_baja(telefono, cliente):
        return False
    if cliente is not None:
        ultima = (AceptacionLegal.objects.filter(cliente=cliente).order_by('-aceptado_en')
                  .values_list('finalidades_aceptadas', 'aceptado_en').first())
        if ultima and 'MARKETING' in (ultima[0] or []) and ultima[1] >= MEDICION_DESDE:
            return True
    conv = lead.conversacion
    return bool(conv is not None and conv.consentimiento_marketing and conv.consentimiento_en
                and conv.consentimiento_en >= MEDICION_DESDE)


# --- Encolado -----------------------------------------------------------------

def _crear(lead, nombre, atribucion, *, valor=None, event_id=None, plataforma=EventoConversion.META):
    campos = {'plataforma': plataforma}
    if event_id is not None:
        campos['event_id'] = event_id
    try:
        with transaction.atomic():
            return EventoConversion.objects.create(
                lead=lead, nombre_evento=nombre, origen=atribucion['origen'], valor=valor,
                ctwa_clid=atribucion.get('ctwa_clid', ''), fbp=atribucion.get('fbp', ''),
                fbc=atribucion.get('fbc', ''), contexto_web=atribucion.get('contexto_web') or {},
                event_time=timezone.now(), **campos)
    except IntegrityError:
        return None  # ya existe ese evento para el prospecto (o ese event_id)


def _atribucion(lead):
    """De dónde acreditar los eventos del prospecto, o None si no hay a quién."""
    if lead.anuncio_clid and settings.META_WA_WABA_ID:
        return {'origen': EventoConversion.WHATSAPP, 'ctwa_clid': lead.anuncio_clid}
    web = lead.conversiones.filter(plataforma=EventoConversion.META, origen=EventoConversion.WEB,
                                   nombre_evento=EventoConversion.LEAD).first()
    if web is not None:
        return {'origen': EventoConversion.WEB, 'fbp': web.fbp, 'fbc': web.fbc}
    return None


def _encolar_etapa(lead_id, etapa):
    nombre = EVENTO_POR_ETAPA.get(etapa)
    if nombre is None or not configurado():
        return None
    lead = Lead.objects.select_related('cotizacion').get(pk=lead_id)
    atribucion = _atribucion(lead)
    if atribucion is None:
        return None
    valor = None
    if nombre == EventoConversion.PURCHASE:
        from .kpis import cobrado_sin_iva
        if lead.cotizacion is None:
            return None
        valor = cobrado_sin_iva(lead.cotizacion)
        if valor <= 0:
            return None
    return _crear(lead, nombre, atribucion, valor=valor)


def _encolar_lead_whatsapp(lead_id):
    if not configurado():
        return None
    lead = Lead.objects.get(pk=lead_id)
    if not lead.anuncio_clid or not settings.META_WA_WABA_ID:
        return None
    return _crear(lead, EventoConversion.LEAD,
                  {'origen': EventoConversion.WHATSAPP, 'ctwa_clid': lead.anuncio_clid})


def _seguro(funcion, *args):
    def correr():
        try:
            funcion(*args)
        except Exception:
            logger.exception('Marketing: no se pudo encolar el evento de conversión')
    if not _pausado.get():
        transaction.on_commit(correr)


def encolar_etapa(lead, etapa):
    """El prospecto llegó a `etapa`: encola el evento que corresponda. Nunca lanza."""
    from . import conversiones_google
    _seguro(_encolar_etapa, lead.pk, etapa)
    _seguro(conversiones_google.encolar_etapa, lead.pk, etapa)


def encolar_lead_whatsapp(lead):
    """El prospecto trae el clic de un anuncio de WhatsApp: encola su Lead. Nunca lanza."""
    _seguro(_encolar_lead_whatsapp, lead.pk)


def registrar_origen_web(*, cotizacion, event_id, fbp='', fbc='', user_agent='', ip='', url=''):
    """La solicitud del cotizador web trae el ``event_id`` del Pixel: el Lead del
    servidor lo reutiliza para que Meta deduplique. Nunca lanza."""
    try:
        if not configurado() or event_id is None:
            return None
        from . import pipeline
        cliente = cotizacion.cliente
        lead = pipeline.leads_de(cliente.telefono, cliente).order_by('-fecha_primer_contacto').first()
        if lead is None or lead.anonimizado_en:
            return None
        contexto = {k: v for k, v in (('client_user_agent', user_agent[:300]), ('client_ip_address', ip),
                                      ('event_source_url', url[:300])) if v}
        web = {'origen': EventoConversion.WEB, 'fbp': fbp, 'fbc': fbc, 'contexto_web': contexto}
        existente = lead.conversiones.filter(plataforma=EventoConversion.META,
                                             nombre_evento=EventoConversion.LEAD).first()
        if existente is None:
            _crear(lead, EventoConversion.LEAD, web, event_id=event_id)
        elif existente.estado == EventoConversion.PENDIENTE and existente.origen != EventoConversion.WEB:
            existente.event_id, existente.origen = event_id, EventoConversion.WEB
            existente.ctwa_clid, existente.fbp, existente.fbc, existente.contexto_web = '', fbp, fbc, contexto
            existente.save()
        if lead.etapa in EVENTO_POR_ETAPA:
            _encolar_etapa(lead.pk, lead.etapa)
    except Exception:
        logger.exception('Marketing: no se pudo registrar el origen web de una solicitud')
    return None


def limpiar_atribucion(lead) -> int:
    """Al anonimizar al prospecto: fuera los identificadores de la bandeja."""
    return EventoConversion.objects.filter(lead=lead).update(
        ctwa_clid='', fbp='', fbc='', contexto_web={})


# --- Envío --------------------------------------------------------------------

def construir_evento(ev) -> dict:
    """El objeto de `data` para este evento. El valor va como marcador (ver `_cuerpo`)."""
    lead = ev.lead
    cliente = lead.cliente
    evento = {'event_name': ev.nombre_evento, 'event_time': int(ev.event_time.timestamp()),
              'event_id': str(ev.event_id)}
    if ev.origen == EventoConversion.WHATSAPP:
        evento.update(action_source='business_messaging', messaging_channel='whatsapp',
                      user_data={'whatsapp_business_account_id': settings.META_WA_WABA_ID,
                                 'ctwa_clid': ev.ctwa_clid})
    else:
        user = {}
        ph = hash_telefono(lead.telefono or (cliente.telefono if cliente else ''))
        em = hash_correo(cliente.email if cliente else '')
        if ph:
            user['ph'] = [ph]
        if em:
            user['em'] = [em]
        if ev.fbp:
            user['fbp'] = ev.fbp
        if ev.fbc:
            user['fbc'] = ev.fbc
        contexto = ev.contexto_web or {}
        if ev.nombre_evento == EventoConversion.LEAD and contexto.get('client_user_agent'):
            evento['action_source'] = 'website'
            user['client_user_agent'] = contexto['client_user_agent']
            if contexto.get('client_ip_address'):
                user['client_ip_address'] = contexto['client_ip_address']
            if contexto.get('event_source_url'):
                evento['event_source_url'] = contexto['event_source_url']
        else:
            evento['action_source'] = 'system_generated'
        evento['user_data'] = user
    if ev.valor is not None:
        evento['custom_data'] = {'currency': ev.moneda, 'value': MARCADOR_VALOR}
    return evento


def _cuerpo(ev) -> str:
    """JSON del envío. El valor se escribe exacto desde el Decimal (nada de float)."""
    cuerpo = {'data': [construir_evento(ev)], 'access_token': settings.META_CAPI_TOKEN}
    if settings.META_CAPI_TEST_CODE:
        cuerpo['test_event_code'] = settings.META_CAPI_TEST_CODE
    texto = json.dumps(cuerpo)
    if ev.valor is not None:
        texto = texto.replace(json.dumps(MARCADOR_VALOR), format(ev.valor, 'f'))
    return texto


def _enviar_a_meta(ev):
    """(ok, transitorio, error). El error nunca lleva el token ni datos del cliente."""
    url = f'{GRAPH}/{settings.META_CAPI_API_VERSION}/{dataset_id()}/events'
    try:
        r = requests.post(url, data=_cuerpo(ev).encode('utf-8'),
                          headers={'Content-Type': 'application/json'}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return False, True, f'Sin respuesta de Meta ({type(exc).__name__})'
    try:
        cuerpo = r.json()
    except ValueError:
        cuerpo = {}
    if r.status_code == 200 and cuerpo.get('events_received', 0) >= 1:
        return True, False, ''
    error = cuerpo.get('error') or {}
    codigo = error.get('code')
    detalle = f"HTTP {r.status_code}"
    if codigo is not None:
        detalle += f" · código {codigo}/{error.get('error_subcode', '-')} · {str(error.get('type', ''))[:40]}"
    transitorio = r.status_code >= 500 or r.status_code == 429 or codigo in CODIGOS_TRANSITORIOS
    return False, transitorio, detalle[:300]


def _cerrar(ev, estado, ahora, error=''):
    ev.estado, ev.ultimo_error, ev.contexto_web = estado, error[:300], {}
    if estado == EventoConversion.ENVIADO:
        ev.enviado_en = ahora
    ev.save()


def _procesar(ev, ahora) -> str:
    lead = ev.lead
    if ev.nombre_evento == EventoConversion.PURCHASE and lead.etapa != Lead.GANADO:
        _cerrar(ev, EventoConversion.CANCELADO, ahora, 'La venta ya no está confirmada')
        return EventoConversion.CANCELADO
    if not tiene_consentimiento(lead):
        _cerrar(ev, EventoConversion.OMITIDO, ahora)
        return EventoConversion.OMITIDO
    es_google = ev.plataforma == EventoConversion.GOOGLE
    ventana = VENTANA_GOOGLE if es_google else VENTANA_META
    if ahora - ev.event_time > ventana:
        _cerrar(ev, EventoConversion.FALLIDO, ahora,
                f'El evento tiene más de {ventana.days} días: {"Google" if es_google else "Meta"} ya no lo acepta')
        return EventoConversion.FALLIDO
    if es_google:
        from . import conversiones_google
        ok, transitorio, error = conversiones_google.enviar(ev)
    elif ev.origen == EventoConversion.WHATSAPP and not (ev.ctwa_clid and settings.META_WA_WABA_ID):
        _cerrar(ev, EventoConversion.FALLIDO, ahora, 'Falta el clic del anuncio o META_WA_WABA_ID')
        return EventoConversion.FALLIDO
    else:
        ok, transitorio, error = _enviar_a_meta(ev)
    ev.intentos += 1
    if ok:
        _cerrar(ev, EventoConversion.ENVIADO, ahora)
        return EventoConversion.ENVIADO
    if transitorio and ev.intentos < MAX_INTENTOS:
        ev.ultimo_error = error
        ev.proximo_intento = ahora + ESPERA_BASE * (2 ** (ev.intentos - 1))
        ev.save()
        return EventoConversion.PENDIENTE
    _cerrar(ev, EventoConversion.FALLIDO, ahora, error)
    return EventoConversion.FALLIDO


def _avisar_fallidos(ahora):
    from comunicacion.services_pendientes import registrar_pendiente

    fallidos = EventoConversion.objects.filter(estado=EventoConversion.FALLIDO,
                                               updated_at__gte=ahora - timedelta(days=7))
    total = fallidos.count()
    if total < UMBRAL_FALLIDOS:
        return None
    ultimo = fallidos.order_by('-updated_at').first()
    anio, semana, _ = ahora.isocalendar()
    return registrar_pendiente(
        titulo='Eventos de conversión sin enviar a Meta o Google', rol='DIRECCION', tipo='ALERTA', severidad='NORMAL',
        detalle=f'{total} eventos fallidos en 7 días\nÚltimo error: {ultimo.ultimo_error or "sin detalle"}\n'
                'Revisa Marketing > Conversiones',
        clave_idempotencia=f'marketing_capi_fallidos:{anio}-{semana}')


def enviar_pendientes(ahora=None) -> dict:
    """Envía los eventos que ya toca. Devuelve un conteo por resultado."""
    from . import conversiones_google
    activas = ([EventoConversion.META] if configurado() else []) + (
        [EventoConversion.GOOGLE] if conversiones_google.configurado() else [])
    resumen = {'apagado': not activas, 'enviados': 0, 'reintentos': 0, 'fallidos': 0,
               'omitidos': 0, 'cancelados': 0}
    if resumen['apagado']:
        return resumen
    ahora = ahora or timezone.now()
    ids = list(EventoConversion.objects.filter(estado=EventoConversion.PENDIENTE, plataforma__in=activas,
                                               proximo_intento__lte=ahora)
               .order_by('created_at').values_list('pk', flat=True)[:LOTE])
    claves = {EventoConversion.ENVIADO: 'enviados', EventoConversion.PENDIENTE: 'reintentos',
              EventoConversion.FALLIDO: 'fallidos', EventoConversion.OMITIDO: 'omitidos',
              EventoConversion.CANCELADO: 'cancelados'}
    for pk in ids:
        # Reclamo: si otro proceso lo tomó o este muere a medias, se vuelve a ver pasado RECLAMO.
        reclamado = EventoConversion.objects.filter(
            pk=pk, estado=EventoConversion.PENDIENTE, proximo_intento__lte=ahora,
        ).update(proximo_intento=ahora + RECLAMO)
        if not reclamado:
            continue
        try:
            ev = EventoConversion.objects.select_related('lead__cliente', 'lead__conversacion').get(pk=pk)
            resumen[claves[_procesar(ev, ahora)]] += 1
        except Exception:
            logger.exception('Marketing: falló el envío del evento de conversión %s', pk)
    if resumen['fallidos']:
        _avisar_fallidos(ahora)
    return resumen


def reintentar(queryset) -> int:
    """Devuelve a PENDIENTE los fallidos (acción del admin)."""
    return queryset.filter(estado=EventoConversion.FALLIDO).update(
        estado=EventoConversion.PENDIENTE, intentos=0, proximo_intento=timezone.now(), ultimo_error='')

