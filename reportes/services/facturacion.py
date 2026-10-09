"""
Servicios de Reportes de Facturación
=====================================
Facturas (solicitudes) emitidas por período.

ERP Quinta Ko'ox Tanil
"""
from datetime import date
from decimal import Decimal
from typing import Dict

from django.db.models import Count, Sum

# Tono del badge por estado de la solicitud (5 tonos del sistema de diseño).
TONOS_ESTADO = {'PENDIENTE': 'alerta', 'ENVIADA': 'info', 'FACTURADA': 'exito'}


class FacturasEmitidasService:
    """
    Genera reporte de solicitudes de factura emitidas en un período.
    """

    @classmethod
    def generar(cls, fecha_inicio: date, fecha_fin: date) -> Dict:
        from facturacion.models import SolicitudFactura

        # Una solicitud cancelada no se timbra: si contara, inflaría el total.
        qs = SolicitudFactura.objects.filter(
            fecha_solicitud__date__gte=fecha_inicio,
            fecha_solicitud__date__lte=fecha_fin,
        ).exclude(estado='CANCELADA').select_related('cliente', 'cotizacion').order_by('-fecha_solicitud')

        facturas = []
        total_monto = Decimal('0.00')
        resumen_forma_pago = {}

        for f in qs:
            folio_cot = f"COT-{f.cotizacion.id:03d}" if f.cotizacion else "—"
            forma = f.get_forma_pago_display()
            resumen_forma_pago[forma] = resumen_forma_pago.get(forma, 0) + 1
            total_monto += f.monto

            facturas.append({
                'folio': f"SOL-{f.id:03d}",
                'fecha': f.fecha_solicitud,
                'cliente': f.cliente.nombre if f.cliente else '—',
                # El RFC de la solicitud (Público en General si el cliente no dio el suyo).
                'rfc': f.rfc or '—',
                'cotizacion': folio_cot,
                'concepto': f.concepto[:60],
                'monto': f.monto,
                'forma_pago': forma,
                'metodo_pago': f.get_metodo_pago_display(),
                'estado': f.get_estado_display(),
                'estado_tono': TONOS_ESTADO.get(f.estado, 'neutro'),
            })

        return {
            'fecha_inicio': fecha_inicio,
            'fecha_fin': fecha_fin,
            'facturas': facturas,
            'total_monto': total_monto,
            'count': len(facturas),
            'resumen_forma_pago': resumen_forma_pago,
        }
