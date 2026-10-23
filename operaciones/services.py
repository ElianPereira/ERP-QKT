"""
Armado y envío de los tres mensajes del módulo de operaciones (Issue #257):

1. Aviso anticipado de horario especial (solo si `requiere_tiempo_extra`).
2. Mensaje operativo (checklist) al responsable, 2 h antes de su entrada.
3. Resumen de operaciones al encargado: ya no sale solo; va dentro del resumen
   diario de la bandeja de pendientes (`comunicacion.services_pendientes`).

Todo es informativo y unidireccional — no hay confirmación digital del
colaborador. El estado de cada envío vive en los tres campos
`estado_*` de `TareaProgramada`, no en `ComunicacionCliente` (que aquí solo
sirve de bitácora de auditoría, igual que en el resto del ERP, pero sin clave
de idempotencia: repetir el intento hasta que el campo `estado_*` quede en
ENVIADO es el comportamiento buscado, no un error a evitar).

Los colaboradores no necesariamente le han escrito al número del negocio en
las últimas 24 h, así que un mensaje de texto libre iniciado por el sistema
puede rechazarse (Meta 131047). El aviso de horario y el checklist operativo
salen primero como plantilla aprobada (`META_WA_PLANTILLA_OPERACIONES`) para abrir
paso, y el contenido real (con el detalle que Meta no deja meter en una
plantilla — listas con saltos de línea) sale justo después como texto libre.
Si `META_WA_PLANTILLA_OPERACIONES` no está configurada, se manda solo el texto
libre (funciona mientras la ventana esté abierta, mismo criterio que el resto
de mensajería interna del ERP sin plantilla).
"""
import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from comunicacion.services import (
    enviar_whatsapp,
    enviar_whatsapp_template,
    normalizar_telefono_wa,
)
from comunicacion.services_bajas import _normalizar
from comunicacion.services_pendientes import registrar_pendiente
from core_erp.horarios import formato_hora_ampm

from .constantes import HORA_ENTRADA_NORMAL, HORA_SALIDA_NORMAL, calcular_horario_turnover
from .models import PlantillaChecklist, TareaProgramada

logger = logging.getLogger(__name__)

MAX_TAREAS_POR_MENSAJE = 8
# «Listo» del personal (Issue #428): el mensaje completo debe ser una de estas frases.
PALABRAS_LISTO = {'listo', 'ya', 'termine'}
MINUTOS_SIN_LISTO = 60

_DIAS_SEMANA = ('lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo')
_MESES = (
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
    'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
)


def _fecha_larga(fecha) -> str:
    """`date` -> "jueves 27 de agosto", sin depender del locale del servidor."""
    return f"{_DIAS_SEMANA[fecha.weekday()]} {fecha.day} de {_MESES[fecha.month - 1]}"


def _telefono_responsable(tarea: TareaProgramada) -> str:
    if not tarea.responsable:
        return ''
    return normalizar_telefono_wa(tarea.responsable.telefono)


def _plantilla_opener() -> str:
    return getattr(settings, 'META_WA_PLANTILLA_OPERACIONES', '') or ''


def _items_en_bloques(tarea: TareaProgramada):
    items = list(tarea.plantilla.items.order_by('orden').values_list('texto', flat=True))
    return [items[i:i + MAX_TAREAS_POR_MENSAJE] for i in range(0, len(items), MAX_TAREAS_POR_MENSAJE)] or [[]]


# ─────────────────────────── Armado de mensajes ────────────────────────────

def construir_bloques_checklist(tarea: TareaProgramada) -> list:
    """
    Devuelve una lista de textos (uno por mensaje). Un solo bloque salvo que
    la plantilla tenga más de MAX_TAREAS_POR_MENSAJE tareas.
    """
    titulo = tarea.plantilla.titulo_mensaje()
    fecha_texto = _fecha_larga(tarea.fecha).capitalize()
    hora_limite_texto = formato_hora_ampm(tarea.hora_limite)
    bloques_items = _items_en_bloques(tarea)
    mensajes = []
    total = len(bloques_items)

    for i, items in enumerate(bloques_items, start=1):
        lineas = [f"*{titulo}*"]
        if total > 1:
            lineas[0] += f" ({i}/{total})"
        lineas.append(fecha_texto)
        lineas.append('')
        if i == 1:
            lineas.append(f"Termina antes de: *{hora_limite_texto}*")
            if tarea.requiere_tiempo_extra:
                lineas.append("(Esto implica tiempo extra hoy — ya contemplado)")
            lineas.append('')
        for n, texto in enumerate(items, start=1):
            lineas.append(f"{n}. {texto}")
        mensajes.append('\n'.join(lineas))
    return mensajes


