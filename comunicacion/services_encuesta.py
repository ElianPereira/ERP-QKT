"""
Encuesta de satisfacción y reactivación anual (Fase 3, Issue #434).

Encuesta: un día después del servicio, plantilla con tres botones de
calificación (`enc:<id>:5|3|1`); el cliente también puede contestar con un
número 1–5. 4–5 → se le pide reseña en Google; 1–3 → pendiente URGENTE a
Dirección (sin texto del cliente). Reactivación: en el aniversario del evento.
Ambas exigen la finalidad MARKETING y que no haya pedido la baja; nada aquí
lanza hacia el cron que las llama.
"""
import logging
import re
from datetime import date, timedelta
from typing import Optional

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .formato_wa import bloques
from .models import EncuestaPostEvento
from .services import enviar_whatsapp_template, normalizar_telefono_wa
from .services_notificaciones import _fecha, _nombre_pila, _plantilla, _seguro, _telefono, _tipo_evento
from .services_pendientes import registrar_pendiente

logger = logging.getLogger(__name__)

ESTADOS_SERVICIO_PRESTADO = ('CONFIRMADA', 'EJECUTADA', 'CERRADA')
ESTADOS_EVENTO_OCURRIDO = ('EJECUTADA', 'CERRADA')
MARGEN_DIAS = 3  # recupera un día sin cron sin escribirle a quien lo vivió hace semanas
VIGENCIA_TEXTO = timedelta(days=3)
BOTONES = ((5, 'Excelente'), (3, 'Regular'), (1, 'Mala'))
RE_BOTON = re.compile(r'^enc:(\d+):([1-5])$')
RE_NUMERO = re.compile(r'^\s*([1-5])\s*$')
GRACIAS_BUENA = bloques('¡Muchas gracias! Nos da mucho gusto que lo hayas disfrutado.')
GRACIAS_MALA = bloques('Gracias por decírnoslo; lamentamos que no haya sido la experiencia que esperabas.',
                       'Una persona del equipo te escribirá para atenderte.')
YA_CONTESTADA = bloques('Ya habíamos recibido tu calificación.', '¡Gracias!')


def id_boton(encuesta_id: int, calificacion: int) -> str:
    return f'enc:{encuesta_id}:{calificacion}'


def es_boton(texto: str) -> bool:
    return bool(RE_BOTON.match((texto or '').strip()))


def _fin_servicio(cotizacion) -> date:
    return cotizacion.fecha_salida or cotizacion.fecha_evento


def _motivo_marketing(cliente) -> str:
    """'' si se le puede escribir con fines de relación comercial; si no, por qué no."""
    from legal.services import LegalService

    from .services_bajas import dado_de_baja

    telefono = _telefono(cliente)
    if not telefono:
        return 'sin teléfono'
    if not LegalService.cliente_acepto(cliente, 'MARKETING'):
        return 'no aceptó promociones (MARKETING)'
    if dado_de_baja(telefono, cliente):
        return 'pidió no recibir más mensajes (BAJA)'
    return ''


# ─────────────────────────────────── Encuesta ──────────────────────────────────

def cotizaciones_para_encuesta(hoy=None):
    """Servicios terminados ayer (o en los `MARGEN_DIAS` previos) sin encuesta."""
    from comercial.models import Cotizacion

    hoy = hoy or timezone.localdate()
    candidatas = (Cotizacion.objects
                  .filter(estado__in=ESTADOS_SERVICIO_PRESTADO, encuesta__isnull=True,
                          fecha_evento__gte=hoy - timedelta(days=MARGEN_DIAS + 30), fecha_evento__lt=hoy)
                  .select_related('cliente').order_by('id'))
    return [c for c in candidatas
            if hoy - timedelta(days=MARGEN_DIAS) <= _fin_servicio(c) <= hoy - timedelta(days=1)]


