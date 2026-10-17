"""
Economía unitaria por tipo de servicio (Issue #402)
===================================================
Definiciones:

- **Unidad vendible**: Evento = 1 día; Pasadía = 1 día; Hospedaje = 1 reservación
  que ocupa `noches` días (se mide también en noches-habitación).
- **Costo variable** = gasto ligado a la cotización (`gasto_set`), con el mismo
  criterio base/IVA que `RentabilidadEventosService`. Con menos de `MIN_MUESTRA`
  ventas con gasto ligado en el tipo, se usa el % de costo variable de
  `ParametrosNegocio` y el resultado se marca «estimado».
- **Margen de contribución (MC)** = ticket neto - costo variable.
  **MC por día** = MC / días que ocupa la unidad.
"""
from datetime import date
from decimal import Decimal
from typing import Dict, Optional

from reportes.services.comercial import _prorratear_iva

from ..models import ParametrosNegocio
from .base import (
    D0,
    ESTANCIA_DEFAULT,
    ETIQUETAS,
    HABITACIONES,
    MIN_MUESTRA,
    TIPOS,
    UMBRAL_GASTO_LIGADO,
    dividir,
    porcentaje,
    q2,
    q4,
    venta_neta,
    ventas_reales,
)


def costo_ligado(cotizacion) -> Decimal:
    """Gasto (sin IVA acreditable) ligado a la cotización."""
    total = D0
    for gasto in cotizacion.gasto_set.all():
        linea = gasto.total_linea or D0
        if gasto.compra.uuid:
            base, _ = _prorratear_iva(linea, gasto.compra)
            total += base
        else:
            total += linea
    return total


def _noches_habitacion(cotizacion) -> int:
    noches = sum(
        (i.cantidad for i in cotizacion.items.all()
         if i.producto_id and i.producto.rol_cotizador == 'HABITACION_HOSPEDAJE'), D0)
    return int(noches) if noches else (cotizacion.noches or 0) * HABITACIONES