def construir_texto_aviso_horario(tarea: TareaProgramada) -> str:
    fecha_texto = _fecha_larga(tarea.fecha)
    hora_entrada_texto = formato_hora_ampm(tarea.hora_entrada)
    motivo = tarea.plantilla.titulo_mensaje()
    return (
        "*Aviso — cambio de horario*\n"
        f"{fecha_texto.capitalize()}\n\n"
        f"Entra a las *{hora_entrada_texto}* (en vez de tu horario normal)\n"
        f"Motivo: {motivo}\n\n"
        "Más tarde te mando el detalle de qué hacer."
    )


def resumen_operaciones(hoy) -> tuple:
    """Tareas de hoy y mañana aún sin resumir y su línea para el resumen diario
    del encargado (`(tareas, texto)`; texto vacío si no hay ninguna)."""
    tareas = list(
        TareaProgramada.objects.filter(
            estado_resumen_propietario='PENDIENTE', fecha__gte=hoy, fecha__lte=hoy + timedelta(days=1))
        .select_related('plantilla', 'responsable').order_by('fecha', 'hora_entrada'))
    if not tareas:
        return [], ''
    lineas = [
        f"{'hoy' if t.fecha == hoy else 'mañana'} {t.responsable.nombre if t.responsable else 'sin asignar'}: "
        f"{t.plantilla.titulo_mensaje()} (antes {formato_hora_ampm(t.hora_limite)})"
        for t in tareas[:3]
    ]
    if len(tareas) > 3:
        lineas.append(f'y {len(tareas) - 3} más')
    return tareas, '; '.join(lineas)


def marcar_resumen_enviado(tareas, estado: str) -> None:
    TareaProgramada.objects.filter(pk__in=[t.pk for t in tareas]).update(estado_resumen_propietario=estado)


# ─────────────────────────────── Envío ──────────────────────────────────────

def _abrir_conversacion(tarea: TareaProgramada, telefono: str) -> None:
    """Manda la plantilla opener si está configurada; ignora el resultado."""
    plantilla = _plantilla_opener()
    if not plantilla:
        return
    enviar_whatsapp_template(
        cotizacion=tarea.cotizacion,
        tipo='OTRO',
        telefono=telefono,
        template_name=plantilla,
        parametros=[tarea.plantilla.titulo_mensaje()],
    )


def enviar_aviso_horario(tarea: TareaProgramada) -> str:
    """Envía (o reintenta) el aviso de horario especial. Devuelve el estado resultante."""
    if not tarea.requiere_tiempo_extra:
        if tarea.estado_aviso_horario != 'NO_APLICA':
            tarea.estado_aviso_horario = 'NO_APLICA'
            tarea.save(update_fields=['estado_aviso_horario'])
        return 'NO_APLICA'
    if tarea.estado_aviso_horario == 'ENVIADO':
        return 'ENVIADO'

    telefono = _telefono_responsable(tarea)
    if not telefono:
        logger.warning("Tarea #%s sin teléfono de responsable; no se manda el aviso de horario", tarea.pk)
        tarea.estado_aviso_horario = 'FALLIDO'
        tarea.save(update_fields=['estado_aviso_horario'])
        return 'FALLIDO'

    _abrir_conversacion(tarea, telefono)
    comm = enviar_whatsapp(
        cotizacion=tarea.cotizacion,
        tipo='OTRO',
        telefono=telefono,
        mensaje=construir_texto_aviso_horario(tarea),
    )
    tarea.estado_aviso_horario = 'ENVIADO' if comm and comm.estado == 'ENVIADO' else 'FALLIDO'
    tarea.save(update_fields=['estado_aviso_horario'])
    return tarea.estado_aviso_horario


def enviar_mensaje_operativo(tarea: TareaProgramada) -> str:
    """Envía (o reintenta) el checklist operativo. Devuelve el estado resultante."""
    if tarea.estado_operativo == 'ENVIADO':
        return 'ENVIADO'

    telefono = _telefono_responsable(tarea)
    if not telefono:
        logger.warning("Tarea #%s sin teléfono de responsable; no se manda el checklist", tarea.pk)
        tarea.estado_operativo = 'FALLIDO'
        tarea.save(update_fields=['estado_operativo'])
        return 'FALLIDO'

    _abrir_conversacion(tarea, telefono)
    bloques = construir_bloques_checklist(tarea)
    exito = True
    for bloque in bloques:
        comm = enviar_whatsapp(
            cotizacion=tarea.cotizacion,
            tipo='OTRO',
            telefono=telefono,
            mensaje=bloque,
        )
        if not comm or comm.estado != 'ENVIADO':
            exito = False
    tarea.estado_operativo = 'ENVIADO' if exito else 'FALLIDO'
    tarea.save(update_fields=['estado_operativo'])
    return tarea.estado_operativo


