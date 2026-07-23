"""
Tests de la integración Openpay (webhook)
=========================================
Ejecutar: python manage.py test comercial.test_openpay --verbosity=2
"""
import base64
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client, override_settings
from django.urls import reverse

from comercial.models import (
    Cliente, Cotizacion, ItemCotizacion, Pago, OpenpayTransaccion, PortalCliente,
)
from comercial.services_openpay import (
    procesar_webhook_openpay, procesar_cargo_tarjeta, procesar_cargo_efectivo,
    procesar_cargo_spei, reembolsar_cargo_openpay,
)

WEBHOOK_USER = 'openpay-test-user'
WEBHOOK_PASSWORD = 'openpay-test-password'


def _basic_auth_header(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


def _crear_cotizacion(monto_items=Decimal('1000.00')):
    """Cotización con un item real para que precio_final > 0 (Cotizacion.save
    recalcula los totales desde los items, así que no basta pasar precio_final)."""
    cliente = Cliente.objects.create(nombre='Cliente Openpay', tipo_persona='FISICA')
    cotizacion = Cotizacion.objects.create(
        cliente=cliente,
        nombre_evento='Evento Openpay',
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


def _payload_exitoso(cotizacion, openpay_id='txabc123', amount=500.00):
    return {
        'type': 'charge.succeeded',
        'transaction': {
            'id': openpay_id,
            'status': 'completed',
            'amount': amount,
            'order_id': f'COT-{cotizacion.id}-VENTA',
            'creation_date': '2026-07-20T10:00:00-06:00',
        }
    }


@override_settings(OPENPAY_WEBHOOK_USER=WEBHOOK_USER, OPENPAY_WEBHOOK_PASSWORD=WEBHOOK_PASSWORD)
class WebhookOpenpayAuthTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse('openpay_webhook')

    def test_rechaza_sin_autenticacion(self):
        response = self.client.post(self.url, secure=True, data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 401)

    def test_rechaza_credenciales_incorrectas(self):
        response = self.client.post(
            self.url, secure=True, data='{}', content_type='application/json',
            HTTP_AUTHORIZATION=_basic_auth_header('otro', 'password-malo'),
        )
        self.assertEqual(response.status_code, 401)

    @override_settings(OPENPAY_WEBHOOK_USER='', OPENPAY_WEBHOOK_PASSWORD='')
    def test_rechaza_todo_si_credenciales_sin_configurar(self):
        response = self.client.post(
            self.url, secure=True, data='{}', content_type='application/json',
            HTTP_AUTHORIZATION=_basic_auth_header('', ''),
        )
        self.assertEqual(response.status_code, 401)

    def test_verification_payload_real_de_openpay_regresa_200(self):
        """Forma real capturada en producción: type=VERIFICATION + verificationCode."""
        payload = {'type': 'VERIFICATION', 'eventDate': 'Jul 23, 2026, 7:46:32 PM', 'verificationCode': 'hvW90eV0'}
        response = self.client.post(
            self.url, secure=True, data=json.dumps(payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=_basic_auth_header(WEBHOOK_USER, WEBHOOK_PASSWORD),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(OpenpayTransaccion.objects.exists())

    def test_verification_code_regresa_200(self):
        response = self.client.post(
            self.url, secure=True, data=json.dumps({'type': 'verification_code', 'verification_code': 'abc123'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=_basic_auth_header(WEBHOOK_USER, WEBHOOK_PASSWORD),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(OpenpayTransaccion.objects.exists())

    def test_payload_invalido_regresa_200_sin_procesar(self):
        response = self.client.post(
            self.url, secure=True, data='esto no es json',
            content_type='application/json',
            HTTP_AUTHORIZATION=_basic_auth_header(WEBHOOK_USER, WEBHOOK_PASSWORD),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(OpenpayTransaccion.objects.exists())

    def test_webhook_completo_crea_pago(self):
        cotizacion = _crear_cotizacion()
        response = self.client.post(
            self.url, secure=True, data=json.dumps(_payload_exitoso(cotizacion)),
            content_type='application/json',
            HTTP_AUTHORIZATION=_basic_auth_header(WEBHOOK_USER, WEBHOOK_PASSWORD),
        )
        self.assertEqual(response.status_code, 200)

        pago = Pago.objects.get(referencia='txabc123')
        self.assertEqual(pago.monto, Decimal('500.00'))
        self.assertEqual(pago.metodo, 'PLATAFORMA')
        self.assertEqual(pago.cotizacion, cotizacion)
        self.assertEqual(pago.fecha_pago, date.today())  # fecha de confirmación del webhook

        registro = OpenpayTransaccion.objects.get(openpay_id='txabc123')
        self.assertTrue(registro.procesado)
        self.assertEqual(registro.pago, pago)


class ProcesarWebhookIdempotenciaTest(TestCase):
    """El mismo openpay_id no debe generar dos Pagos aunque llegue repetido."""

    def test_no_duplica_pago_con_mismo_openpay_id(self):
        cotizacion = _crear_cotizacion()
        payload = _payload_exitoso(cotizacion)

        procesar_webhook_openpay(payload)
        procesar_webhook_openpay(payload)  # simula reintento de Openpay

        self.assertEqual(OpenpayTransaccion.objects.filter(openpay_id='txabc123').count(), 1)
        self.assertEqual(Pago.objects.filter(referencia='txabc123').count(), 1)

    def test_evento_no_exitoso_no_genera_pago(self):
        payload = {
            'type': 'charge.failed',
            'transaction': {'id': 'txfail001', 'status': 'failed', 'amount': 500.00, 'order_id': 'COT-1-VENTA'}
        }
        registro = procesar_webhook_openpay(payload)
        self.assertFalse(Pago.objects.filter(referencia='txfail001').exists())
        self.assertFalse(registro.procesado)
        self.assertEqual(registro.estado_openpay, 'failed')

    def test_order_id_desconocido_no_genera_pago(self):
        payload = {
            'type': 'charge.succeeded',
            'transaction': {'id': 'txsinorden', 'status': 'completed', 'amount': 500.00, 'order_id': 'COT-99999-VENTA'}
        }
        registro = procesar_webhook_openpay(payload)
        self.assertFalse(Pago.objects.filter(referencia='txsinorden').exists())
        self.assertFalse(registro.procesado)
        self.assertIn('no hay cotización ligada', registro.error_detalle)

    def test_monto_excede_saldo_no_genera_pago_pero_queda_registrado(self):
        cotizacion = _crear_cotizacion(monto_items=Decimal('100.00'))
        payload = _payload_exitoso(cotizacion, openpay_id='txexceso', amount=500.00)
        registro = procesar_webhook_openpay(payload)
        self.assertFalse(Pago.objects.filter(referencia='txexceso').exists())
        self.assertFalse(registro.procesado)
        self.assertIn('Error al crear Pago', registro.error_detalle)

    def test_notificacion_sin_id_se_ignora(self):
        self.assertIsNone(procesar_webhook_openpay({'type': 'charge.succeeded', 'transaction': {}}))
        self.assertFalse(OpenpayTransaccion.objects.exists())


class CargoTarjetaTest(TestCase):
    @patch('comercial.services_openpay.requests.post')
    def test_cargo_exitoso_crea_pago(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx001', 'status': 'completed', 'amount': 500.00
        })
        cotizacion = _crear_cotizacion()
        resultado = procesar_cargo_tarjeta(cotizacion, Decimal('500.00'), 'tok123', 'dev123')
        self.assertTrue(resultado['ok'])
        self.assertTrue(Pago.objects.filter(referencia='tx001', metodo='PLATAFORMA').exists())
        registro = OpenpayTransaccion.objects.get(openpay_id='tx001')
        self.assertTrue(registro.procesado)
        self.assertEqual(registro.metodo, 'card')

    @patch('comercial.services_openpay.requests.post')
    def test_cargo_incluye_datos_del_customer(self, mock_post):
        """Openpay rechaza cargos sin objeto customer ('Attribute customer is required')."""
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx010', 'status': 'completed', 'amount': 500.00
        })
        cotizacion = _crear_cotizacion()
        cotizacion.cliente.nombre = 'Elian Pereira Ceh'
        cotizacion.cliente.email = 'cliente@ejemplo.com'
        cotizacion.cliente.telefono = '9991234567'
        cotizacion.cliente.save()
        procesar_cargo_tarjeta(cotizacion, Decimal('500.00'), 'tok123', 'dev123')

        payload_enviado = mock_post.call_args.kwargs['json']
        self.assertEqual(payload_enviado['customer']['name'], 'Elian')
        self.assertEqual(payload_enviado['customer']['last_name'], 'Pereira Ceh')
        self.assertEqual(payload_enviado['customer']['email'], 'cliente@ejemplo.com')
        self.assertEqual(payload_enviado['customer']['phone_number'], '9991234567')
        self.assertFalse(payload_enviado['customer']['requires_account'])

    @patch('comercial.services_openpay.requests.post')
    def test_tarjeta_declinada_no_crea_pago(self, mock_post):
        mock_post.return_value = MagicMock(status_code=402, json=lambda: {
            'error_code': 3001, 'description': 'La tarjeta fue declinada'
        })
        cotizacion = _crear_cotizacion()
        resultado = procesar_cargo_tarjeta(cotizacion, Decimal('500.00'), 'tok999', 'dev999')
        self.assertFalse(resultado['ok'])
        self.assertIn('declinada', resultado['mensaje'])
        self.assertFalse(Pago.objects.filter(cotizacion=cotizacion).exists())
        # El intento fallido queda registrado para auditoría
        self.assertEqual(OpenpayTransaccion.objects.filter(cotizacion=cotizacion, procesado=False).count(), 1)


class CargoEfectivoSpeiTest(TestCase):
    @patch('comercial.services_openpay.requests.post')
    def test_cargo_efectivo_no_crea_pago_hasta_webhook(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx002', 'status': 'in_progress',
            'payment_method': {'reference': 'OPENPAY02ABC', 'barcode_url': 'https://ejemplo/barcode.png'}
        })
        cotizacion = _crear_cotizacion()
        resultado = procesar_cargo_efectivo(cotizacion, Decimal('500.00'))
        self.assertTrue(resultado['ok'])
        self.assertEqual(resultado['reference'], 'OPENPAY02ABC')
        self.assertFalse(Pago.objects.filter(cotizacion=cotizacion).exists())  # aún no se paga
        registro = OpenpayTransaccion.objects.get(openpay_id='tx002')
        self.assertFalse(registro.procesado)
        self.assertEqual(registro.referencia_pago, 'OPENPAY02ABC')

    @patch('comercial.services_openpay.requests.post')
    def test_webhook_confirma_cargo_efectivo_pendiente(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx003', 'status': 'in_progress',
            'payment_method': {'reference': 'OPENPAY03XYZ'}
        })
        cotizacion = _crear_cotizacion()
        procesar_cargo_efectivo(cotizacion, Decimal('500.00'))

        webhook_payload = {
            'type': 'charge.succeeded',
            'transaction': {'id': 'tx003', 'status': 'completed', 'amount': 500.00, 'method': 'store'}
        }
        registro = procesar_webhook_openpay(webhook_payload)
        self.assertTrue(registro.procesado)
        self.assertTrue(Pago.objects.filter(referencia='tx003').exists())

        # Reintento del webhook: no duplica el Pago
        procesar_webhook_openpay(webhook_payload)
        self.assertEqual(Pago.objects.filter(referencia='tx003').count(), 1)

    @patch('comercial.services_openpay.requests.post')
    def test_cargo_spei_regresa_clabe(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx004', 'status': 'in_progress',
            'payment_method': {'bank': 'STP', 'clabe': '646180111812345678', 'name': 'REF004'}
        })
        cotizacion = _crear_cotizacion()
        resultado = procesar_cargo_spei(cotizacion, Decimal('500.00'))
        self.assertTrue(resultado['ok'])
        self.assertEqual(resultado['clabe'], '646180111812345678')
        self.assertFalse(Pago.objects.filter(cotizacion=cotizacion).exists())
        self.assertEqual(OpenpayTransaccion.objects.get(openpay_id='tx004').referencia_pago, '646180111812345678')


class PortalCheckoutViewTest(TestCase):
    """La vista del checkout usa la misma autenticación del portal (token)."""

    def setUp(self):
        self.cotizacion = _crear_cotizacion()
        # Cotizacion.save() ya crea el PortalCliente automáticamente
        self.portal = PortalCliente.objects.get(cotizacion=self.cotizacion)
        self.url = reverse('portal_procesar_pago_openpay', args=[self.portal.token])

    def test_token_invalido_regresa_404(self):
        url = reverse('portal_procesar_pago_openpay', args=['token-inexistente'])
        response = self.client.post(url, secure=True, data={'metodo': 'store', 'monto': '500.00'})
        self.assertEqual(response.status_code, 404)

    def test_monto_invalido_rechazado(self):
        response = self.client.post(self.url, secure=True, data={'metodo': 'store', 'monto': 'abc'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['ok'])

    def test_monto_mayor_al_saldo_rechazado(self):
        response = self.client.post(self.url, secure=True, data={'metodo': 'store', 'monto': '999999.00'})
        self.assertFalse(response.json()['ok'])
        self.assertIn('saldo pendiente', response.json()['mensaje'])

    def test_metodo_desconocido_rechazado(self):
        response = self.client.post(self.url, secure=True, data={'metodo': 'bitcoin', 'monto': '500.00'})
        self.assertFalse(response.json()['ok'])

    def test_tarjeta_sin_token_rechazada_sin_llamar_openpay(self):
        response = self.client.post(self.url, secure=True, data={'metodo': 'card', 'monto': '500.00'})
        self.assertFalse(response.json()['ok'])
        self.assertFalse(OpenpayTransaccion.objects.exists())

    @patch('comercial.services_openpay.requests.post')
    def test_flujo_completo_efectivo(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx005', 'status': 'in_progress',
            'payment_method': {'reference': 'OPENPAY05REF'}
        })
        response = self.client.post(self.url, secure=True, data={'metodo': 'store', 'monto': '500.00'})
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['reference'], 'OPENPAY05REF')


class ReembolsoOpenpayTest(TestCase):
    def test_pago_sin_transaccion_openpay_no_reembolsable(self):
        cotizacion = _crear_cotizacion()
        pago = Pago.objects.create(
            cotizacion=cotizacion, tipo='INGRESO', concepto='VENTA',
            monto=Decimal('500.00'), metodo='EFECTIVO',
        )
        resultado = reembolsar_cargo_openpay(pago)
        self.assertFalse(resultado['ok'])
        self.assertIn('no viene de Openpay', resultado['mensaje'])

    @patch('comercial.services_openpay.requests.post')
    def test_reembolso_exitoso(self, mock_post):
        cotizacion = _crear_cotizacion()
        pago = Pago.objects.create(
            cotizacion=cotizacion, tipo='INGRESO', concepto='VENTA',
            monto=Decimal('500.00'), metodo='PLATAFORMA', referencia='tx006',
        )
        OpenpayTransaccion.objects.create(
            openpay_id='tx006', metodo='card', monto=Decimal('500.00'),
            cotizacion=cotizacion, pago=pago, payload_crudo={}, procesado=True,
        )
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {'id': 'tx006', 'status': 'completed'})
        resultado = reembolsar_cargo_openpay(pago)
        self.assertTrue(resultado['ok'])
        self.assertIn('/charges/tx006/refund', mock_post.call_args[0][0])
