"""
Servicios de Reportes de Facturación
=====================================
Facturas (solicitudes) emitidas por período.

ERP Quinta Ko'ox Tanil
"""
from decimal import Decimal
from datetime import date
from typing import Dict
from django.db.models import Sum, Count


class FacturasEmitidasService:
    """
    Genera reporte de solicitudes de factura emitidas en un período.
    """

    @classmethod
    def generar(cls, fecha_inicio: date, fecha_fin: date) -> Dict:
        from facturacion.models import SolicitudFactura

        qs = SolicitudFactura.objects.filter(
            fecha_solicitud__date__gte=fecha_inicio,
            fecha_solicitud__date__lte=fecha_fin,
        ).select_related('cliente', 'cotizacion').order_by('-fecha_solicitud')

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
                'rfc': f.cliente.rfc if f.cliente else '—',
                'cotizacion': folio_cot,
                'concepto': f.concepto[:60],
                'monto': f.monto,
                'forma_pago': forma,
                'metodo_pago': f.get_metodo_pago_display(),
            })

        return {
            'fecha_inicio': fecha_inicio,
            'fecha_fin': fecha_fin,
            'facturas': facturas,
            'total_monto': total_monto,
            'count': len(facturas),
            'resumen_forma_pago': resumen_forma_pago,
        }