# ─────────────────────────────── Generación ─────────────────────────────────

def generar_tarea_turnover(cotizacion) -> 'TareaProgramada | None':
    """
    Crea (si no existía) la TareaProgramada de preparación para una
    Cotizacion confirmada. Idempotente: se puede llamar varias veces para la
    misma cotización sin duplicar (misma lógica que el resto de signals del
    ERP, que confían en volver a ejecutarse en cada save()).

    Devuelve None si no hay plantilla activa para ese tipo de servicio.
    """
    tipo_plantilla = PlantillaChecklist.TIPO_POR_SERVICIO.get(cotizacion.tipo_servicio)
    if not tipo_plantilla:
        return None
    plantilla = PlantillaChecklist.objects.filter(tipo=tipo_plantilla, activa=True).first()
    if plantilla is None:
        logger.warning(
            "COT-%s: no hay PlantillaChecklist activa para %s; no se genera tarea de preparación",
            cotizacion.pk, cotizacion.tipo_servicio,
        )
        return None
    if not cotizacion.hora_inicio:
        logger.warning(
            "COT-%s: sin hora_inicio; no se puede calcular la hora límite de preparación", cotizacion.pk,
        )
        return None

    hora_entrada, requiere_tiempo_extra = calcular_horario_turnover(
        cotizacion.hora_inicio, plantilla.duracion_estimada_horas,
    )

    tarea, _creada = TareaProgramada.objects.get_or_create(
        plantilla=plantilla, cotizacion=cotizacion, fecha=cotizacion.fecha_evento,
        defaults={
            'responsable': plantilla.responsable_default,
            'hora_entrada': hora_entrada,
            'hora_limite': cotizacion.hora_inicio,
            'requiere_tiempo_extra': requiere_tiempo_extra,
        },
    )
    return tarea


def reubicar_tareas_turnover(cotizacion, fecha_anterior) -> int:
    """Tras un cambio de fecha aprobado: mueve a la fecha nueva las tareas de
    preparación de la cotización que siguen sin enviarse ni completarse (la hora
    límite y la de entrada salen de `hora_inicio`, no de la fecha, así que no se
    recalculan). Si no queda ninguna en la fecha nueva, genera la que falte con
    `generar_tarea_turnover`. Devuelve cuántas movió."""
    movidas = 0
    for tarea in TareaProgramada.objects.filter(
            cotizacion=cotizacion, fecha=fecha_anterior, estado_operativo='PENDIENTE', completada_en__isnull=True):
        if TareaProgramada.objects.filter(plantilla=tarea.plantilla, cotizacion=cotizacion,
                                          fecha=cotizacion.fecha_evento).exists():
            continue
        tarea.fecha = cotizacion.fecha_evento
        tarea.save(update_fields=['fecha'])
        movidas += 1
    if not TareaProgramada.objects.filter(cotizacion=cotizacion, fecha=cotizacion.fecha_evento).exists():
        generar_tarea_turnover(cotizacion)
    return movidas


def _corresponde_hoy(plantilla: PlantillaChecklist, fecha) -> bool:
    if plantilla.cadencia == 'DIARIA':
        return True
    if plantilla.cadencia == 'SEMANAL':
        return plantilla.dia_semana is not None and fecha.weekday() == plantilla.dia_semana
    if plantilla.cadencia == 'MENSUAL':
        return plantilla.dia_mes is not None and fecha.day == plantilla.dia_mes
    return False


def generar_tareas_mantenimiento(fecha) -> int:
    """Genera (idempotente) las tareas de mantenimiento recurrente que tocan `fecha`."""
    plantillas = PlantillaChecklist.objects.filter(tipo='MANTENIMIENTO_RECURRENTE', activa=True)
    creadas = 0
    for plantilla in plantillas:
        if not _corresponde_hoy(plantilla, fecha):
            continue
        hora_limite = plantilla.hora_limite_default or HORA_SALIDA_NORMAL
        _tarea, creada = TareaProgramada.objects.get_or_create(
            plantilla=plantilla, cotizacion=None, fecha=fecha,
            defaults={
                'responsable': plantilla.responsable_default,
                'hora_entrada': HORA_ENTRADA_NORMAL,
                'hora_limite': hora_limite,
                'requiere_tiempo_extra': False,
            },
        )
        if creada:
            creadas += 1
    return creadas


