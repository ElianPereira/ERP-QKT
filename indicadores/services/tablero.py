"""
Tablero de Indicadores (Issue #402, fase 3)
===========================================
Compone los services del módulo para un mes: se calcula una sola vez la
economía unitaria, los costos fijos y la capacidad, y se reparten entre
equilibrio, plan anual y metas. La vista solo pinta lo que este service devuelve.

Cada KPI lleva un tono: **error** bajo el equilibrio, **alerta** entre el
equilibrio y la meta (o el objetivo), **exito** sobre la meta.
"""
from datetime import date
from typing import Dict, Optional

from reportes.services.operacion import OcupacionService

from ..models import ParametrosNegocio
from .base import (
    D0,
    corte,
    dividir,
    mes_anterior,
    mes_rango,
    porcentaje,
    q2,
    venta_neta,
    ventana_promedio,
    ventas_reales,
)
from .capacidad import CapacidadService
from .costos_fijos import CostosFijosService
from .embudo import EmbudoComercialService
from .equilibrio import EquilibrioService, _hacia_arriba
from .metas import MetasService
from .plan_anual import PlanAnualService
from .unitarios import EconomiaUnitariaService

EXITO, ALERTA, ERROR, NEUTRO = 'exito', 'alerta', 'error', 'neutro'
MESES_TENDENCIA = 12
MESES = ('Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre',
         'Noviembre', 'Diciembre')


def _tono(valor, equilibrio, meta) -> str:
    """error bajo el equilibrio, alerta entre equilibrio y meta, exito sobre la meta."""
    if valor is None or equilibrio is None:
        return NEUTRO
    if valor < equilibrio:
        return ERROR
    if meta is not None and valor < meta:
        return ALERTA
    return EXITO


def _tendencia(anio: int, mes: int) -> list:
    meses, a, m = [], anio, mes
    for _ in range(MESES_TENDENCIA):
        meses.append((a, m))
        a, m = mes_anterior(a, m)
    filas = []
    for a, m in reversed(meses):
        inicio, fin = mes_rango(a, m)
        venta = sum((venta_neta(c) for c in ventas_reales(inicio, fin)), D0)
        filas.append({'etiqueta': f'{m:02d}/{a}', 'venta_neta': q2(venta),
                      'ocupacion': OcupacionService.generar(inicio, fin)['ocupacion']})
    return filas