class EconomiaUnitariaService:

    @classmethod
    def generar(cls, desde: date, hasta: date, parametros: Optional[ParametrosNegocio] = None,
                overrides: Optional[dict] = None) -> Dict:
        """
        `overrides` permite simular: `ticket_<TIPO>` (pesos) y `costo_pct_<TIPO>` (porcentaje).
        El service nunca lee el `request`.
        """
        p = parametros or ParametrosNegocio.vigente()
        overrides = overrides or {}
        acc = {t: dict(ventas=0, venta=D0, dias=0, noches=0, noches_hab=0, personas=0,
                       con_gasto=0, venta_con_gasto=D0, costo_con_gasto=D0) for t in TIPOS}
        otros_venta = D0
        cotizaciones = ventas_reales(desde, hasta).select_related('cliente').prefetch_related(
            'gasto_set__compra', 'items__producto')
        for cot in cotizaciones:
            neta = venta_neta(cot)
            if cot.tipo_servicio not in acc:
                otros_venta += neta
                continue
            a = acc[cot.tipo_servicio]
            a['ventas'] += 1
            a['venta'] += neta
            a['personas'] += cot.num_personas or 0
            if cot.tipo_servicio == 'HOSPEDAJE':
                a['dias'] += cot.noches or 1
                a['noches'] += cot.noches or 1
                a['noches_hab'] += _noches_habitacion(cot)
            else:
                a['dias'] += 1
            if cot.gasto_set.all():
                a['con_gasto'] += 1
                a['venta_con_gasto'] += neta
                a['costo_con_gasto'] += costo_ligado(cot)

        tipos, avisos = {}, []
        for tipo in TIPOS:
            fila = cls._fila(tipo, acc[tipo], p, overrides)
            tipos[tipo] = fila
            avisos.extend(fila['avisos'])

        ventas = sum(f['ventas'] for f in tipos.values())
        total_neta = sum((acc[t]['venta'] for t in TIPOS), D0) + otros_venta
        ligado = cls.pct_gasto_ligado(desde, hasta)
        if ligado is not None and ligado < UMBRAL_GASTO_LIGADO:
            avisos.append(
                f'Solo {ligado}% del gasto de costo del periodo está ligado a una cotización: '
                'el costo variable sale bajo y el margen, inflado.')
        return {
            'desde': desde, 'hasta': hasta, 'tipos': tipos, 'ventas': ventas,
            'venta_neta': q2(total_neta), 'otros_venta': q2(otros_venta),
            'pct_gasto_ligado': ligado, 'avisos': avisos,
        }

    @staticmethod
    def _fila(tipo, a, p, overrides) -> Dict:
        nombre, ventas = ETIQUETAS[tipo], a['ventas']
        avisos = []
        ticket_sim = overrides.get(f'ticket_{tipo}')
        if ticket_sim is not None:
            ticket = q2(ticket_sim)
        else:
            ticket = dividir(a['venta'], ventas)

        pct_sim = overrides.get(f'costo_pct_{tipo}')
        if pct_sim is not None:
            pct, fuente = q4(Decimal(pct_sim) / 100), 'simulado'
        elif a['con_gasto'] >= MIN_MUESTRA and a['venta_con_gasto'] > 0:
            pct, fuente = q4(a['costo_con_gasto'] / a['venta_con_gasto']), 'medido'
        else:
            pct, fuente = q4(p.pct_costo_de(tipo) / 100), 'estimado'
            avisos.append(f'{nombre}: {a["con_gasto"]} de {MIN_MUESTRA} ventas con gasto ligado necesarias; '
                          'se usa el % de costo variable de Parámetros.')

        if ventas:
            dias_unidad = q4(Decimal(a['dias']) / ventas)
        elif tipo == 'HOSPEDAJE':
            dias_unidad = ESTANCIA_DEFAULT
        else:
            dias_unidad = Decimal('1')

        costo = q2(ticket * pct) if ticket is not None else None
        mc = ticket - costo if ticket is not None else None
        mc_dia = dividir(mc, dias_unidad) if mc is not None and mc > 0 else None
        if ticket is None:
            avisos.append(f'{nombre}: sin ventas en el periodo; captura un ticket de simulación.')
        elif mc <= 0:
            avisos.append(f'{nombre}: el margen de contribución es {mc}; no se calcula equilibrio con él.')
        return {
            'tipo': tipo, 'nombre': nombre, 'ventas': ventas, 'venta_neta': q2(a['venta']),
            'dias': a['dias'], 'noches': a['noches'], 'noches_habitacion': a['noches_hab'],
            'personas_promedio': dividir(a['personas'], ventas),
            'ticket': ticket, 'costo_pct': pct, 'costo_variable': costo, 'mc': mc,
            'mc_pct': porcentaje(mc, ticket) if mc is not None and ticket else None,
            'dias_por_unidad': dias_unidad, 'mc_dia': mc_dia,
            'fuente_costo': fuente, 'muestra': a['con_gasto'], 'avisos': avisos,
        }

    @staticmethod
    def pct_gasto_ligado(desde: date, hasta: date) -> Optional[Decimal]:
        """% del gasto de categorías de costo (COSTO) del periodo que está ligado a una cotización."""
        from comercial.models import Gasto
        from contabilidad.categorias_gasto import COSTO, GRUPO_POR_CATEGORIA

        claves = [c for c, g in GRUPO_POR_CATEGORIA.items() if g == COSTO]
        gastos = Gasto.objects.filter(
            categoria__in=claves, fecha_gasto__gte=desde, fecha_gasto__lte=hasta).select_related('compra')
        total = ligado = D0
        for gasto in gastos:
            linea = gasto.total_linea or D0
            base = _prorratear_iva(linea, gasto.compra)[0] if gasto.compra.uuid else linea
            total += base
            if gasto.evento_relacionado_id:
                ligado += base
        return porcentaje(ligado, total) if total > 0 else None