# ─────────────────────────── «Listo» del personal ───────────────────────────

def es_listo(texto: str) -> bool:
    return _normalizar(texto) in PALABRAS_LISTO


def marcar_listo(telefono: str, hoy=None, ahora=None):
    """«Listo» de un `nomina.Empleado` responsable: marca como completada su tarea
    de hoy con la hora límite más próxima. Devuelve el texto con que contestarle,
    o None si ese número no tiene tareas de hoy sin completar (el mensaje sigue
    el flujo normal)."""
    tel = normalizar_telefono_wa(telefono)
    if not tel:
        return None
    hoy = hoy or timezone.localdate()
    ahora = ahora or timezone.now()
    from nomina.models import Empleado
    empleados = [e.pk for e in Empleado.objects.exclude(telefono='') if normalizar_telefono_wa(e.telefono) == tel]
    if not empleados:
        return None
    with transaction.atomic():
        tarea = (TareaProgramada.objects.select_for_update().select_related('plantilla')
                 .filter(responsable__in=empleados, fecha=hoy, completada_en__isnull=True)
                 .order_by('hora_limite', 'id').first())
        if tarea is None:
            return None
        tarea.completada_en, tarea.completada_por_telefono = ahora, tel
        tarea.save(update_fields=['completada_en', 'completada_por_telefono'])
    return f'Listo, quedó marcada como terminada: {tarea.plantilla.titulo_mensaje()}. Gracias.'


def avisar_sin_listo(ahora: datetime) -> int:
    """Una tarea de hoy sin «listo» 60 min después de su hora límite abre un
    pendiente NORMAL para el encargado, una sola vez (clave por tarea)."""
    abiertos = 0
    for tarea in (TareaProgramada.objects.select_related('plantilla', 'responsable')
                  .filter(fecha=ahora.date(), completada_en__isnull=True)):
        if ahora < datetime.combine(tarea.fecha, tarea.hora_limite) + timedelta(minutes=MINUTOS_SIN_LISTO):
            continue
        quien = tarea.responsable.nombre if tarea.responsable else 'sin responsable'
        pendiente = registrar_pendiente(
            titulo=f'Tarea sin aviso de «listo»: {tarea.plantilla.titulo_mensaje()}', rol='ENCARGADO',
            tipo='OTRO', severidad='NORMAL', objeto=tarea,
            detalle=(f'{tarea.fecha:%d/%m/%Y}, hora límite {formato_hora_ampm(tarea.hora_limite)}, '
                     f'responsable: {quien}. Pasó más de una hora sin que respondiera «listo».'),
            clave_idempotencia=f'tarea:{tarea.pk}:sin_listo')
        abiertos += pendiente is not None
    return abiertos


# ─────────────────────────── Orquestación (cron) ────────────────────────────

def procesar_pendientes(ahora: datetime = None) -> dict:
    """
    Recorre las tareas con algo pendiente de enviar (incluye reintentos de
    FALLIDO) y dispara lo que ya esté en su horario. Pensado para correr cada
    10 minutos desde el cron — es la única función que llama el comando.
    """
    # Naive a propósito: fecha/hora_entrada/hora_limite son campos naive (hora
    # local de Mérida, sin zona), así que comparar contra localtime() sin tz
    # evita tener que aware-izar cada datetime calculado a partir de ellos.
    ahora = ahora or timezone.localtime().replace(tzinfo=None)
    contadores = {'aviso_horario': 0, 'operativo': 0}

    pendientes_aviso = TareaProgramada.objects.filter(
        requiere_tiempo_extra=True,
    ).exclude(estado_aviso_horario__in=['ENVIADO', 'NO_APLICA'])
    for tarea in pendientes_aviso:
        if enviar_aviso_horario(tarea) == 'ENVIADO':
            contadores['aviso_horario'] += 1

    pendientes_operativo = TareaProgramada.objects.exclude(estado_operativo='ENVIADO')
    for tarea in pendientes_operativo:
        if ahora >= tarea.hora_envio_operativo() and enviar_mensaje_operativo(tarea) == 'ENVIADO':
            contadores['operativo'] += 1

    contadores['sin_listo'] = avisar_sin_listo(ahora)
    return contadores
