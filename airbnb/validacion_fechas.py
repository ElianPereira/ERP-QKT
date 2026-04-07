"""
Servicio de validación de fechas bloqueadas
============================================
Verifica si una fecha está disponible considerando reservas de Airbnb.
"""
from datetime import date, timedelta
from typing import List, Tuple, Optional
from django.core.exceptions import ValidationError


def verificar_disponibilidad_fecha(fecha_evento: date, cotizacion_id: int = None) -> Tuple[bool, Optional[str]]:
    """
    Verifica si una fecha está disponible para eventos.
    
    Args:
        fecha_evento: La fecha a verificar
        cotizacion_id: ID de la cotización actual (para excluirla en ediciones)
    
    Returns:
        Tuple (disponible: bool, mensaje_error: str o None)
    """
    try:
        from airbnb.models import ReservaAirbnb
    except ImportError:
        # Si el módulo airbnb no está instalado, permitir siempre
        return True, None
    
    # Buscar reservas de Airbnb que afecten la quinta y coincidan con la fecha
    reservas_conflicto = ReservaAirbnb.objects.filter(
        anuncio__afecta_eventos_quinta=True,
        anuncio__activo=True,
        estado='CONFIRMADA',
        fecha_inicio__lte=fecha_evento,
        fecha_fin__gt=fecha_evento,  # fecha_fin es checkout, no incluye ese día
    ).select_related('anuncio')
    
    if reservas_conflicto.exists():
        reserva = reservas_conflicto.first()
        mensaje = (
            f"Fecha no disponible: {reserva.anuncio.nombre} "
            f"tiene reserva del {reserva.fecha_inicio.strftime('%d/%m/%Y')} "
            f"al {reserva.fecha_fin.strftime('%d/%m/%Y')}."
        )
        return False, mensaje

    # Cotizaciones ya apartadas (anticipo/confirmada/en preparación)
    try:
        from comercial.models import Cotizacion
        ESTADOS_APARTADO = ['ANTICIPO', 'CONFIRMADA', 'EN_PREPARACION']
        qs = Cotizacion.objects.filter(
            fecha_evento=fecha_evento,
            estado__in=ESTADOS_APARTADO,
        )
        if cotizacion_id:
            qs = qs.exclude(pk=cotizacion_id)
        if qs.exists():
            cot = qs.first()
            mensaje = (
                f"Fecha no disponible: ya existe un evento apartado para "
                f"{fecha_evento.strftime('%d/%m/%Y')} "
                f"({cot.get_estado_display()})."
            )
            return False, mensaje
    except Exception:
        pass

    return True, None


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
    
    # Cotizaciones apartadas en el rango
    try:
        from comercial.models import Cotizacion
        cots = Cotizacion.objects.filter(
            fecha_evento__gte=fecha_inicio,
            fecha_evento__lte=fecha_fin,
            estado__in=['ANTICIPO', 'CONFIRMADA', 'EN_PREPARACION'],
        )
        for c in cots:
            bloqueos.append({
                'fecha_inicio': c.fecha_evento,
                'fecha_fin': c.fecha_evento,
                'anuncio': 'Quinta Ko\'ox Tanil',
                'tipo': 'cotizacion',
                'titulo': f"Evento COT-{c.id:03d}",
            })
    except Exception:
        pass

    return bloqueos
