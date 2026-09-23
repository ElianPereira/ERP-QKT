"""
Disponibilidad de fechas de la Quinta
=====================================
Verifica si un rango de fechas está libre cruzando las Cotizacion ya
apartadas (Evento, Pasadía, Arrendamiento, Hospedaje).

Todo se expresa como [fecha_inicio, fecha_fin) — fecha_fin EXCLUSIVA (el
checkout no cuenta como noche ocupada). Un servicio de un solo día
(Evento/Pasadía/Arrendamiento) es el rango [fecha, fecha + 1 día); Hospedaje
usa su rango real de noches. `Cotizacion.rango_ocupado()` es la fuente única
de esa conversión.
"""
from datetime import date, timedelta
from typing import List, Optional, Tuple

from django.db.models import Q


def _cotizaciones_apartadas(fecha_inicio: date, fecha_fin: date):
    """Cotizaciones CONFIRMADA cuyo rango ocupado traslapa [fecha_inicio, fecha_fin).

    c.rango_ocupado() = [fecha_evento, fecha_salida o fecha_evento+1día); traslapa
    si fecha_evento < fecha_fin y (fecha_salida > fecha_inicio, o sin
    fecha_salida y fecha_evento >= fecha_inicio). Una BORRADOR/COTIZADA nunca
    bloquea: solo apartar la fecha (CONFIRMADA) lo hace."""
    from .models import Cotizacion

    return Cotizacion.objects.filter(
        estado='CONFIRMADA',
        fecha_evento__lt=fecha_fin,
    ).filter(
        Q(fecha_salida__gt=fecha_inicio)
        | Q(fecha_salida__isnull=True, fecha_evento__gte=fecha_inicio)
    )


def verificar_disponibilidad_rango(
    fecha_inicio: date, fecha_fin: date, cotizacion_id: int = None,
) -> Tuple[bool, Optional[str]]:
    """
    Verifica si el rango [fecha_inicio, fecha_fin) está disponible contra
    cualquier otra Cotizacion CONFIRMADA (de cualquier tipo de servicio).

    Returns:
        Tuple (disponible: bool, mensaje_error: str o None)
    """
    qs = _cotizaciones_apartadas(fecha_inicio, fecha_fin)
    if cotizacion_id:
        qs = qs.exclude(pk=cotizacion_id)
    cot = qs.first()
    if not cot:
        return True, None

    inicio_cot, fin_cot = cot.rango_ocupado()
    if cot.tipo_servicio == 'HOSPEDAJE':
        return False, (
            f"Fechas no disponibles: ya hay un Hospedaje confirmado del "
            f"{inicio_cot.strftime('%d/%m/%Y')} al {fin_cot.strftime('%d/%m/%Y')} "
            f"({cot.get_estado_display()})."
        )
    return False, (
        f"Fechas no disponibles: ya existe un {cot.get_tipo_servicio_display().lower()} "
        f"apartado para {cot.fecha_evento.strftime('%d/%m/%Y')} "
        f"({cot.get_estado_display()})."
    )


def verificar_disponibilidad_fecha(fecha_evento: date, cotizacion_id: int = None) -> Tuple[bool, Optional[str]]:
    """Verifica si un solo día está disponible (Evento/Pasadía/Arrendamiento)."""
    return verificar_disponibilidad_rango(
        fecha_evento, fecha_evento + timedelta(days=1), cotizacion_id,
    )


def verificar_disponibilidad_hospedaje(
    fecha_entrada: date, fecha_salida: date, cotizacion_id: int = None,
) -> Tuple[bool, Optional[str]]:
    """Verifica disponibilidad para una estancia de Hospedaje de varias noches."""
    return verificar_disponibilidad_rango(fecha_entrada, fecha_salida, cotizacion_id)


def obtener_fechas_bloqueadas(fecha_inicio: date, fecha_fin: date) -> List[dict]:
    """
    Bloqueos (cotizaciones apartadas) que traslapan [fecha_inicio, fecha_fin),
    cada uno con su rango real — `fecha_fin` de cada bloqueo es EXCLUSIVA.
    """
    bloqueos = []
    for c in _cotizaciones_apartadas(fecha_inicio, fecha_fin):
        inicio_c, fin_c = c.rango_ocupado()
        bloqueos.append({
            'fecha_inicio': inicio_c,
            'fecha_fin': fin_c,
            'titulo': (
                f"Hospedaje COT-{c.id:03d}" if c.tipo_servicio == 'HOSPEDAJE'
                else f"Evento COT-{c.id:03d}"
            ),
        })
    return bloqueos
