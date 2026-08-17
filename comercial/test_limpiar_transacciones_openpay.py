"""
Tests del comando limpiar_transacciones_openpay_prueba.
Ejecutar: python manage.py test comercial.test_limpiar_transacciones_openpay --verbosity=2
"""
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from comercial.models import OpenpayTransaccion, Pago
from comercial.services_openpay import procesar_webhook_openpay
from comercial.test_openpay import _crear_cotizacion, _payload_exitoso
from contabilidad.models import Poliza


def _webhook_con_fee(cotizacion, openpay_id='txlimpieza01', amount=1000.00):
    return {
        'type': 'charge.succeeded',
        'transaction': {
            'id': openpay_id, 'status': 'completed', 'amount': amount,
            'method': 'store', 'order_id': f'COT-{cotizacion.id}-1',
            'fee': {'amount': 31.5, 'tax': 5.04, 'currency': 'MXN'},
        }
    }


class LimpiarTransaccionesOpenpayTest(TestCase):
    def test_dry_run_no_borra_nada(self):
        cotizacion = _crear_cotizacion()
        procesar_webhook_openpay(_webhook_con_fee(cotizacion))
        self.assertEqual(OpenpayTransaccion.objects.count(), 1)

        out = StringIO()
        call_command('limpiar_transacciones_openpay_prueba', stdout=out)

        self.assertEqual(OpenpayTransaccion.objects.count(), 1)
        self.assertIn('DRY RUN', out.getvalue())

    def test_apply_borra_transaccion_pago_y_polizas(self):
        cotizacion = _crear_cotizacion()
        procesar_webhook_openpay(_webhook_con_fee(cotizacion))
        self.assertEqual(OpenpayTransaccion.objects.count(), 1)
        self.assertEqual(Pago.objects.count(), 1)
        self.assertTrue(Poliza.objects.filter(origen='PAGO_CLIENTE').exists())
        self.assertTrue(Poliza.objects.filter(origen='COMISION_OPENPAY').exists())
        saldo_antes = cotizacion.saldo_pendiente()

        out = StringIO()
        call_command('limpiar_transacciones_openpay_prueba', '--apply', stdout=out)

        self.assertEqual(OpenpayTransaccion.objects.count(), 0)
        self.assertEqual(Pago.objects.count(), 0)
        self.assertFalse(Poliza.objects.filter(origen='PAGO_CLIENTE').exists())
        self.assertFalse(Poliza.objects.filter(origen='COMISION_OPENPAY').exists())

        # La cotización sigue existiendo, con el saldo restaurado
        cotizacion.refresh_from_db()
        self.assertTrue(cotizacion.pk)
        self.assertGreater(cotizacion.saldo_pendiente(), saldo_antes)

    @override_settings(OPENPAY_MODE='production')
    def test_se_niega_a_correr_en_produccion(self):
        with self.assertRaises(CommandError):
            call_command('limpiar_transacciones_openpay_prueba', '--apply')


class AdminActionBorrarTransaccionesTest(TestCase):
    """La acción del admin usa la misma lógica que el comando — para usuarios
    sin acceso a shell/CLI en Railway."""

    def setUp(self):
        from django.contrib.auth.models import User
        self.admin_user = User.objects.create_superuser('admin_tpv', 'admin@test.com', 'pass1234')
        self.client.force_login(self.admin_user)

    def test_sin_confirmar_no_borra_nada(self):
        """SEC-BIZ-002: un solo POST sin 'confirmar=si' no debe borrar nada."""
        cotizacion = _crear_cotizacion()
        procesar_webhook_openpay(_webhook_con_fee(cotizacion))

        from django.urls import reverse
        url = reverse('admin:comercial_openpaytransaccion_changelist')
        respuesta = self.client.post(url, {
            'action': 'borrar_transacciones_de_prueba',
            '_selected_action': [str(OpenpayTransaccion.objects.get().pk)],
        }, follow=True)

        self.assertEqual(OpenpayTransaccion.objects.count(), 1)
        self.assertContains(respuesta, '¿Confirmar esta acción?')

    def test_accion_borra_seleccionadas(self):
        cotizacion = _crear_cotizacion()
        procesar_webhook_openpay(_webhook_con_fee(cotizacion))
        registro = OpenpayTransaccion.objects.get()

        from django.urls import reverse
        url = reverse('admin:comercial_openpaytransaccion_changelist')
        response = self.client.post(url, {
            'action': 'borrar_transacciones_de_prueba',
            '_selected_action': [str(registro.pk)],
            'confirmar': 'si',
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(OpenpayTransaccion.objects.count(), 0)
        self.assertEqual(Pago.objects.count(), 0)
        self.assertFalse(Poliza.objects.filter(origen='COMISION_OPENPAY').exists())

    @override_settings(OPENPAY_MODE='production')
    def test_accion_no_borra_en_produccion(self):
        cotizacion = _crear_cotizacion()
        procesar_webhook_openpay(_webhook_con_fee(cotizacion))
        registro = OpenpayTransaccion.objects.get()

        from django.urls import reverse
        url = reverse('admin:comercial_openpaytransaccion_changelist')
        self.client.post(url, {
            'action': 'borrar_transacciones_de_prueba',
            '_selected_action': [str(registro.pk)],
            'confirmar': 'si',
        }, follow=True)

        self.assertEqual(OpenpayTransaccion.objects.count(), 1)