class TableroService:

    @classmethod
    def generar(cls, anio: int, mes: int, overrides: Optional[dict] = None, hoy: Optional[date] = None,
                parametros: Optional[ParametrosNegocio] = None) -> Dict:
        from django.utils import timezone

        p = parametros or ParametrosNegocio.vigente()
        hoy = hoy or timezone.localdate()
        overrides = overrides or {}
        inicio, fin = mes_rango(anio, mes)

        economia = EconomiaUnitariaService.generar(*ventana_promedio(anio, mes, p), parametros=p)
        costos = CostosFijosService.generar(corte(anio, mes, hoy), p)
        capacidad = CapacidadService.generar(inicio, fin, p, economia, overrides)
        equilibrio = EquilibrioService.generar(
            anio, mes, p, overrides, hoy, economia=economia, costos=costos, capacidad=capacidad)
        plan = PlanAnualService.del_mes(anio, mes, p, hoy, economia=economia, costos=costos, overrides=overrides)
        metas = MetasService.generar(anio, mes, p, overrides, hoy, equilibrio=equilibrio)
        embudo = EmbudoComercialService.generar(inicio, fin)
        ocupacion = OcupacionService.generar(inicio, fin)

        avisos = list(dict.fromkeys(
            equilibrio['avisos'] + economia['avisos'] + capacidad['avisos'] + metas['avisos']))
        resumen = cls._resumen(equilibrio, metas, embudo, ocupacion, plan)
        return {
            'anio': anio, 'inicio': inicio, 'fin': fin, 'hoy': hoy, 'parametros': p,
            'ventana': ventana_promedio(anio, mes, p), 'overrides': overrides,
            'economia': economia, 'costos': costos, 'capacidad': capacidad, 'equilibrio': equilibrio,
            'plan': plan, 'metas': metas, 'embudo': embudo, 'ocupacion': ocupacion,
            'resumen': resumen, 'avisos': avisos,
            'mes': cls._mes(equilibrio, metas, plan, ocupacion, anio, mes),
            'tendencia': _tendencia(anio, mes),
            'equilibrio_pesos': equilibrio['base']['pesos'],
            'equilibrio_ocupacion': equilibrio['base']['ocupacion_pct'],
        }

    @staticmethod
    def _mes(eq, metas, plan, ocupacion, anio: int = 0, mes: int = 1) -> dict:
        """Lo que muestra «Este mes»: venta contra meta, utilidad proyectada con lo confirmado, equilibrio y ocupación."""
        base = eq['base']
        venta = metas['real']['venta_neta']
        con_plan = not plan['plan']['vacio']
        meta = metas['meta']
        if meta and meta.meta_venta_neta:
            meta_venta, fuente = meta.meta_venta_neta, 'meta'
        elif con_plan:
            meta_venta, fuente = plan['fila']['venta'], 'plan'
        elif base['pesos'] is not None:
            meta_venta, fuente = base['pesos'], 'equilibrio'
        else:
            meta_venta, fuente = None, None

        pct = porcentaje(venta, meta_venta) if meta_venta else None
        if pct is None:
            tono = NEUTRO
        else:
            tono = EXITO if pct >= 100 else (ALERTA if pct > 0 else ERROR)
        dias_eq = _hacia_arriba(base['dias'])
        dias_vendidos = ocupacion['dias_ocupados']
        if con_plan:
            ocupacion_plan = plan['fila']['ocupacion']
        else:
            ocupacion_plan = meta.meta_ocupacion_pct if meta else None
        return {
            'etiqueta': f'{MESES[mes - 1]} {anio}',
            'venta': venta, 'venta_realizada': metas['real']['venta_realizada'],
            'meta_venta': meta_venta, 'fuente_meta': fuente,
            'pct_meta': pct, 'pct_barra': int(min(max(pct, 0), 100)) if pct is not None else 0, 'tono_meta': tono,
            'cf': eq['cf'],
            'utilidad': q2(venta * base['mc_pct'] - eq['cf']) if base['mc_pct'] is not None else None,
            'falta_cf': max(D0, q2(base['pesos'] - venta)) if base['pesos'] is not None else None,
            'dias_equilibrio': dias_eq, 'dias_vendidos': dias_vendidos,
            'dias_faltan': max(0, dias_eq - dias_vendidos) if dias_eq is not None else None,
            'dias_vendibles': eq['dias_vendibles'], 'ocupacion': ocupacion['ocupacion'],
            'ocupacion_plan': ocupacion_plan,
        }

    @staticmethod
    def _resumen(eq, metas, embudo, ocupacion, plan) -> list:
        base, objetivo, meta = eq['base'], eq['objetivo'], metas['meta']
        venta = metas['real']['venta_neta']
        meta_venta = meta.meta_venta_neta if meta and meta.meta_venta_neta else objetivo['pesos']
        meta_ocupacion = meta.meta_ocupacion_pct if meta and meta.meta_ocupacion_pct else objetivo['ocupacion_pct']
        vendibles = eq['dias_vendibles']

        utilidad, etiqueta_utilidad, sub_utilidad, estimada = None, 'Utilidad estimada', 'Venta × margen − costos fijos', True
        if not plan['plan']['vacio']:
            utilidad, estimada, etiqueta_utilidad = plan['fila']['utilidad'], False, 'Utilidad del plan'
            sub_utilidad = f'Plan óptimo · objetivo ${eq["utilidad_objetivo"]:,.2f}'
        elif base['mc_pct'] is not None:
            utilidad = q2(venta * base['mc_pct'] - eq['cf'])

        conversion = embudo['total']
        return [
            {'etiqueta': 'Venta neta del mes', 'tipo': 'moneda', 'valor': venta,
             'sub': f'Equilibrio ${base["pesos"]:,.2f}' if base['pesos'] is not None else 'Equilibrio sin calcular',
             'tono': _tono(venta, base['pesos'], meta_venta)},
            {'etiqueta': 'Ocupación', 'tipo': 'pct', 'valor': ocupacion['ocupacion'],
             'sub': 'Equilibrio {} · meta {}'.format(
                 f'{base["ocupacion_pct"]} %' if base['ocupacion_pct'] is not None else 's/d',
                 f'{meta_ocupacion} %' if meta_ocupacion is not None else 's/d'),
             'tono': _tono(ocupacion['ocupacion'], base['ocupacion_pct'], meta_ocupacion)},
            {'etiqueta': etiqueta_utilidad, 'tipo': 'moneda', 'valor': utilidad, 'estimado': estimada,
             'sub': sub_utilidad,
             'tono': NEUTRO if utilidad is None else (EXITO if utilidad >= eq['utilidad_objetivo'] and utilidad >= 0
                                                       else (ALERTA if utilidad >= 0 else ERROR))},
            {'etiqueta': 'Ingreso por día disponible', 'tipo': 'moneda',
             'valor': dividir(venta, vendibles) if vendibles else None,
             'sub': f'{vendibles} días vendibles', 'tono': NEUTRO},
            {'etiqueta': 'Conversión de solicitudes', 'tipo': 'pct', 'valor': conversion['tasa_conversion'],
             'sub': f'{conversion["ventas"]} de {conversion["solicitudes"]} solicitudes', 'tono': NEUTRO},
            {'etiqueta': 'CAC', 'tipo': 'moneda', 'valor': embudo['cac'],
             'sub': f'Marketing ${embudo["gasto_marketing"]:,.2f}', 'tono': NEUTRO},
        ]


