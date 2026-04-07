"""
Tests del módulo Contabilidad
=============================
Cubre signals de pago, reembolsos y reversión por cancelación.
"""
from decimal import Decimal
from datetime import date, timedelta
from django.test import TestCase
from django.contrib.auth.models import User

from contabilidad.models import (
    CuentaContable, ConfiguracionContable, UnidadNegocio, Poliza
)
from comercial.models import Cliente, Cotizacion, ItemCotizacion, Pago


def setup_contabilidad_minima():
    """Crea catálogo mínimo y configuración para signals."""
    UnidadNegocio.objects.get_or_create(
        clave='EVENTOS', defaults={'nombre': 'Eventos'}
    )
    cuentas = {
        'CAJA':                  ('101.01', 'Caja',               'ACTIVO',  'D'),
        'BANCO_PRINCIPAL':       ('102.01', 'Bancos',             'ACTIVO',  'D'),
        'ANTICIPO_CLIENTES':     ('206.01', 'Anticipo clientes',  'PASIVO',  'A'),
        'INGRESO_EVENTOS':       ('401.01', 'Ingreso eventos',    'INGRESO', 'A'),
        'IVA_TRASLADADO':        ('208.01', 'IVA trasladado',     'PASIVO',  'A'),
        'ISR_RETENIDO_CLIENTES': ('118.01', 'ISR ret. clientes',  'ACTIVO',  'D'),
    }
    for op, (codigo, nombre, tipo, naturaleza) in cuentas.items():
        cc, _ = CuentaContable.objects.get_or_create(
            codigo_sat=codigo,
            defaults={'nombre': nombre, 'tipo': tipo, 'naturaleza': naturaleza}
        )
        ConfiguracionContable.objects.get_or_create(
            operacion=op, defaults={'cuenta': cc, 'activa': True}
        )


def _crear_cotizacion(cliente, precio):
    cot = Cotizacion.objects.create(
        cliente=cliente,
        nombre_evento='Evento Test',
        fecha_evento=date.today() + timedelta(days=60),
        incluye_refrescos=False, incluye_cerveza=False,
        incluye_licor_nacional=False, incluye_licor_premium=False,
        incluye_cocteleria_basica=False, incluye_cocteleria_premium=False,
    )
    # precio = 11600 → subtotal=10000, iva=1600
    subtotal = (precio / Decimal('1.16')).quantize(Decimal('0.01'))
    iva = precio - subtotal
    Cotizacion.objects.filter(pk=cot.pk).update(
        precio_final=precio, subtotal=subtotal, iva=iva
    )
    cot.refresh_from_db()
    return cot


class PolizaPagoClienteTest(TestCase):

    def setUp(self):
        setup_contabilidad_minima()
        self.user = User.objects.create_user('u', password='x')
        self.cliente = Cliente.objects.create(nombre='Cliente', tipo_persona='FISICA')
        self.cot = _crear_cotizacion(self.cliente, Decimal('11600.00'))

    def test_pago_genera_poliza_ingreso_balanceada(self):
        Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('11600.00'),
            metodo='TRANSFERENCIA', usuario=self.user
        )
        polizas = Poliza.objects.filter(origen='PAGO_CLIENTE')
        self.assertEqual(polizas.count(), 1)
        p = polizas.first()
        debe = sum(m.debe for m in p.movimientos.all())
        haber = sum(m.haber for m in p.movimientos.all())
        self.assertEqual(debe, haber)
        self.assertEqual(debe, Decimal('11600.00'))


class ReembolsoClienteTest(TestCase):

    def setUp(self):
        setup_contabilidad_minima()
        self.user = User.objects.create_user('u', password='x')
        self.cliente = Cliente.objects.create(nombre='Cliente', tipo_persona='FISICA')
        self.cot = _crear_cotizacion(self.cliente, Decimal('11600.00'))
        self.pago = Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('11600.00'),
            metodo='TRANSFERENCIA', usuario=self.user
        )

    def test_reembolso_genera_poliza_egreso_inversa(self):
        Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('5000.00'),
            metodo='TRANSFERENCIA', tipo='REEMBOLSO', usuario=self.user
        )
        polizas_e = Poliza.objects.filter(tipo='E', origen='PAGO_CLIENTE')
        self.assertEqual(polizas_e.count(), 1)
        p = polizas_e.first()
        debe = sum(m.debe for m in p.movimientos.all())
        haber = sum(m.haber for m in p.movimientos.all())
        self.assertEqual(debe, haber)
        self.assertEqual(haber, Decimal('5000.00'))

    def test_reembolso_excedente_rechazado(self):
        from django.core.exceptions import ValidationError
        pago = Pago(
            cotizacion=self.cot, monto=Decimal('20000.00'),
            metodo='EFECTIVO', tipo='REEMBOLSO', usuario=self.user
        )
        with self.assertRaises(ValidationError):
            pago.clean()

    def test_total_pagado_neto_descuenta_reembolso(self):
        Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('3000.00'),
            metodo='EFECTIVO', tipo='REEMBOLSO', usuario=self.user
        )
        self.assertEqual(self.cot.total_pagado(), Decimal('8600.00'))


class ReversionCancelacionTest(TestCase):

    def setUp(self):
        setup_contabilidad_minima()
        self.user = User.objects.create_user('u', password='x')
        self.cliente = Cliente.objects.create(nombre='Cliente', tipo_persona='FISICA')
        self.cot = _crear_cotizacion(self.cliente, Decimal('11600.00'))
        ItemCotizacion.objects.create(
            cotizacion=self.cot, descripcion='X',
            cantidad=1, precio_unitario=Decimal('10000.00')
        )
        Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('5800.00'),
            metodo='TRANSFERENCIA', usuario=self.user
        )

    def test_cancelacion_crea_poliza_reversion(self):
        ok, _ = self.cot.cambiar_estado('CANCELADA', self.user, motivo='Cliente desistió')
        self.assertTrue(ok)
        reversiones = Poliza.objects.filter(origen='AJUSTE')
        self.assertEqual(reversiones.count(), 1)
        rev = reversiones.first()
        debe = sum(m.debe for m in rev.movimientos.all())
        haber = sum(m.haber for m in rev.movimientos.all())
        self.assertEqual(debe, haber)
        self.assertEqual(debe, Decimal('5800.00'))

    def test_cancelacion_idempotente(self):
        from contabilidad.signals import crear_polizas_reversion_cancelacion
        self.cot.cambiar_estado('CANCELADA', self.user, motivo='X')
        crear_polizas_reversion_cancelacion(self.cot, self.user, motivo='X')
        self.assertEqual(Poliza.objects.filter(origen='AJUSTE').count(), 1)
