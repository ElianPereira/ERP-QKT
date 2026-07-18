"""
Tests del módulo Facturación
============================
"""
from decimal import Decimal
from datetime import date, timedelta
from django.test import TestCase
from django.contrib.auth.models import User
from django.template.loader import render_to_string

from comercial.models import Cliente, Cotizacion, Pago
from facturacion.models import SolicitudFactura


def _crear_cotizacion(cliente, precio):
    cot = Cotizacion.objects.create(
        cliente=cliente,
        nombre_evento='Evento Test',
        fecha_evento=date.today() + timedelta(days=30),
        incluye_refrescos=False, incluye_cerveza=False,
        incluye_licor_nacional=False, incluye_licor_premium=False,
        incluye_cocteleria_basica=False, incluye_cocteleria_premium=False,
    )
    Cotizacion.objects.filter(pk=cot.pk).update(precio_final=precio)
    cot.refresh_from_db()
    return cot


class SolicitudFacturaClienteNoFiscalTest(TestCase):
    """
    Regresión: un pago de un cliente SIN datos fiscales debe generar una
    solicitud con el snapshot "público en general" (RFC genérico, S01), y el
    PDF debe leer esos datos de la propia solicitud — nunca del Cliente en
    vivo, que para un cliente no fiscal está vacío/None.
    """

    def setUp(self):
        self.user = User.objects.create_user('u', password='x')
        self.cliente = Cliente.objects.create(nombre='Cliente sin factura')
        self.cot = _crear_cotizacion(self.cliente, Decimal('11600.00'))

    def test_solicitud_usa_snapshot_publico_en_general(self):
        pago = Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('11600.00'),
            metodo='EFECTIVO', usuario=self.user,
        )
        solicitud = SolicitudFactura.objects.get(pago=pago)
        self.assertEqual(solicitud.rfc, 'XAXX010101000')
        self.assertEqual(solicitud.razon_social, 'PUBLICO EN GENERAL')
        self.assertEqual(solicitud.uso_cfdi, 'S01')

    def test_pdf_no_muestra_none_y_usa_datos_de_la_solicitud(self):
        pago = Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('11600.00'),
            metodo='EFECTIVO', usuario=self.user,
        )
        solicitud = SolicitudFactura.objects.get(pago=pago)
        html = render_to_string('facturacion/solicitud_pdf.html', {
            'solicitud': solicitud, 'cliente': self.cliente,
            'folio': f'SOL-{solicitud.id:03d}', 'logo_url': '',
            'calc_subtotal': Decimal('0'), 'calc_iva': Decimal('0'),
            'calc_ret_isr': Decimal('0'), 'calc_total': solicitud.monto,
        })
        self.assertNotIn('None', html)
        self.assertIn('XAXX010101000', html)
        self.assertIn('PUBLICO EN GENERAL', html)
        self.assertIn('S01 - Sin efectos fiscales', html)

    def test_cliente_fiscal_conserva_sus_propios_datos(self):
        """No debe romperse el caso normal: cliente con datos fiscales reales."""
        cliente_fiscal = Cliente.objects.create(
            nombre='Empresa SA', es_cliente_fiscal=True, tipo_persona='MORAL',
            rfc='ABC010101AB1', razon_social='EMPRESA SA DE CV',
            codigo_postal_fiscal='97000', regimen_fiscal='601', uso_cfdi='G03',
        )
        cot = _crear_cotizacion(cliente_fiscal, Decimal('11600.00'))
        pago = Pago.objects.create(
            cotizacion=cot, monto=Decimal('11600.00'),
            metodo='TRANSFERENCIA', usuario=self.user,
        )
        solicitud = SolicitudFactura.objects.get(pago=pago)
        self.assertEqual(solicitud.rfc, 'ABC010101AB1')
        self.assertEqual(solicitud.razon_social, 'EMPRESA SA DE CV')
        self.assertEqual(solicitud.uso_cfdi, 'G03')
