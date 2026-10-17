"""
Tablero de Indicadores (Issue #402, fase 3)
===========================================
Compone los services del módulo para un mes: se calcula una sola vez la
economía unitaria, los costos fijos y la capacidad, y se reparten entre
equilibrio, mezcla y metas. La vista solo pinta lo que este service devuelve.

Cada KPI lleva un tono: **error** bajo el equilibrio, **alerta** entre el
equilibrio y la meta (o el objetivo), **exito** sobre la meta.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Dict, Optional

from reportes.services.operacion import OcupacionService

from ..models import ParametrosNegocio
from .base import (
    D0,
    TIPOS,
    corte,
    dividir,
    mes_anterior,
    mes_rango,
    q2,
    venta_neta,
    ventana_promedio,
    ventas_reales,
)
from .capacidad import CapacidadService
from .costos_fijos import CostosFijosService
from .embudo import EmbudoComercialService
from .equilibrio import EquilibrioService
from .metas import MetasService
from .mezcla import MezclaOptimaService
from .unitarios import EconomiaUnitariaService

EXITO, ALERTA, ERROR, NEUTRO = 'exito', 'alerta', 'error', 'neutro'
MESES_TENDENCIA = 12


def overrides_desde(params) -> dict:
    """Simulación por GET: `ticket_<TIPO>`, `costo_pct_<TIPO>` y `demanda_<TIPO>`. Lo inválido se ignora."""
    resultado = {}
    for tipo in TIPOS:
        for prefijo, minimo, maximo in (('ticket', 0, None), ('costo_pct', 0, 100), ('demanda', 0, None)):
            clave = f'{prefijo}_{tipo}'
            texto = (params.get(clave) or '').strip().replace(',', '')
            if not texto:
                continue
            try:
                valor = Decimal(texto)
            except InvalidOperation:
                continue
            if not valor.is_finite() or valor < minimo or (maximo is not None and valor > maximo):
                continue
            resultado[clave] = int(valor) if prefijo == 'demanda' else valor
    return resultado


def campos_simulacion(overrides: dict) -> list:
    """Entradas del formulario de simulación, con lo que se está simulando ya puesto."""
    from .base import ETIQUETAS

    campos = []
    for prefijo, etiqueta, paso in (('ticket', 'Precio neto', '0.01'), ('costo_pct', '% costo directo', '0.1'),
                                     ('demanda', 'Demanda máxima al mes', '1')):
        for tipo in TIPOS:
            clave = f'{prefijo}_{tipo}'
            campos.append({'nombre': clave, 'etiqueta': f'{etiqueta} · {ETIQUETAS[tipo].lower()}',
                           'paso': paso, 'valor': overrides.get(clave, '')})
    return campos


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

        economia = EconomiaUnitariaService.generar(
            *ventana_promedio(anio, mes, p), parametros=p, overrides=overrides)
        costos = CostosFijosService.generar(corte(anio, mes, hoy), p)
        capacidad = CapacidadService.generar(inicio, fin, p, economia)
        equilibrio = EquilibrioService.generar(
            anio, mes, p, overrides, hoy, economia=economia, costos=costos, capacidad=capacidad)
        mezcla = MezclaOptimaService.generar(anio, mes, p, overrides, hoy, economia=economia, costos=costos)
        metas = MetasService.generar(anio, mes, p, overrides, hoy, equilibrio=equilibrio)
        embudo = EmbudoComercialService.generar(inicio, fin)
        ocupacion = OcupacionService.generar(inicio, fin)

        avisos = list(dict.fromkeys(
            equilibrio['avisos'] + economia['avisos'] + capacidad['avisos'] + mezcla['avisos'] + metas['avisos']))
        resumen = cls._resumen(equilibrio, metas, embudo, ocupacion, mezcla)
        return {
            'anio': anio, 'mes': mes, 'inicio': inicio, 'fin': fin, 'hoy': hoy, 'parametros': p,
            'ventana': ventana_promedio(anio, mes, p), 'overrides': overrides,
            'economia': economia, 'costos': costos, 'capacidad': capacidad, 'equilibrio': equilibrio,
            'mezcla': mezcla, 'metas': metas, 'embudo': embudo, 'ocupacion': ocupacion,
            'resumen': resumen, 'avisos': avisos, 'campos_simulacion': campos_simulacion(overrides),
            'tendencia': _tendencia(anio, mes),
            'equilibrio_pesos': equilibrio['base']['pesos'],
            'equilibrio_ocupacion': equilibrio['base']['ocupacion_pct'],
        }

    @staticmethod
    def _resumen(eq, metas, embudo, ocupacion, mezcla) -> list:
        base, objetivo, meta = eq['base'], eq['objetivo'], metas['meta']
        venta = metas['real']['venta_neta']
        meta_venta = meta.meta_venta_neta if meta and meta.meta_venta_neta else objetivo['pesos']
        meta_ocupacion = meta.meta_ocupacion_pct if meta and meta.meta_ocupacion_pct else objetivo['ocupacion_pct']
        vendibles = eq['dias_vendibles']

        utilidad = None
        if base['mc_pct'] is not None:
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
            {'etiqueta': 'Utilidad estimada', 'tipo': 'moneda', 'valor': utilidad, 'estimado': True,
             'sub': 'Venta × margen − costos fijos',
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


