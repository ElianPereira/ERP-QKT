"""
Órdenes de Dirección por WhatsApp (Fase 2b, Issue #433).

Kooxi solo PROPONE; el ERP ejecuta. Patrón de `services_terminal.py`, con un
candado más estricto en el teléfono:

  1. el teléfono debe ser el `telefono_wa` activo de un `Responsable` DIRECCION.
     No vale ENCARGADO, ni un número pendiente de «ACEPTO», ni el respaldo
     `ERP_WA_NEGOCIO_NUMERO` (que hoy es el del encargado);
  2. la propuesta es una `AccionWhatsApp` con botones [Confirmar]/[Cancelar]
     cuyo token es aleatorio, se guarda con HMAC, es de un solo uso, vence en
     10 min y solo sirve para el mismo teléfono;
  3. al confirmar se revalida todo bajo `select_for_update` (lo revalida cada
     ejecutor: saldo, custodia, tope, choques de fecha…).

El texto que ve Dirección lo arma el ERP desde datos estructurados; el modelo
nunca lo redacta. Cada propuesta, rechazo y resolución queda en la bitácora.

Aquí viven la mecánica común y el bloqueo de fechas; las acciones de dinero
están en `services_dinero_wa.py`.
"""
import logging
import re
import secrets
from datetime import date, timedelta
from typing import NamedTuple, Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from comunicacion.services import normalizar_telefono_wa
from comunicacion.services_responsables import responsable

from .disponibilidad import rango_traslapa
from .models import AccionWhatsApp, BloqueoFecha, Cotizacion

logger = logging.getLogger(__name__)

RE_BOTON = re.compile(r'^ord:([A-Za-z0-9_-]{16,64}):(ok|no)$')
VIGENCIA = timedelta(minutes=10)
ETIQUETA_CONFIRMAR = 'Confirmar'
ETIQUETA_CANCELAR = 'Cancelar'
MAX_DIAS_BLOQUEO = 90

BOTON_INVALIDO = 'Ese botón ya no es válido. Si lo necesitas, pídemelo de nuevo.'
BOTON_VENCIDO = 'Pasaron más de 10 minutos y el botón venció. Pídemelo de nuevo si sigue pendiente.'
YA_REGISTRADA = 'Esa orden ya se había atendido. No se hizo nada más.'
CANCELADA = 'Listo, cancelé la orden. No se hizo ningún cambio.'
ERROR_INTERNO = 'No se pudo ejecutar la orden y no se hizo ningún cambio. Hazlo desde el admin.'
NO_AUTORIZADO = 'Este número no está autorizado para dar órdenes.'


class OrdenError(ValueError):
    """Motivo, apto para mostrarse a Dirección, por el que una orden no procede."""


class Resultado(NamedTuple):
    texto: str
    botones: Optional[list] = None   # [(id, título)]: si hay, el texto es la propuesta a confirmar
    accion: Optional[AccionWhatsApp] = None
    ejecutada: bool = False          # True solo al confirmar con éxito


def es_boton(texto: str) -> bool:
    return bool(RE_BOTON.match((texto or '').strip()))


def id_boton(token: str, decision: str) -> str:
    return f'ord:{token}:{decision}'


def _hash(token: str) -> str:
    return salted_hmac('comercial.ordenes_whatsapp', token, algorithm='sha256').hexdigest()


def direccion_activa(telefono: str) -> bool:
    """¿Es el teléfono ACTIVO del responsable de Dirección? Sin respaldo: un
    número pendiente de «ACEPTO», el del encargado o el de cualquier otro rol
    no mueven dinero."""
    tel = normalizar_telefono_wa(telefono)
    resp = responsable('DIRECCION')
    return bool(tel) and resp is not None and tel == normalizar_telefono_wa(resp.telefono_wa)


def dinero(valor) -> str:
    return f'${valor:,.2f}'


def folio(cot: Cotizacion) -> str:
    return f'COT-{cot.pk:03d}'


def fecha_corta(valor: date) -> str:
    return valor.strftime('%d/%m/%Y')


# ───────────────────────────── Bitácora y propuesta ─────────────────────────

def rechazar(telefono: str, tipo: str, motivo: str, *, cotizacion=None, monto=None, parametros=None) -> Resultado:
    """Deja asentado en la bitácora lo que Dirección pidió y no procedió."""
    ahora = timezone.now()
    accion = AccionWhatsApp.objects.create(
        telefono=normalizar_telefono_wa(telefono), rol='DIRECCION', tipo=tipo, cotizacion=cotizacion,
        monto=monto, parametros=parametros or {}, estado='RECHAZADA', resultado=motivo[:2000],
        resuelta_en=ahora)
    return Resultado(motivo, accion=accion)


