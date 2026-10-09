"""
Servicios de Reportes Comerciales
==================================
CxC (Antigüedad de Saldos) y Cotizaciones por período.

ERP Quinta Ko'ox Tanil
"""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional

from django.utils import timezone

# Etiqueta y tono (los 5 del sistema de diseño) de cada tramo de antigüedad.
ANTIGUEDAD = {
    'VENCIDO': ('Vencido', 'error'),
    'URGENTE': ('Urgente', 'alerta'),
    'PROXIMO': ('Próximo', 'info'),
    'AL_DIA': ('Al día', 'exito'),
}


class CxCCarteraService:
    """
    Genera reporte de antigüedad de saldos (CxC) para PDF.
    Reutiliza la lógica de ver_cartera_cxc pero orientada a reporte imprimible.
    """

    @classmethod
    def generar(cls, fecha_corte: date = None) -> Dict:
        from comercial.models import Cotizacion

        if not fecha_corte:
            fecha_corte = timezone.now().date()

        cotizaciones = Cotizacion.objects.filter(
            estado='CONFIRMADA'
        ).select_related('cliente').order_by('fecha_evento')

        cartera = []
        total_por_cobrar = Decimal('0.00')
        total_vencido = Decimal('0.00')
        total_por_vencer = Decimal('0.00')
        resumen = {'VENCIDO': 0, 'URGENTE': 0, 'PROXIMO': 0, 'AL_DIA': 0}

        for cot in cotizaciones:
            saldo = cot.saldo_pendiente()
            if saldo <= 0:
                continue

            total_por_cobrar += saldo
            dias_evento = (cot.fecha_evento - fecha_corte).days

            if dias_evento < 0:
                antiguedad = 'VENCIDO'
                total_vencido += saldo
            elif dias_evento <= 7:
                antiguedad = 'URGENTE'
                total_por_vencer += saldo
            elif dias_evento <= 30:
                antiguedad = 'PROXIMO'
                total_por_vencer += saldo
            else:
                antiguedad = 'AL_DIA'

            resumen[antiguedad] += 1

            cartera.append({
                'folio': f"COT-{cot.id:03d}",
                'cliente': cot.cliente.nombre,
                'evento': cot.nombre_evento,
                'fecha_evento': cot.fecha_evento,
                'precio_final': cot.precio_final,
                'total_pagado': cot.total_pagado(),
                'saldo': saldo,
                'porcentaje_pagado': cot.porcentaje_pagado,
                'dias_evento': dias_evento,
                'antiguedad': antiguedad,
                'antiguedad_etiqueta': ANTIGUEDAD[antiguedad][0],
                'antiguedad_tono': ANTIGUEDAD[antiguedad][1],
            })

        # Ordenar: vencidos primero
        orden = {'VENCIDO': 0, 'URGENTE': 1, 'PROXIMO': 2, 'AL_DIA': 3}
        cartera.sort(key=lambda x: (orden.get(x['antiguedad'], 4), x['fecha_evento']))

        return {
            'fecha_corte': fecha_corte,
            'cartera': cartera,
            'total_por_cobrar': total_por_cobrar,
            'total_vencido': total_vencido,
            'total_por_vencer': total_por_vencer,
            'resumen': resumen,
            'count_total': len(cartera),
        }


class CotizacionesPeriodoService:
    """
    Genera reporte de cotizaciones filtrado por período y estado.
    """

    @classmethod
    def generar(
        cls,
        fecha_inicio: date,
        fecha_fin: date,
        estado: str = None,
    ) -> Dict:
        from comercial.models import Cotizacion

        qs = Cotizacion.objects.filter(
            fecha_evento__gte=fecha_inicio,
            fecha_evento__lte=fecha_fin,
        ).select_related('cliente').order_by('fecha_evento')

        if estado:
            qs = qs.filter(estado=estado)

        cotizaciones = []
        total_cotizado = Decimal('0.00')
        total_cobrado = Decimal('0.00')
        resumen_estados = {}

        for cot in qs:
            pagado = cot.total_pagado()
            total_cotizado += cot.precio_final
            total_cobrado += pagado

            estado_cot = cot.get_estado_display()
            resumen_estados[estado_cot] = resumen_estados.get(estado_cot, 0) + 1

            cotizaciones.append({
                'folio': f"COT-{cot.id:03d}",
                'cliente': cot.cliente.nombre,
                'evento': cot.nombre_evento,
                'tipo_servicio': cot.get_tipo_servicio_display(),
                'fecha_evento': cot.fecha_evento,
                'precio_final': cot.precio_final,
                'total_pagado': pagado,
                'saldo': cot.saldo_pendiente(),
                'estado': estado_cot,
                'estado_clave': cot.estado,
            })

        return {
            'fecha_inicio': fecha_inicio,
            'fecha_fin': fecha_fin,
            'estado_filtro': estado,
            'estado_filtro_etiqueta': dict(Cotizacion.ESTADOS).get(estado, estado),
            'cotizaciones': cotizaciones,
            'total_cotizado': total_cotizado,
            'total_cobrado': total_cobrado,
            'total_pendiente': total_cotizado - total_cobrado,
            'resumen_estados': resumen_estados,
            'count': len(cotizaciones),
        }


def _prorratear_iva(total_linea: Decimal, compra) -> tuple:
    """(base, iva) de una línea de gasto: solo una compra con UUID acredita IVA,
    en proporción a lo que la línea pesa en el total de su factura."""
    if compra.uuid and compra.total > 0 and compra.iva > 0:
        iva = (total_linea / compra.total * compra.iva).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return total_linea - iva, iva
    return total_linea, Decimal('0.00')