def motivo_para_no_encuestar(cotizacion) -> str:
    if not _plantilla('META_WA_PLANTILLA_ENCUESTA'):
        return 'sin plantilla de encuesta configurada'
    return _motivo_marketing(cotizacion.cliente)


def enviar_encuesta(cotizacion):
    """Manda la encuesta. Idempotente por cotización: la fila es la reserva."""
    if motivo_para_no_encuestar(cotizacion):
        return None
    cliente = cotizacion.cliente
    telefono = _telefono(cliente)
    try:
        with transaction.atomic():
            encuesta = EncuestaPostEvento.objects.create(cotizacion=cotizacion, telefono=telefono)
    except IntegrityError:
        return None
    components = [
        {'type': 'body', 'parameters': [
            {'type': 'text', 'text': _nombre_pila(cliente)},
            {'type': 'text', 'text': _tipo_evento(cotizacion)},
        ]},
    ] + [
        {'type': 'button', 'sub_type': 'quick_reply', 'index': str(i),
         'parameters': [{'type': 'payload', 'payload': id_boton(encuesta.pk, valor)}]}
        for i, (valor, _) in enumerate(BOTONES)
    ]
    comm = _seguro(
        'enviar la encuesta de satisfacción', enviar_whatsapp_template,
        cotizacion=cotizacion, tipo='ENCUESTA', telefono=telefono,
        template_name=_plantilla('META_WA_PLANTILLA_ENCUESTA'), components=components, trigger='CRON',
        clave_idempotencia=f'encuesta:{cotizacion.pk}:whatsapp')
    if comm is None or comm.estado == 'FALLIDO':
        encuesta.delete()  # no se contó: la clave de idempotencia evita reintentar a ciegas
        return comm
    from .services_agente import registrar_mensaje_automatico
    registrar_mensaje_automatico(
        telefono=telefono, wamid=comm.proveedor_id,
        texto='Encuesta: se le preguntó cómo calificaría su experiencia (1 a 5).')
    return comm


def atender_respuesta(telefono: str, texto: str):
    """Procesa un botón `enc:<id>:<n>` o un número 1–5 del teléfono de una
    encuesta abierta. Devuelve `(atendido, respuesta)`: `atendido` es True si el
    mensaje era de la encuesta (aunque no haya nada que contestar)."""
    telefono = normalizar_telefono_wa(telefono)
    texto = (texto or '').strip()
    boton = RE_BOTON.match(texto)
    if boton:
        encuesta = EncuestaPostEvento.objects.filter(pk=int(boton.group(1))).select_related('cotizacion__cliente').first()
        if encuesta is None or normalizar_telefono_wa(encuesta.telefono) != telefono:
            return True, None  # botón ajeno: se descarta sin llegar al agente
        calificacion = int(boton.group(2))
    elif RE_NUMERO.match(texto):
        encuesta = (EncuestaPostEvento.objects
                    .filter(telefono=telefono, estado='ENVIADA', enviada_en__gte=timezone.now() - VIGENCIA_TEXTO)
                    .select_related('cotizacion__cliente').order_by('-enviada_en').first())
        if encuesta is None:
            return False, None
        calificacion = int(RE_NUMERO.match(texto).group(1))
    else:
        return False, None
    return True, _registrar_calificacion(encuesta, calificacion)


def _registrar_calificacion(encuesta, calificacion: int) -> Optional[str]:
    ahora = timezone.now()
    filas = EncuestaPostEvento.objects.filter(pk=encuesta.pk, estado='ENVIADA').update(
        estado='CONTESTADA', calificacion=calificacion, contestada_en=ahora, updated_at=ahora)
    if not filas:
        return YA_CONTESTADA
    cotizacion = encuesta.cotizacion
    if calificacion >= 4:
        url = getattr(settings, 'ERP_RESENA_GOOGLE_URL', '') or ''
        if not url:
            return GRACIAS_BUENA
        EncuestaPostEvento.objects.filter(pk=encuesta.pk).update(resena_solicitada=True)
        return bloques(GRACIAS_BUENA, 'Solo toma un minuto:', enlace=url, pregunta='¿Nos ayudas con una reseña en Google?')
    registrar_pendiente(
        titulo=f'Encuesta con calificación {calificacion}/5 en COT-{cotizacion.pk:03d}',
        rol='DIRECCION', tipo='QUEJA', severidad='URGENTE', objeto=cotizacion,
        detalle=f'{cotizacion.cliente.nombre} ({encuesta.telefono}) calificó su experiencia con {calificacion} de 5. '
                'Contáctalo para atender lo que pasó.',
        clave_idempotencia=f'encuesta:{encuesta.pk}:queja')
    return GRACIAS_MALA