def crear_propuesta(telefono: str, tipo: str, resumen: str, *, cotizacion=None, monto=None,
                    parametros=None) -> Resultado:
    """Registra la propuesta y devuelve el texto con los dos botones. Una
    propuesta idéntica anterior sin confirmar queda sin efecto: dos vivas de la
    misma orden podrían confirmarse las dos."""
    tel = normalizar_telefono_wa(telefono)
    parametros = parametros or {}
    token = secrets.token_urlsafe(24)
    ahora = timezone.now()
    with transaction.atomic():
        AccionWhatsApp.objects.filter(
            telefono=tel, tipo=tipo, cotizacion=cotizacion, monto=monto, parametros=parametros,
            estado='PROPUESTA').update(estado='VENCIDA', resultado='Sustituida por una propuesta idéntica.',
                                       resuelta_en=ahora, updated_at=ahora)
        accion = AccionWhatsApp.objects.create(
            telefono=tel, rol='DIRECCION', tipo=tipo, cotizacion=cotizacion, monto=monto,
            parametros=parametros, resumen=resumen, token_hash=_hash(token), vence_en=ahora + VIGENCIA)
    texto = f'{resumen} Toca Confirmar en los próximos 10 minutos.'
    return Resultado(texto, botones=[(id_boton(token, 'ok'), ETIQUETA_CONFIRMAR),
                                     (id_boton(token, 'no'), ETIQUETA_CANCELAR)], accion=accion)


# ───────────────────────────── Botones ──────────────────────────────────────

def _ejecutor(tipo: str):
    if tipo == 'BLOQUEO_FECHAS':
        return _ejecutar_bloqueo
    from . import services_dinero_wa
    return services_dinero_wa.EJECUTORES[tipo]


def responder_boton(telefono: str, texto: str) -> Optional[Resultado]:
    """Botón `ord:<token>:ok|no`. None si no es un botón de órdenes o el
    teléfono no es Dirección (no hace nada, ni siquiera consulta el token)."""
    coincide = RE_BOTON.match((texto or '').strip())
    if not coincide or not direccion_activa(telefono):
        return None
    tel = normalizar_telefono_wa(telefono)
    token, decision = coincide.groups()
    try:
        with transaction.atomic():
            accion = AccionWhatsApp.objects.select_for_update().filter(token_hash=_hash(token)).first()
            if accion is None or accion.telefono != tel:
                return Resultado(BOTON_INVALIDO)
            if accion.estado == 'CONFIRMADA':
                return Resultado(YA_REGISTRADA, accion=accion)
            if accion.estado == 'CANCELADA':
                return Resultado(YA_REGISTRADA, accion=accion)
            if accion.estado != 'PROPUESTA':
                return Resultado(BOTON_VENCIDO)
            ahora = timezone.now()
            if accion.vence_en is None or accion.vence_en < ahora:
                _cerrar(accion, 'VENCIDA', 'El botón venció sin confirmarse.')
                return Resultado(BOTON_VENCIDO)
            if decision == 'no':
                _cerrar(accion, 'CANCELADA', 'Cancelada por Dirección.')
                return Resultado(CANCELADA, accion=accion)
            try:
                with transaction.atomic():
                    mensaje = _ejecutor(accion.tipo)(accion)
            except (OrdenError, ValidationError) as e:
                motivo = '; '.join(e.messages) if isinstance(e, ValidationError) else str(e)
                _cerrar(accion, 'FALLIDA', motivo)
                return Resultado(f'No se hizo ningún cambio: {motivo}', accion=accion)
            accion.estado, accion.resuelta_en, accion.resultado = 'CONFIRMADA', ahora, mensaje[:2000]
            accion.save()
            return Resultado(mensaje, accion=accion, ejecutada=True)
    except Exception:
        logger.exception("Órdenes por WhatsApp: no se pudo ejecutar la orden de %s", tel)
        AccionWhatsApp.objects.filter(token_hash=_hash(token), estado='PROPUESTA').update(
            estado='FALLIDA', resultado='Error interno.', resuelta_en=timezone.now(), updated_at=timezone.now())
        return Resultado(ERROR_INTERNO)


def _cerrar(accion, estado: str, resultado: str) -> None:
    accion.estado, accion.resultado, accion.resuelta_en = estado, resultado[:2000], timezone.now()
    accion.save(update_fields=['estado', 'resultado', 'resuelta_en', 'updated_at'])


# ───────────────────────────── Bloqueo de fechas ────────────────────────────

MOTIVOS_BLOQUEO = dict(BloqueoFecha.MOTIVO_CHOICES)


