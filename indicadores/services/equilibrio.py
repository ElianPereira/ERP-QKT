"""
Punto de equilibrio y ocupación objetivo (Issue #402)
=====================================================
- **MC % ponderado** con la mezcla real de la ventana de promedio (unidades vendidas: ventas de
  evento y pasadía, habitaciones-noche de hospedaje): Σ(unidades × MC unitario) / Σ(unidades ×
  ticket). El ticket y el MC unitario salen de `servicio.economia_servicio`. El ticket
  ponderado de la cascada de metas, en cambio, es por venta (reservación), no por habitación-noche.
- **Equilibrio en pesos** = CF / MC %.
- **Equilibrio en días ocupados** = CF / MC por día ponderado (Σ MC / Σ días);
  **ocupación de equilibrio** = esos días / días vendibles del mes.
- **Unidades «solo X»**: CF / MC unitario de cada servicio (redondeado hacia arriba), y los días
  que ocupan.
- Con `utilidad_mensual_objetivo`: el mismo cálculo con (CF + utilidad) → ocupación objetivo.
- Si el margen ponderado es ≤ 0 no se divide: se devuelve `None` y un aviso.
"""
from datetime import date
from decimal import Decimal
from typing import Dict, Optional

from ..models import ParametrosNegocio
from .base import D0, ETIQUETAS, TIPOS, corte, dividir, mes_rango, porcentaje, q2, q4, ventana_promedio
from .capacidad import CapacidadService
from .costos_fijos import CostosFijosService
from .servicio import economia_servicios
from .unitarios import EconomiaUnitariaService


def _hacia_arriba(valor: Optional[Decimal]) -> Optional[int]:
    if valor is None:
        return None
    entero = int(valor)
    return entero if Decimal(entero) == valor else entero + 1


def punto_equilibrio(objetivo: Decimal, economia: dict, servicios: dict, dias_vendibles: int) -> Dict:
    """Cobertura de `objetivo` (CF o CF + utilidad) con el margen ponderado de la mezcla real de `economia`."""
    ventas = ingresos = mc_total = dias = D0
    for tipo, e in economia['tipos'].items():
        u = servicios[tipo]
        if u['ticket'] is None or e['ventas'] == 0:
            continue
        unidades = e['noches_habitacion'] if tipo == 'HOSPEDAJE' else e['ventas']
        ventas += e['ventas']
        ingresos += unidades * u['ticket']
        mc_total += unidades * u['mc']
        dias += e['dias']
    mc_pct = dividir(mc_total, ingresos, cuantizar=q4)
    mc_dia = dividir(mc_total, dias)
    pesos = dividir(objetivo, mc_pct) if mc_pct else None
    dias_eq = dividir(objetivo, mc_dia) if mc_dia else None
    return {
        'objetivo': q2(objetivo), 'ventas_base': int(ventas), 'mc_pct': mc_pct,
        'mc_dia': mc_dia, 'pesos': pesos, 'dias': dias_eq,
        'ocupacion_pct': porcentaje(dias_eq, dias_vendibles) if dias_eq is not None and dias_vendibles else None,
        'ticket_ponderado': dividir(ingresos, ventas),
        'dias_por_unidad_ponderado': dividir(dias, ventas, cuantizar=q4),
    }


class EquilibrioService:

    @classmethod
    def generar(cls, anio: int, mes: int, parametros: Optional[ParametrosNegocio] = None,
                overrides: Optional[dict] = None, hoy: Optional[date] = None,
                economia: Optional[dict] = None, costos: Optional[dict] = None,
                capacidad: Optional[dict] = None) -> Dict:
        from django.utils import timezone

        p = parametros or ParametrosNegocio.vigente()
        hoy = hoy or timezone.localdate()
        inicio, fin = mes_rango(anio, mes)
        economia = economia or EconomiaUnitariaService.generar(*ventana_promedio(anio, mes, p), parametros=p)
        servicios = economia_servicios(economia, p, overrides)
        costos = costos or CostosFijosService.generar(corte(anio, mes, hoy), p)
        capacidad = capacidad or CapacidadService.generar(inicio, fin, p, economia, overrides)

        cf = costos['cf']
        vendibles = capacidad['dias_vendibles']
        base = punto_equilibrio(cf, economia, servicios, vendibles)
        objetivo = punto_equilibrio(cf + q2(p.utilidad_mensual_objetivo), economia, servicios, vendibles)

        avisos = list(costos['avisos'])
        for u in servicios.values():
            avisos.extend(u['avisos'])
        if base['pesos'] is None:
            avisos.append('No se puede calcular el equilibrio: no hay ventas con margen de contribución '
                          'positivo en la ventana de promedio.')
        elif base['ocupacion_pct'] is not None and base['ocupacion_pct'] > 100:
            avisos.append('El equilibrio supera el 100% de ocupación: con el ticket actual no alcanza el mes.')

        solo = {}
        for tipo in TIPOS:
            u = servicios[tipo]
            mc = u['mc']
            unidades = dividir(cf, mc, cuantizar=lambda x: x) if mc is not None and mc > 0 else None
            solo[tipo] = {
                'nombre': ETIQUETAS[tipo], 'unidades': _hacia_arriba(unidades),
                'dias': _hacia_arriba(dividir(cf, u['mc_dia'], cuantizar=lambda x: x)) if u['mc_dia'] else None,
                'mc': mc, 'fuente_costo': u['fuente_costo'],
            }
        return {
            'anio': anio, 'mes': mes, 'cf': cf, 'fuente_cf': costos['fuente'], 'costos': costos,
            'base': base, 'objetivo': objetivo, 'utilidad_objetivo': q2(p.utilidad_mensual_objetivo),
            'solo': solo, 'dias_vendibles': vendibles, 'economia': economia, 'servicios': servicios,
            'capacidad': capacidad,
            'avisos': avisos,
        }
