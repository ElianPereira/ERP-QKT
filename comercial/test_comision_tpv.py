"""
Tests de la comisión de terminal (TPV) capturada manualmente en Pago.
Ejecutar: python manage.py test comercial.test_comision_tpv --verbosity=2
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from comercial.models import Cliente, Cotizacion, ItemCotizacion, Pago
from contabilidad.models import Poliza


def _crear_cotizacion(monto_items=Decimal('1000.00')):
    cliente = Cliente.objects.create(nombre='Cliente TPV', tipo_persona='FISICA')
    cotizacion = Cotizacion.objects.create(
        cliente=cliente,
        nombre_evento='Evento TPV',
        fecha_evento=date.today() + timedelta(days=90),
        incluye_refrescos=False,
    )
    ItemCotizacion.objects.create(
        cotizacion=cotizacion, descripcion='Servicio de evento',
        cantidad=1, precio_unitario=monto_items,
    )
    cotizacion.save()
    cotizacion.refresh_from_db()
    return cotizacion


class MontoNetoTest(TestCase):
    def test_monto_neto_resta_comision(self):
        cotizacion = _crear_cotizacion(monto_items=Decimal('500.00'))
        pago = Pago.objects.create(
            cotizacion=cotizacion, tipo='INGRESO', concepto='VENTA',
            monto=Decimal('500.00'), metodo='TARJETA_CREDITO',
            comision_tpv=Decimal('2.94'),
        )
        self.assertEqual(pago.monto_neto, Decimal('497.06'))

    def test_monto_neto_sin_comision_es_igual_al_monto(self):
        cotizacion = _crear_cotizacion(monto_items=Decimal('300.00'))
        pago = Pago.objects.create(
            cotizacion=cotizacion, tipo='INGRESO', concepto='VENTA',
            monto=Decimal('300.00'), metodo='EFECTIVO',
        )
        self.assertEqual(pago.monto_neto, Decimal('300.00'))


class PolizaComisionTpvTest(TestCase):
    def test_pago_tarjeta_con_comision_genera_poliza(self):
        cotizacion = _crear_cotizacion(monto_items=Decimal('1000.00'))
        pago = Pago.objects.create(
            cotizacion=cotizacion, tipo='INGRESO', concepto='VENTA',
            monto=Decimal('1000.00'), metodo='TARJETA_DEBITO',
            comision_tpv=Decimal('23.20'),  # 20.00 + 3.20 IVA (16%)
        )
        poliza = Poliza.objects.filter(origen='COMISION_TPV', object_id=pago.pk).first()
        self.assertIsNotNone(poliza)
        self.assertEqual(poliza.tipo, 'E')
        movimientos = list(poliza.movimientos.all())
        total_debe = sum(m.debe for m in movimientos)
        total_haber = sum(m.haber for m in movimientos)
        self.assertEqual(total_debe, Decimal('23.20'))
        self.assertEqual(total_haber, Decimal('23.20'))

    def test_pago_tarjeta_sin_comision_no_genera_poliza(self):
        cotizacion = _crear_cotizacion(monto_items=Decimal('1000.00'))
        pago = Pago.objects.create(
            cotizacion=cotizacion, tipo='INGRESO', concepto='VENTA',
            monto=Decimal('1000.00'), metodo='TARJETA_CREDITO',
        )
        self.assertFalse(Poliza.objects.filter(origen='COMISION_TPV', object_id=pago.pk).exists())

    def test_pago_efectivo_con_comision_no_genera_poliza(self):
        """comision_tpv solo aplica a tarjeta cobrada en la terminal física."""
        cotizacion = _crear_cotizacion(monto_items=Decimal('1000.00'))
        pago = Pago.objects.create(
            cotizacion=cotizacion, tipo='INGRESO', concepto='VENTA',
            monto=Decimal('1000.00'), metodo='EFECTIVO',
            comision_tpv=Decimal('10.00'),
        )
        self.assertFalse(Poliza.objects.filter(origen='COMISION_TPV', object_id=pago.pk).exists())

    def test_no_duplica_poliza_si_signal_se_dispara_de_mas(self):
        from contabilidad.signals import crear_poliza_comision_tpv
        cotizacion = _crear_cotizacion(monto_items=Decimal('1000.00'))
        pago = Pago.objects.create(
            cotizacion=cotizacion, tipo='INGRESO', concepto='VENTA',
            monto=Decimal('1000.00'), metodo='TARJETA_DEBITO',
            comision_tpv=Decimal('23.20'),
        )
        crear_poliza_comision_tpv(pago)  # llamada extra manual
        self.assertEqual(Poliza.objects.filter(origen='COMISION_TPV', object_id=pago.pk).count(), 1)
