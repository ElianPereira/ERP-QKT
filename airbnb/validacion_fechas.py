"""
Servicio de validación de fechas bloqueadas
============================================
Verifica si un rango de fechas está disponible, cruzando reservas de Airbnb
y otras Cotizacion (Evento, Pasadía, Arrendamiento, Hospedaje).

Todo se expresa como [fecha_inicio, fecha_fin) — fecha_fin EXCLUSIVA, mismo
criterio que `ReservaAirbnb.fecha_fin` (el checkout no cuenta como noche
ocupada). Un servicio de un solo día (Evento/Pasadía/Arrendamiento) es
simplemente el rango [fecha, fecha + 1 día); Hospedaje usa su rango real de
noches. `Cotizacion.rango_ocupado()` es la fuente única de esa conversión.
"""
from datetime import date, timedelta
from typing import List, Optional, Tuple

from django.core.exceptions import ValidationError
from django.db.models import Q


def verificar_disponibilidad_rango(
    fecha_inicio: date, fecha_fin: date, cotizacion_id: int = None,
) -> Tuple[bool, Optional[str]]:
    """
    Verifica si el rango [fecha_inicio, fecha_fin) está disponible.

    Cruza contra reservas de Airbnb que afectan la quinta y contra cualquier
    otra Cotizacion CONFIRMADA (de cualquier tipo de servicio) cuyo rango se
    traslape. Una Cotizacion BORRADOR/COTIZADA nunca bloquea — solo apartar
    la fecha (CONFIRMADA) lo hace, igual que siempre.

    Returns:
        Tuple (disponible: bool, mensaje_error: str o None)
    """
    try:
        from airbnb.models import ReservaAirbnb
    except ImportError:
        # Si el módulo airbnb no está instalado, permitir siempre
        return True, None

    reservas_conflicto = ReservaAirbnb.objects.filter(
        anuncio__afecta_eventos_quinta=True,
        anuncio__activo=True,
        estado='CONFIRMADA',
        fecha_inicio__lt=fecha_fin,
        fecha_fin__gt=fecha_inicio,
    ).select_related('anuncio')

    reserva = reservas_conflicto.first()
    if reserva:
        mensaje = (
            f"Fechas no disponibles: {reserva.anuncio.nombre} "
            f"tiene reserva del {reserva.fecha_inicio.strftime('%d/%m/%Y')} "
            f"al {reserva.fecha_fin.strftime('%d/%m/%Y')}."
        )
        return False, mensaje

    try:
        from comercial.models import Cotizacion
        # Overlap de rangos exacto: c.rango_ocupado() = [fecha_evento, fecha_salida
        # o fecha_evento+1día). [fecha_inicio,fecha_fin) traslapa con ese rango si
        # fecha_evento < fecha_fin (el candidato empieza antes de que acabe lo pedido)
        # y (fecha_salida > fecha_inicio, o sin fecha_salida y fecha_evento >= fecha_inicio
        # — un día empieza en o después de fecha_inicio).
        qs = Cotizacion.objects.filter(
            estado='CONFIRMADA',
            fecha_evento__lt=fecha_fin,
        ).filter(
            Q(fecha_salida__gt=fecha_inicio)
            | Q(fecha_salida__isnull=True, fecha_evento__gte=fecha_inicio)
        )
        if cotizacion_id:
            qs = qs.exclude(pk=cotizacion_id)
        cot = qs.first()
        if cot:
            inicio_cot, fin_cot = cot.rango_ocupado()
            if cot.tipo_servicio == 'HOSPEDAJE':
                mensaje = (
                    f"Fechas no disponibles: ya hay un Hospedaje confirmado del "
                    f"{inicio_cot.strftime('%d/%m/%Y')} al {fin_cot.strftime('%d/%m/%Y')} "
                    f"({cot.get_estado_display()})."
                )
            else:
                mensaje = (
                    f"Fechas no disponibles: ya existe un {cot.get_tipo_servicio_display().lower()} "
                    f"apartado para {cot.fecha_evento.strftime('%d/%m/%Y')} "
                    f"({cot.get_estado_display()})."
                )
            return False, mensaje
    except Exception:
        pass

    return True, None


def verificar_disponibilidad_fecha(fecha_evento: date, cotizacion_id: int = None) -> Tuple[bool, Optional[str]]:
    """
    Verifica si un solo día está disponible (Evento/Pasadía/Arrendamiento).

    Wrapper delgado de `verificar_disponibilidad_rango` para el caso de un
    servicio de un solo día — firma y mensajes de error sin cambios respecto
    a como funcionaba antes de que existiera Hospedaje.
    """
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
    Obtiene todas las fechas bloqueadas en un rango.

    Returns:
        Lista de diccionarios con info de cada bloqueo
    """
    try:
        from airbnb.models import ReservaAirbnb
    except ImportError:
        return []

    reservas = ReservaAirbnb.objects.filter(
        anuncio__afecta_eventos_quinta=True,
        anuncio__activo=True,
        estado='CONFIRMADA',
        fecha_inicio__lte=fecha_fin,
        fecha_fin__gte=fecha_inicio,
    ).select_related('anuncio')

    bloqueos = []
    for reserva in reservas:
        bloqueos.append({
            'fecha_inicio': reserva.fecha_inicio,
            'fecha_fin': reserva.fecha_fin,
            'anuncio': reserva.anuncio.nombre,
            'tipo': 'airbnb',
            'titulo': reserva.titulo or 'Reserva Airbnb',
        })

    # Cotizaciones apartadas en el rango — cada una con su rango real (un solo
    # día para Evento/Pasadía/Arrendamiento, varias noches para Hospedaje).
    try:
        from comercial.models import Cotizacion
        cots = Cotizacion.objects.filter(
            estado='CONFIRMADA',
            fecha_evento__lt=fecha_fin,
        ).filter(
            Q(fecha_salida__gt=fecha_inicio)
            | Q(fecha_salida__isnull=True, fecha_evento__gte=fecha_inicio)
        )
        for c in cots:
            inicio_c, fin_c = c.rango_ocupado()
            bloqueos.append({
                'fecha_inicio': inicio_c,
                'fecha_fin': fin_c,
                'anuncio': 'Quinta Ko\'ox Tanil',
                'tipo': 'cotizacion',
                'titulo': (
                    f"Hospedaje COT-{c.id:03d}" if c.tipo_servicio == 'HOSPEDAJE'
                    else f"Evento COT-{c.id:03d}"
                ),
            })
    except Exception:
        pass

    return bloqueos
