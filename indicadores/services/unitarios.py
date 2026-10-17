"""
Economía unitaria por tipo de servicio (Issue #402, #409)
=========================================================
Solo describe el **histórico** de la ventana. El precio y el costo que usan los paneles salen
de `servicio.economia_servicio`, que decide entre simulación, Parámetros e histórico.

Definiciones:

- **Unidad vendible**: Evento = 1 día; Pasadía = 1 día; Hospedaje = una habitación, una noche
  (`noches_habitacion`). `ventas` cuenta reservaciones.
- **Precio especial**: la venta con un `DescuentoAplicado` activo cuyo `Descuento.es_cortesia`
  es verdadero cuenta en venta neta, días y ventas, pero no en `tipicas`: el promedio de
  ticket y el de costo solo miran ventas típicas.
- **Costo variable** = gasto ligado a la cotización (`gasto_set`), con el mismo criterio base/IVA
  que `RentabilidadEventosService`. Con menos de `MIN_MUESTRA` ventas típicas con gasto ligado
  en el tipo, el % de costo es el de respaldo de `ParametrosNegocio` y se marca «estimado».
"""
from datetime import date
from decimal import Decimal
from typing import Dict, Optional

from reportes.services.comercial import _prorratear_iva

from ..models import ParametrosNegocio
from .base import (
    D0,
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


def noches_habitacion(cotizacion) -> int:
    noches = sum(
        (i.cantidad for i in cotizacion.items.all()
         if i.producto_id and i.producto.rol_cotizador == 'HABITACION_HOSPEDAJE'), D0)
    return int(noches) if noches else (cotizacion.noches or 0) * HABITACIONES


def es_precio_especial(cotizacion) -> bool:
    """Cierto si la venta lleva un descuento activo marcado como cortesía (`Descuento.es_cortesia`)."""
    return any(a.activo and a.descuento.es_cortesia for a in cotizacion.descuentos_aplicados.all())


def folio(cotizacion) -> str:
    return f'COT-{cotizacion.pk:03d}'


class EconomiaUnitariaService:

    @classmethod
    def generar(cls, desde: date, hasta: date, parametros: Optional[ParametrosNegocio] = None) -> Dict:
        """
        Histórico de [desde, hasta]. No simula ni lee Parámetros de precio: eso es de `economia_servicio`.
        El service nunca lee el `request`.
        """
        p = parametros or ParametrosNegocio.vigente()
        acc = {t: dict(ventas=0, venta=D0, dias=0, noches=0, noches_hab=0, personas=0,
                       tipicas=0, venta_tipica=D0, noches_hab_tipicas=0, folios=[],
                       con_gasto=0, venta_con_gasto=D0, costo_con_gasto=D0) for t in TIPOS}
        otros_venta = D0
        cotizaciones = ventas_reales(desde, hasta).select_related('cliente').prefetch_related(
            'gasto_set__compra', 'items__producto', 'descuentos_aplicados__descuento')
        for cot in cotizaciones:
            neta = venta_neta(cot)
            if cot.tipo_servicio not in acc:
                otros_venta += neta
                continue
            a = acc[cot.tipo_servicio]
            especial = es_precio_especial(cot)
            noches_hab = 0
            a['ventas'] += 1
            a['venta'] += neta
            a['personas'] += cot.num_personas or 0
            if cot.tipo_servicio == 'HOSPEDAJE':
                noches_hab = noches_habitacion(cot)
                a['dias'] += cot.noches or 1
                a['noches'] += cot.noches or 1
                a['noches_hab'] += noches_hab
            else:
                a['dias'] += 1
            if especial:
                continue
            a['tipicas'] += 1
            a['venta_tipica'] += neta
            a['noches_hab_tipicas'] += noches_hab
            a['folios'].append(folio(cot))
            if cot.gasto_set.all():
                a['con_gasto'] += 1
                a['venta_con_gasto'] += neta
                a['costo_con_gasto'] += costo_ligado(cot)

        tipos = {tipo: cls._fila(tipo, acc[tipo], p) for tipo in TIPOS}
        avisos = []
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
    def _fila(tipo, a, p) -> Dict:
        """Cifras del histórico. `ticket_unidad` es por unidad vendible (hospedaje: habitación-noche)."""
        unidades_tipicas = a['noches_hab_tipicas'] if tipo == 'HOSPEDAJE' else a['tipicas']
        if a['con_gasto'] >= MIN_MUESTRA and a['venta_con_gasto'] > 0:
            pct, fuente = q4(a['costo_con_gasto'] / a['venta_con_gasto']), 'medido'
        else:
            pct, fuente = q4(p.pct_costo_de(tipo) / 100), 'estimado'
        return {
            'tipo': tipo, 'nombre': ETIQUETAS[tipo], 'ventas': a['ventas'], 'venta_neta': q2(a['venta']),
            'dias': a['dias'], 'noches': a['noches'], 'noches_habitacion': a['noches_hab'],
            'personas_promedio': dividir(a['personas'], a['ventas']),
            'tipicas': a['tipicas'], 'ticket': dividir(a['venta_tipica'], a['tipicas']),
            'ticket_unidad': dividir(a['venta_tipica'], unidades_tipicas), 'folios': a['folios'],
            'costo_pct': pct, 'fuente_costo': fuente, 'muestra': a['con_gasto'],
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
