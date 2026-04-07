"""Tests para la app comunicacion."""
from datetime import date, timedelta
from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings

from comercial.models import Cliente, Cotizacion, Pago
from comunicacion.models import ComunicacionCliente


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='test@qkt.mx',
    COMUNICACION_SIGNALS_ENABLED=True,
)
class ComunicacionSignalsTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(
            nombre='Cliente Test',
            email='cliente@example.com',
            tipo_persona='FISICA',
        )
        self.cot = Cotizacion.objects.create(
            cliente=self.cliente,
            nombre_evento='Boda Test',
            fecha_evento=date.today() + timedelta(days=60),
            num_personas=100,
            precio_final=Decimal('50000.00'),
            incluye_refrescos=False,
            incluye_cerveza=False,
            incluye_licor_nacional=False,
            incluye_licor_premium=False,
            incluye_cocteleria_basica=False,
            incluye_cocteleria_premium=False,
        )
        Cotizacion.objects.filter(pk=self.cot.pk).update(precio_final=Decimal('50000.00'))
        self.cot.refresh_from_db()

    def test_pago_ingreso_envia_confirmacion(self):
        mail.outbox = []
        Pago.objects.create(
            cotizacion=self.cot,
            monto=Decimal('10000.00'),
            metodo='TRANSFERENCIA',
            tipo='INGRESO',
        )
        comms = ComunicacionCliente.objects.filter(
            cotizacion=self.cot, tipo='CONFIRMACION_PAGO'
        )
        self.assertEqual(comms.count(), 1)
        self.assertEqual(comms.first().estado, 'ENVIADO')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('cliente@example.com', mail.outbox[0].to)

    def test_pago_reembolso_envia_notificacion_reembolso(self):
        Pago.objects.create(
            cotizacion=self.cot,
            monto=Decimal('10000.00'),
            metodo='TRANSFERENCIA',
            tipo='INGRESO',
        )
        mail.outbox = []
        Pago.objects.create(
            cotizacion=self.cot,
            monto=Decimal('5000.00'),
            metodo='TRANSFERENCIA',
            tipo='REEMBOLSO',
        )
        comms = ComunicacionCliente.objects.filter(
            cotizacion=self.cot, tipo='REEMBOLSO'
        )
        self.assertEqual(comms.count(), 1)
        self.assertEqual(
            ComunicacionCliente.objects.filter(
                cotizacion=self.cot, tipo='CONFIRMACION_PAGO',
                estado='ENVIADO',
            ).exclude(pago__tipo='REEMBOLSO').count(),
            1,
        )

    def test_cotizacion_enviada_es_idempotente(self):
        self.cot.estado = 'COTIZADA'
        self.cot.save()
        self.cot.save()
        self.assertEqual(
            ComunicacionCliente.objects.filter(
                cotizacion=self.cot, tipo='COTIZACION'
            ).count(),
            1,
        )

    def test_cliente_sin_email_no_crea_comunicacion(self):
        self.cliente.email = ''
        self.cliente.save()
        Pago.objects.create(
            cotizacion=self.cot,
            monto=Decimal('1000.00'),
            metodo='EFECTIVO',
            tipo='INGRESO',
        )
        self.assertEqual(
            ComunicacionCliente.objects.filter(cotizacion=self.cot).count(),
            0,
        )
