"""
Precio y costo por servicio: la única fuente (Issue #409)
========================================================
Capacidad, equilibrio, plan anual, metas y el resumen de Parámetros leen de aquí el precio neto, el
costo y el margen de cada servicio, así que un mismo servicio muestra la misma cifra en todos
los paneles.

**Unidad.** Evento y Pasadía = 1 venta. Hospedaje = una habitación, una noche; ocupa
`1 / HABITACIONES` de día-quinta (supone que, si hay demanda, las dos habitaciones se rentan la
misma noche). Un evento ocupa `1 + dias_preparacion_evento` días.

**Precio** (sin IVA): simulado > Parámetros (se captura con IVA) > promedio histórico, y este
último solo con al menos `MIN_MUESTRA` ventas típicas (las de precio especial no cuentan). Con
menos, no hay precio: `ticket=None` y un aviso con los folios que formaban el promedio.

**Costo**: % simulado > costo directo de Parámetros > % de respaldo (ambos solo si el precio es
simulado o de Parámetros) > costo histórico (medido, o el % de respaldo si no hay muestra).
"""
from decimal import Decimal
from typing import Dict, Optional

from core_erp.impuestos import sin_iva

from ..models import ParametrosNegocio
from .base import ETIQUETAS, HABITACIONES, MIN_MUESTRA, TIPOS, dividir, porcentaje, q2


def referencia(p: ParametrosNegocio, tipo: str) -> Optional[dict]:
    """
    Precio y costo directo capturados en Parámetros para `tipo`, o `None` si no hay precio.
    El precio se captura con IVA; todo lo demás va sin IVA. Sin costo directo en pesos, se usa
    el % de respaldo sobre el precio neto.
    """
    precio = p.precio_de(tipo)
    if precio is None:
        return None
    neto = q2(sin_iva(precio))
    directo = p.costo_directo_de(tipo)
    if directo is None:
        costo, fuente = q2(neto * p.pct_costo_de(tipo) / 100), 'estimado'
    else:
        costo, fuente = q2(directo), 'parámetros'
    return {'neto': neto, 'costo': costo, 'mc': neto - costo, 'fuente_costo': fuente}


def dias_por_unidad(tipo: str, p: ParametrosNegocio) -> Decimal:
    if tipo == 'EVENTO':
        return Decimal(1 + p.dias_preparacion_evento)
    return Decimal(1) / HABITACIONES if tipo == 'HOSPEDAJE' else Decimal(1)


def economia_servicio(tipo: str, e: dict, p: ParametrosNegocio, overrides: Optional[dict] = None) -> Dict:
    """
    `e` es la fila del histórico (`EconomiaUnitariaService.generar()['tipos'][tipo]`). Devuelve
    `ticket`, `costo`, `mc`, `mc_pct`, `mc_dia`, `dias_unidad`, `fuente_precio`, `fuente_costo`,
    `folios` y `avisos`; con `ticket=None` el costo y el margen también van vacíos.
    """
    overrides = overrides or {}
    nombre, avisos = ETIQUETAS[tipo], []
    dias_unidad = dias_por_unidad(tipo, p)

    ticket_sim = overrides.get(f'ticket_{tipo}')
    ref = referencia(p, tipo)
    if ticket_sim is not None:
        ticket, fuente_precio = q2(ticket_sim), 'simulado'
    elif ref:
        ticket, fuente_precio = ref['neto'], 'parámetros'
    elif e['tipicas'] >= MIN_MUESTRA:
        ticket, fuente_precio = e['ticket_unidad'], 'histórico'
        avisos.append(f'{nombre}: sin precio de venta en Parámetros; se usa el promedio de {e["tipicas"]} venta(s) '
                      'típica(s) de la ventana.')
    else:
        ticket, fuente_precio = None, None
        folios = f' Ventas que lo formaban: {", ".join(e["folios"])}.' if e['folios'] else ''
        avisos.append(f'{nombre}: sin precio de venta en Parámetros y solo {e["tipicas"]} de {MIN_MUESTRA} ventas '
                      f'típicas necesarias para promediar; no entra a los cálculos. Captura el precio en Parámetros.'
                      + folios)
    base = {'tipo': tipo, 'nombre': nombre, 'dias_unidad': dias_unidad, 'folios': e['folios'],
            'muestra': e['tipicas'], 'avisos': avisos}
    if ticket is None:
        return {**base, 'ticket': None, 'costo': None, 'mc': None, 'mc_pct': None, 'mc_dia': None,
                'fuente_precio': None, 'fuente_costo': None}

    pct_sim = overrides.get(f'costo_pct_{tipo}')
    directo = p.costo_directo_de(tipo)
    if pct_sim is not None:
        costo, fuente_costo = q2(ticket * Decimal(pct_sim) / 100), 'simulado'
    elif fuente_precio != 'histórico' and directo is not None:
        costo, fuente_costo = q2(directo), 'parámetros'
    elif fuente_precio != 'histórico':
        costo, fuente_costo = q2(ticket * p.pct_costo_de(tipo) / 100), 'estimado'
    else:
        costo, fuente_costo = q2(ticket * e['costo_pct']), e['fuente_costo']

    mc = ticket - costo
    if mc <= 0:
        avisos.append(f'{nombre}: el margen de contribución es {mc}: el costo directo iguala o rebasa al precio; '
                      'no entra al plan ni al equilibrio.')
    return {**base, 'ticket': ticket, 'costo': costo, 'mc': mc, 'mc_pct': porcentaje(mc, ticket),
            'mc_dia': dividir(mc, dias_unidad) if mc > 0 else None,
            'fuente_precio': fuente_precio, 'fuente_costo': fuente_costo}


def economia_servicios(economia: dict, p: ParametrosNegocio, overrides: Optional[dict] = None) -> Dict[str, dict]:
    """`economia_servicio` de los tres tipos, sobre una misma economía histórica."""
    return {tipo: economia_servicio(tipo, economia['tipos'][tipo], p, overrides) for tipo in TIPOS}
