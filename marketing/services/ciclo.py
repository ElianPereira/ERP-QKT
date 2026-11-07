"""Ciclo de venta medido con el histórico.

Ciclo de una venta = días entre su primer contacto y su primer pago. Primer contacto = lo más antiguo entre
la creación de la cotización y el primer contacto de un prospecto ligado a ella (los prospectos solo existen
desde jul/2026; la cotización da más historia). Primer pago = el `Pago` de ingreso de la venta más antiguo,
sin condonaciones (una cortesía no es cobro).

Entran las ventas reales (CONFIRMADA/EJECUTADA/CERRADA) del servicio, sin las importadas del sistema anterior
(`es_historica`: sus fechas no son las reales). Se toma el **percentil 80** (rango más cercano), no el
promedio: el ciclo decide cuándo una cohorte ya puede juzgarse, y con el promedio se juzgaría antes de que
llegara la mitad de las ventas lentas. Con menos de `MUESTRA_MINIMA` ventas no hay cifra (vale el manual).
"""
from math import ceil
from typing import Dict, Optional

from django.db.models import Min, Q
from django.utils import timezone

ESTADOS_GANADA = ('CONFIRMADA', 'EJECUTADA', 'CERRADA')
MUESTRA_MINIMA = 5
PERCENTIL = 80


def ciclo_historico(tipo: str) -> Dict[str, Optional[int]]:
    """`{'dias': int|None, 'muestra': int}` del servicio `tipo` (EVENTO, PASADIA u HOSPEDAJE)."""
    from comercial.models import Cotizacion

    filas = (Cotizacion.objects
             .filter(tipo_servicio=tipo, estado__in=ESTADOS_GANADA, es_historica=False)
             .annotate(primer_pago=Min('pagos__fecha_pago',
                                       filter=Q(pagos__tipo='INGRESO', pagos__concepto='VENTA')
                                       & ~Q(pagos__metodo='CONDONACION')),
                       primer_prospecto=Min('prospectos__fecha_primer_contacto'))
             .filter(primer_pago__isnull=False)
             .values_list('created_at', 'primer_prospecto', 'primer_pago'))
    dias = []
    for creada, prospecto, pago in filas:
        inicio = min(x for x in (creada, prospecto) if x is not None)
        dias.append(max(0, (pago - timezone.localtime(inicio).date()).days))
    if len(dias) < MUESTRA_MINIMA:
        return {'dias': None, 'muestra': len(dias)}
    dias.sort()
    return {'dias': dias[ceil(len(dias) * PERCENTIL / 100) - 1], 'muestra': len(dias)}