def _fecha_iso(valor) -> Optional[date]:
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _abiertas_en(inicio: date, fin_exclusivo: date) -> list:
    """BORRADOR/COTIZADA que traslapan: ya no podrán pagarse ni confirmarse."""
    return list(rango_traslapa(
        Cotizacion.objects.filter(estado__in=Cotizacion.ESTADOS_SIN_APARTAR), inicio, fin_exclusivo,
    ).order_by('fecha_evento')[:10])


def _validar_bloqueo(desde: date, hasta: date, motivo: str) -> BloqueoFecha:
    """El bloqueo sin guardar, ya validado con las mismas reglas del calendario
    (`clean()`: no encima de una CONFIRMADA). Lanza OrdenError si no procede."""
    if motivo not in MOTIVOS_BLOQUEO:
        raise OrdenError('El motivo debe ser mantenimiento, reparación, uso propio u otro.')
    if hasta < desde:
        raise OrdenError('La fecha final no puede ser anterior a la inicial.')
    if hasta < timezone.localdate():
        raise OrdenError('Esas fechas ya pasaron.')
    if (hasta - desde).days + 1 > MAX_DIAS_BLOQUEO:
        raise OrdenError(f'Son más de {MAX_DIAS_BLOQUEO} días: bloquéalo desde el admin.')
    if BloqueoFecha.objects.filter(activo=True, fecha_inicio__lte=desde, fecha_fin__gte=hasta).exists():
        raise OrdenError('Esas fechas ya están bloqueadas.')
    bloqueo = BloqueoFecha(fecha_inicio=desde, fecha_fin=hasta, motivo=motivo, activo=True)
    try:
        bloqueo.full_clean()
    except ValidationError as e:
        raise OrdenError('; '.join(e.messages)) from e
    return bloqueo


def _texto_afectadas(abiertas: list) -> str:
    return ', '.join(f'{folio(c)} ({fecha_corta(c.fecha_evento)})' for c in abiertas)


def proponer_bloqueo(telefono: str, desde, hasta, motivo: str = 'MANTENIMIENTO', notas: str = '') -> Optional[Resultado]:
    """«Bloquea del 3 al 5 de diciembre por mantenimiento» → propuesta con botones.
    None si el teléfono no es Dirección."""
    if not direccion_activa(telefono):
        return None
    d, h = _fecha_iso(desde), _fecha_iso(hasta or desde)
    parametros = {'desde': str(desde), 'hasta': str(hasta or desde), 'motivo': motivo, 'notas': (notas or '')[:200]}
    if d is None or h is None:
        return rechazar(telefono, 'BLOQUEO_FECHAS', 'No entendí las fechas. Dame el día inicial y el final.',
                        parametros=parametros)
    parametros.update(desde=d.isoformat(), hasta=h.isoformat())
    try:
        _validar_bloqueo(d, h, motivo)
    except OrdenError as e:
        return rechazar(telefono, 'BLOQUEO_FECHAS', str(e), parametros=parametros)
    dias = (h - d).days + 1
    resumen = (f'Bloquear del {fecha_corta(d)} al {fecha_corta(h)} ({dias} día{"s" if dias != 1 else ""}) '
               f'por {MOTIVOS_BLOQUEO[motivo].lower()}.')
    abiertas = _abiertas_en(d, h + timedelta(days=1))
    if abiertas:
        resumen += (f' Ojo: estas cotizaciones abiertas ya no podrán pagarse ni confirmarse: '
                    f'{_texto_afectadas(abiertas)}.')
    return crear_propuesta(telefono, 'BLOQUEO_FECHAS', resumen, parametros=parametros)


def _ejecutar_bloqueo(accion: AccionWhatsApp) -> str:
    p = accion.parametros
    d, h = _fecha_iso(p.get('desde')), _fecha_iso(p.get('hasta'))
    if d is None or h is None:
        raise OrdenError('Las fechas de la orden no son válidas.')
    bloqueo = _validar_bloqueo(d, h, p.get('motivo', ''))   # revalida: pudo confirmarse algo en estos minutos
    nota = f'Bloqueado por WhatsApp desde el número de {accion.rol} ({accion.telefono}), orden #{accion.pk}.'
    bloqueo.notas = f'{nota} {p.get("notas", "")}'.strip()
    bloqueo.save()
    accion.bloqueo = bloqueo
    texto = f'Listo: fechas bloqueadas del {fecha_corta(d)} al {fecha_corta(h)}.'
    abiertas = _abiertas_en(d, h + timedelta(days=1))
    if abiertas:
        texto += (f' Cotizaciones abiertas que ya no podrán pagarse ni confirmarse: {_texto_afectadas(abiertas)}. '
                  'Avísales a los clientes.')
    return texto