# ──────────────────────────────── Reactivación ────────────────────────────────

def _mas_un_anio(fecha: date) -> date:
    try:
        return fecha.replace(year=fecha.year + 1)
    except ValueError:  # 29 de febrero
        return fecha.replace(year=fecha.year + 1, day=28)


def cotizaciones_para_reactivacion(hoy=None):
    """Eventos ocurridos cuyo aniversario cayó hoy o en los `MARGEN_DIAS` previos."""
    from comercial.models import Cotizacion

    hoy = hoy or timezone.localdate()
    candidatas = (Cotizacion.objects
                  .filter(estado__in=ESTADOS_EVENTO_OCURRIDO,
                          fecha_evento__gte=hoy - timedelta(days=366 + MARGEN_DIAS),
                          fecha_evento__lte=hoy - timedelta(days=364))
                  .select_related('cliente').order_by('id'))
    return [c for c in candidatas
            if hoy - timedelta(days=MARGEN_DIAS) <= _mas_un_anio(c.fecha_evento) <= hoy]


def motivo_para_no_reactivar(cotizacion, hoy=None) -> str:
    from comercial.models import Cotizacion

    hoy = hoy or timezone.localdate()
    if not _plantilla('META_WA_PLANTILLA_REACTIVACION'):
        return 'sin plantilla de reactivación configurada'
    cliente = cotizacion.cliente
    motivo = _motivo_marketing(cliente)
    if motivo:
        return motivo
    if Cotizacion.objects.filter(cliente=cliente, fecha_evento__gte=hoy,
                                 estado__in=('BORRADOR', 'COTIZADA', 'CONFIRMADA')).exists():
        return 'ya tiene otra reservación en curso'
    encuesta = EncuestaPostEvento.objects.filter(cotizacion__cliente=cliente, calificacion__lte=3).exists()
    if encuesta:
        return 'calificó mal su experiencia'
    return ''


def enviar_reactivacion(cotizacion, hoy=None):
    """Una por cliente y año. Idempotente por clave."""
    from .herramientas_agente import URL_COTIZADOR

    if motivo_para_no_reactivar(cotizacion, hoy):
        return None
    cliente = cotizacion.cliente
    anio = _mas_un_anio(cotizacion.fecha_evento).year
    comm = _seguro(
        'enviar la reactivación por aniversario', enviar_whatsapp_template,
        cotizacion=cotizacion, tipo='REACTIVACION', telefono=_telefono(cliente),
        template_name=_plantilla('META_WA_PLANTILLA_REACTIVACION'), trigger='CRON',
        parametros=[_nombre_pila(cliente), _tipo_evento(cotizacion), _fecha(cotizacion.fecha_evento), URL_COTIZADOR],
        clave_idempotencia=f'reactivacion:{cliente.pk}:{anio}')
    if comm is not None and comm.estado != 'FALLIDO':
        from .services_agente import registrar_mensaje_automatico
        registrar_mensaje_automatico(
            telefono=_telefono(cliente), wamid=comm.proveedor_id,
            texto=(f'Reactivación: se le recordó su {_tipo_evento(cotizacion)} del '
                   f'{_fecha(cotizacion.fecha_evento)} y se le invitó a cotizar de nuevo.'))
    return comm
