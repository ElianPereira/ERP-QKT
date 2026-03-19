"""
Servicios de Reportes Comerciales
==================================
CxC (Antigüedad de Saldos) y Cotizaciones por período.

ERP Quinta Ko'ox Tanil
"""
from decimal import Decimal
from datetime import date
from typing import Dict, List, Optional
from django.utils import timezone


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
                'tipo_evento': cot.nombre_evento[:20],
                'fecha_evento': cot.fecha_evento,
                'precio_final': cot.precio_final,
                'total_pagado': pagado,
                'saldo': cot.saldo_pendiente(),
                'estado': estado_cot,
            })

        return {
            'fecha_inicio': fecha_inicio,
            'fecha_fin': fecha_fin,
            'estado_filtro': estado,
            'cotizaciones': cotizaciones,
            'total_cotizado': total_cotizado,
            'total_cobrado': total_cobrado,
            'total_pendiente': total_cotizado - total_cobrado,
            'resumen_estados': resumen_estados,
            'count': len(cotizaciones),
        }
