"""
Tests del backlog de seguridad (Issue #190), orden 48 (SEC-BIZ-002):
`registrar_reembolso` (PagoAdmin) exige un segundo POST con
'confirmar=si' antes de crear el Pago tipo REEMBOLSO espejo.

Ejecutar: python manage.py test comercial.test_admin_confirmacion_pagos --verbosity=2
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from comercial.models import Cliente, Cotizacion, ItemCotizacion, Pago


def _crear_cotizacion():
    cliente = Cliente.objects.create(nombre='Cliente reembolso')
    cotizacion = Cotizacion.objects.create(
        cliente=cliente,
        nombre_evento='Evento de prueba',
        fecha_evento=date.today() + timedelta(days=90),
    )
    ItemCotizacion.objects.create(
        cotizacion=cotizacion, descripcion='Servicio', cantidad=1,
        precio_unitario=1000,
    )
    return cotizacion


class RegistrarReembolsoAdminAccionTest(TestCase):
    def setUp(self):
        self.superusuario = User.objects.create_superuser(
            'jefa_pagos', 'jefa_pagos@quintakooxtanil.com', 'clave-de-prueba',
        )
        self.client.force_login(self.superusuario)
        cotizacion = _crear_cotizacion()
        self.pago = Pago.objects.create(
            cotizacion=cotizacion, tipo='INGRESO', monto=Decimal('500.00'),
            metodo='EFECTIVO', usuario=self.superusuario,
        )
        self.url = reverse('admin:comercial_pago_changelist')

    def test_sin_confirmar_no_crea_el_reembolso(self):
        respuesta = self.client.post(self.url, {
            'action': 'registrar_reembolso',
            '_selected_action': [str(self.pago.pk)],
        }, follow=True)

        self.assertFalse(Pago.objects.filter(tipo='REEMBOLSO').exists())
        self.assertContains(respuesta, '¿Confirmar esta acción?')

    def test_con_confirmar_si_crea_el_reembolso_espejo(self):
        self.client.post(self.url, {
            'action': 'registrar_reembolso',
            '_selected_action': [str(self.pago.pk)],
            'confirmar': 'si',
        }, follow=True)

        reembolso = Pago.objects.get(tipo='REEMBOLSO')
        self.assertEqual(reembolso.monto, Decimal('500.00'))
        self.assertEqual(reembolso.cotizacion, self.pago.cotizacion)


class ReembolsarEnOpenpayAdminAccionTest(TestCase):
    """`reembolsar_en_openpay` dispara dinero real — el gate de confirmación
    intercepta antes de siquiera llamar a la API de Openpay, así que no hace
    falta mockearla para probar que sin 'confirmar=si' no se llega ahí."""

    def setUp(self):
        self.superusuario = User.objects.create_superuser(
            'jefa_openpay', 'jefa_openpay@quintakooxtanil.com', 'clave-de-prueba',
        )
        self.client.force_login(self.superusuario)
        cotizacion = _crear_cotizacion()
        self.pago = Pago.objects.create(
            cotizacion=cotizacion, tipo='INGRESO', monto=Decimal('500.00'),
            metodo='TARJETA_CREDITO', usuario=self.superusuario,
        )
        self.url = reverse('admin:comercial_pago_changelist')

    def test_sin_confirmar_no_llega_a_disparar_el_refund(self):
        respuesta = self.client.post(self.url, {
            'action': 'reembolsar_en_openpay',
            '_selected_action': [str(self.pago.pk)],
        }, follow=True)

        self.assertContains(respuesta, '¿Confirmar esta acción?')
