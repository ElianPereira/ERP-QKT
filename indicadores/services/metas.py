"""
Metas en cascada: ventas → marketing (Issue #402)
=================================================
**Cascada** (ida): utilidad objetivo → venta neta necesaria
(`EquilibrioService` con CF + utilidad) → ventas necesarias (÷ ticket
ponderado) → solicitudes necesarias (÷ tasa de conversión de la ventana de
promedio) → presupuesto de marketing sugerido (× costo por solicitud).
Si las ventas necesarias no caben en los días vendibles del mes, se avisa:
hay que subir ticket o cambiar la mezcla.

**Avance**: lo real del mes contra la `MetaMensual` activa. La proyección a fin
de mes usa **solo lo ya confirmado** para fechas del mes (las reservas se venden
por adelantado), no una línea recta por días transcurridos.
"""
from datetime import date
from decimal import Decimal
from typing import Dict, Optional

from reportes.services.operacion import OcupacionService

from ..models import MetaMensual, ParametrosNegocio
from .base import D0, TIPOS, dividir, mes_rango, porcentaje, q2, venta_neta, ventana_promedio, ventas_reales
from .embudo import EmbudoComercialService, gasto_marketing
from .equilibrio import EquilibrioService, _hacia_arriba


def _real_del_mes(anio: int, mes: int, hoy: date) -> Dict:
    inicio, fin = mes_rango(anio, mes)
    venta = realizado = D0
    unidades = {t: 0 for t in TIPOS}
    noches = 0
    for cot in ventas_reales(inicio, fin):
        neta = venta_neta(cot)
        venta += neta
        if cot.fecha_evento <= hoy:
            realizado += neta
        if cot.tipo_servicio in unidades:
            unidades[cot.tipo_servicio] += 1
        if cot.tipo_servicio == 'HOSPEDAJE':
            noches += cot.noches or 1
    return {
        'venta_neta': q2(venta), 'venta_realizada': q2(realizado),
        'ocupacion': OcupacionService.generar(inicio, fin)['ocupacion'],
        'eventos': unidades['EVENTO'], 'pasadias': unidades['PASADIA'], 'noches': noches,
        'gasto_marketing': gasto_marketing(inicio, fin),
    }


def _avance(real, meta) -> Dict:
    return {'real': real, 'meta': meta, 'pct': porcentaje(real, meta) if meta else None}


class MetasService:

    @classmethod
    def generar(cls, anio: int, mes: int, parametros: Optional[ParametrosNegocio] = None,
                overrides: Optional[dict] = None, hoy: Optional[date] = None,
                equilibrio: Optional[dict] = None) -> Dict:
        from django.utils import timezone

        p = parametros or ParametrosNegocio.vigente()
        hoy = hoy or timezone.localdate()
        eq = equilibrio or EquilibrioService.generar(anio, mes, p, overrides, hoy)
        objetivo, avisos = eq['objetivo'], list(eq['avisos'])

        desde, hasta = ventana_promedio(anio, mes, p)
        embudo = EmbudoComercialService.generar(desde, min(hasta, hoy), whatsapp=False)
        conversion = dividir(embudo['total']['ventas'], embudo['total']['solicitudes'], cuantizar=lambda x: x)

        venta_necesaria = objetivo['pesos']
        ticket = objetivo['ticket_ponderado']
        ventas = _hacia_arriba(dividir(venta_necesaria, ticket, cuantizar=lambda x: x)) if ticket else None
        solicitudes = _hacia_arriba(dividir(ventas, conversion, cuantizar=lambda x: x)) if ventas and conversion else None
        costo_solicitud = embudo['costo_por_solicitud']
        presupuesto = q2(solicitudes * costo_solicitud) if solicitudes and costo_solicitud else None

        if venta_necesaria is None:
            avisos.append('No se puede armar la cascada: falta margen de contribución positivo.')
        elif solicitudes is None:
            avisos.append('Sin solicitudes ni ventas en la ventana de promedio: no hay tasa de conversión.')
        if ventas and objetivo['dias_por_unidad_ponderado'] and eq['dias_vendibles']:
            dias = Decimal(ventas) * objetivo['dias_por_unidad_ponderado']
            if dias > eq['dias_vendibles']:
                avisos.append(
                    f'La meta no cabe en la capacidad: requiere {q2(dias)} días y el mes tiene '
                    f'{eq["dias_vendibles"]} vendibles. Hay que subir el ticket o cambiar la mezcla.')

        meta = MetaMensual.del_mes(anio, mes)
        real = _real_del_mes(anio, mes, hoy)
        avance = None
        if meta:
            avance = {
                'venta_neta': _avance(real['venta_neta'], meta.meta_venta_neta),
                'ocupacion': _avance(real['ocupacion'], meta.meta_ocupacion_pct),
                'eventos': _avance(real['eventos'], meta.meta_eventos),
                'pasadias': _avance(real['pasadias'], meta.meta_pasadias),
                'noches': _avance(real['noches'], meta.meta_noches),
                'marketing': _avance(real['gasto_marketing'], meta.presupuesto_marketing),
            }
        return {
            'anio': anio, 'mes': mes, 'objetivo': objetivo, 'venta_necesaria': venta_necesaria,
            'ventas_necesarias': ventas, 'ticket_ponderado': ticket, 'tasa_conversion': (
                porcentaje(embudo['total']['ventas'], embudo['total']['solicitudes'])),
            'solicitudes_necesarias': solicitudes, 'costo_por_solicitud': costo_solicitud,
            'presupuesto_marketing': presupuesto, 'embudo': embudo, 'meta': meta, 'real': real,
            'avance': avance, 'equilibrio': eq, 'avisos': avisos,
            'proyeccion': real['venta_neta'],  # solo lo confirmado para fechas del mes
        }
