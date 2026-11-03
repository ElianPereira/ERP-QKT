"""Sugerencias de contenido para el resumen del lunes (Issue #468, fase 7). Reglas, sin IA.

Qué promover según los fines de semana libres de los próximos 45 días, la temporada que se acerca y el servicio
prioritario (eventos sociales). Hasta 3 sugerencias, las más cercanas primero.
"""
from datetime import date, timedelta
from typing import Optional

from django.utils import timezone

from comercial.models import Temporada

from .decisiones import agenda

HORIZONTE_DIAS = 45
MAXIMO = 3
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre',
         'noviembre', 'diciembre']


def _fecha(d: date) -> str:
    return f'{d.day} de {MESES[d.month - 1]}'


def sugerencias(hoy: Optional[date] = None, maximo: int = MAXIMO) -> list:
    """Lista de dicts `{fecha, servicio, texto}` ordenada por cercanía."""
    hoy = hoy or timezone.localdate()
    inicio, fin = hoy + timedelta(days=1), hoy + timedelta(days=HORIZONTE_DIAS)
    libres = set(agenda(inicio, fin)['libres'])
    resultado = []
    for dia in sorted(d for d in libres if d.weekday() == 5):  # sábados libres: evento social (prioridad)
        domingo = dia + timedelta(days=1)
        complemento = f' y {_fecha(domingo)}' if domingo in libres else ''
        resultado.append({'fecha': dia, 'servicio': 'EVENTO',
                          'texto': f'Eventos sociales: sábado {_fecha(dia)}{complemento} libre'})
    for dia in sorted(d for d in libres if d.weekday() == 6 and d - timedelta(days=1) not in libres):
        resultado.append({'fecha': dia, 'servicio': 'PASADIA',
                          'texto': f'Pasadía: domingo {_fecha(dia)} libre (el sábado ya está ocupado)'})
    for t in Temporada.objects.filter(activo=True, fecha_fin__gte=hoy, fecha_inicio__lte=fin).order_by('fecha_inicio'):
        desde = max(t.fecha_inicio, hoy)
        resultado.append({'fecha': desde, 'servicio': 'EVENTO',
                          'texto': f'Temporada «{t.nombre}» ({_fecha(t.fecha_inicio)} al {_fecha(t.fecha_fin)}): '
                                   'contenido de eventos sociales'})
    resultado.sort(key=lambda s: (s['fecha'], 0 if s['servicio'] == 'EVENTO' else 1))
    vistos, final = set(), []
    for s in resultado:
        if s['texto'] not in vistos:
            vistos.add(s['texto'])
            final.append(s)
    return final[:maximo]


def lineas_resumen(hoy: Optional[date] = None) -> list:
    return [f'Qué promover: {s["texto"]}' for s in sugerencias(hoy)]
