"""
Metas en cascada: ventas → marketing (Issue #402)
=================================================
**Cascada** (ida): utilidad objetivo → venta neta necesaria
(`EquilibrioService` con CF + utilidad) → ventas necesarias (÷ ticket
ponderado) → solicitudes necesarias (÷ tasa de conversión de la ventana de
promedio) → presupuesto de marketing sugerido (× costo por solicitud).
Si las ventas necesarias no caben en los días vendibles del mes, se avisa:
hay que subir ticket o cambiar la mezcla.

**Con topes capturados** (`MezclaOptimaService` en `modo='plan'`), `proponer_meta` toma el plan del
mes en vez de la cascada: unidades, venta neta y ocupación del plan. Sin topes, o sin precio, usa el
equilibrio. El presupuesto de marketing sigue saliendo de la cascada.

**Avance**: lo real del mes contra la `MetaMensual` activa. La proyección a fin
de mes usa **solo lo ya confirmado** para fechas del mes (las reservas se venden
por adelantado), no una línea recta por días transcurridos.
"""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, Optional

from reportes.services.operacion import OcupacionService

from ..models import MetaMensual, ParametrosNegocio
from .base import D0, TIPOS, dividir, mes_rango, porcentaje, q2, venta_neta, ventana_promedio, ventas_reales
from .embudo import EmbudoComercialService, gasto_marketing
from .equilibrio import EquilibrioService, _hacia_arriba
from .mezcla import MezclaOptimaService
from .unitarios import noches_habitacion


def _real_del_mes(anio: int, mes: int, hoy: date) -> Dict:
    inicio, fin = mes_rango(anio, mes)
    venta = realizado = D0
    unidades = {t: 0 for t in TIPOS}
    noches = 0   # habitaciones-noche
    for cot in ventas_reales(inicio, fin):
        neta = venta_neta(cot)
        venta += neta
        if cot.fecha_evento <= hoy:
            realizado += neta
        if cot.tipo_servicio in unidades:
            unidades[cot.tipo_servicio] += 1
        if cot.tipo_servicio == 'HOSPEDAJE':
            noches += noches_habitacion(cot)
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


def _campos_del_plan(plan_mezcla: dict) -> dict:
    """Los campos de `MetaMensual` que salen del plan de la mezcla óptima."""
    unidades = {f['tipo']: f['unidades'] for f in plan_mezcla['filas']}
    return {
        'meta_venta_neta': plan_mezcla['plan']['venta'], 'meta_ocupacion_pct': plan_mezcla['ocupacion'],
        'meta_eventos': unidades['EVENTO'], 'meta_pasadias': unidades['PASADIA'], 'meta_noches': unidades['HOSPEDAJE'],
    }


def valores_desde_plan(anio: int, mes: int, parametros: Optional[ParametrosNegocio] = None,
                       hoy: Optional[date] = None) -> Optional[dict]:
    """Venta, ocupación y unidades del plan de ese mes, o `None` si aún no hay plan (faltan topes o precios)."""
    mezcla = MezclaOptimaService.generar(anio, mes, parametros, hoy=hoy)
    if mezcla['modo'] != 'plan' or not any(f['unidades'] for f in mezcla['filas']):
        return None
    return _campos_del_plan(mezcla)


def proponer_meta(anio: int, mes: int, usuario=None, hoy: Optional[date] = None,
                  parametros: Optional[ParametrosNegocio] = None) -> MetaMensual:
    """
    Crea o actualiza la meta activa del mes (el propietario la ajusta después). Con el plan de la mezcla
    óptima (topes capturados) toma venta, ocupación y unidades de ahí; si no, de la cascada del equilibrio
    con utilidad objetivo y la mezcla real de la ventana de promedio. El presupuesto de marketing sale
    siempre de la cascada.
    """
    datos = MetasService.generar(anio, mes, parametros, hoy=hoy)
    del_plan = valores_desde_plan(anio, mes, parametros, hoy)
    if del_plan:
        campos, origen = del_plan, 'plan'
    else:
        campos, origen = _campos_del_equilibrio(datos), 'equilibrio'
    campos['presupuesto_marketing'] = datos['presupuesto_marketing'] or D0
    meta = MetaMensual.del_mes(anio, mes)
    if meta is None:
        meta = MetaMensual(anio=anio, mes=mes, created_by=usuario)
    for campo, valor in campos.items():
        setattr(meta, campo, valor)
    meta.updated_by = usuario
    meta.notas = (meta.notas + '\n' if meta.notas else '') + f'Propuesta desde {origen}.'
    meta.save()
    return meta


def _campos_del_equilibrio(datos: dict) -> dict:
    """Metas por la cascada: ventas repartidas según la mezcla real de la ventana de promedio."""
    tipos = datos['equilibrio']['economia']['tipos']
    total = sum(t['ventas'] for t in tipos.values())
    ventas = datos['ventas_necesarias'] or 0

    def reparto(tipo):
        if not total:
            return 0
        return int((Decimal(ventas) * tipos[tipo]['ventas'] / total).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

    hospedaje = tipos['HOSPEDAJE']
    noches = 0
    if hospedaje['ventas']:
        noches = int((Decimal(reparto('HOSPEDAJE')) * hospedaje['noches_habitacion'] / hospedaje['ventas']).quantize(
            Decimal('1'), rounding=ROUND_HALF_UP))
    ocupacion = datos['objetivo']['ocupacion_pct']
    return {
        'meta_venta_neta': datos['venta_necesaria'] or D0,
        'meta_ocupacion_pct': min(ocupacion, Decimal('100')) if ocupacion is not None else D0,
        'meta_eventos': reparto('EVENTO'), 'meta_pasadias': reparto('PASADIA'), 'meta_noches': noches,
    }