class RentabilidadEventosService:
    """
    Rentabilidad por evento: venta sin IVA contra lo gastado en cada evento,
    más los gastos operativos del periodo (los que no se ligan a un evento).

    Sin filtro de estado cuenta solo ventas reales (confirmadas, ejecutadas y
    cerradas): un borrador o una cancelada no es venta.
    """

    @classmethod
    def generar(cls, fecha_inicio: date, fecha_fin: date, estado: str = None) -> Dict:
        from comercial.models import Cotizacion, Gasto
        from comercial.views import ESTADOS_VENTA_REAL

        cero = Decimal('0.00')
        cotizaciones = Cotizacion.objects.filter(
            fecha_evento__gte=fecha_inicio, fecha_evento__lte=fecha_fin,
            estado__in=[estado] if estado else ESTADOS_VENTA_REAL,
        ).select_related('cliente').prefetch_related('gasto_set__compra').order_by('fecha_evento')

        eventos = []
        t = dict.fromkeys(
            ('venta', 'iva', 'base', 'gasto_fiscal', 'iva_acreditable', 'gasto_nofiscal', 'utilidad'), cero)
        for cot in cotizaciones:
            base = cot.subtotal - cot.descuento
            fiscal = iva_acr = nofiscal = cero
            for gasto in cot.gasto_set.all():
                total_linea = gasto.total_linea or cero
                if gasto.compra.uuid:
                    b, i = _prorratear_iva(total_linea, gasto.compra)
                    fiscal += b
                    iva_acr += i
                else:
                    nofiscal += total_linea
            fila = {
                'folio': f"COT-{cot.id:03d}", 'fecha': cot.fecha_evento,
                'cliente': cot.cliente.nombre, 'evento': cot.nombre_evento,
                'venta': cot.precio_final, 'iva': cot.iva, 'base': base,
                'gasto_fiscal': fiscal, 'iva_acreditable': iva_acr, 'gasto_nofiscal': nofiscal,
                'utilidad': base - fiscal - nofiscal,
            }
            for clave in t:
                t[clave] += fila[clave]
            eventos.append(fila)

        etiquetas = dict(Gasto.CATEGORIAS)
        fiscales, nofiscales = {}, {}
        gastos = Gasto.objects.filter(
            evento_relacionado__isnull=True, fecha_gasto__gte=fecha_inicio, fecha_gasto__lte=fecha_fin,
        ).select_related('compra')
        for gasto in gastos:
            total_linea = gasto.total_linea or cero
            nombre = etiquetas.get(gasto.categoria, gasto.categoria)
            if gasto.compra.uuid:
                b, i = _prorratear_iva(total_linea, gasto.compra)
                fila = fiscales.setdefault(nombre, {'nombre': nombre, 'base': cero, 'iva': cero})
                fila['base'] += b
                fila['iva'] += i
            else:
                fila = nofiscales.setdefault(nombre, {'nombre': nombre, 'total': cero})
                fila['total'] += total_linea

        op_fiscal = sum((f['base'] for f in fiscales.values()), cero)
        op_iva = sum((f['iva'] for f in fiscales.values()), cero)
        op_nofiscal = sum((f['total'] for f in nofiscales.values()), cero)
        costos_deducibles = t['gasto_fiscal'] + op_fiscal
        costos_no_deducibles = t['gasto_nofiscal'] + op_nofiscal
        iva_acreditable = t['iva_acreditable'] + op_iva

        return {
            'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin,
            'estado_filtro_etiqueta': dict(Cotizacion.ESTADOS).get(estado) if estado else 'Ventas reales',
            'eventos': eventos, 'totales': t,
            'operativos_fiscales': sorted(fiscales.values(), key=lambda f: f['nombre']),
            'operativos_nofiscales': sorted(nofiscales.values(), key=lambda f: f['nombre']),
            'op_fiscal': op_fiscal, 'op_iva': op_iva, 'op_nofiscal': op_nofiscal,
            'costos_deducibles': costos_deducibles,
            'costos_no_deducibles': costos_no_deducibles,
            'costos_totales': costos_deducibles + costos_no_deducibles,
            'utilidad_neta': t['base'] - costos_deducibles - costos_no_deducibles,
            'iva_acreditable': iva_acreditable,
            'iva_por_pagar': t['iva'] - iva_acreditable,
        }


class PagosRecibidosService:
    """
    Pagos registrados en el periodo. El cobrado neto resta los reembolsos, y
    las condonaciones (cortesías) se informan aparte porque no entra dinero.
    """

    @classmethod
    def generar(cls, fecha_inicio: date, fecha_fin: date) -> Dict:
        from comercial.models import Pago

        cero = Decimal('0.00')
        pagos = list(Pago.objects.filter(
            fecha_pago__gte=fecha_inicio, fecha_pago__lte=fecha_fin,
        ).select_related('cotizacion', 'cotizacion__cliente', 'usuario').order_by('fecha_pago', 'id'))

        cobrado = reembolsado = condonado = cero
        por_metodo = {}
        for pago in pagos:
            if pago.tipo == 'REEMBOLSO':
                reembolsado += pago.monto
            elif pago.metodo == 'CONDONACION':
                condonado += pago.monto
            else:
                cobrado += pago.monto
                nombre = pago.get_metodo_display()
                por_metodo[nombre] = por_metodo.get(nombre, cero) + pago.monto

        metodos = [
            {'nombre': nombre, 'total': total,
             'porcentaje': (total / cobrado * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}
            for nombre, total in sorted(por_metodo.items(), key=lambda m: -m[1])
        ]
        return {
            'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin,
            'pagos': pagos, 'metodos': metodos,
            'cobrado': cobrado, 'reembolsado': reembolsado, 'condonado': condonado,
            'neto': cobrado - reembolsado,
            'count': len(pagos),
        }
