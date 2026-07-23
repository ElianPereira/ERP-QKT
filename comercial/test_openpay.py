"""
Tests de la integración Openpay (webhook)
=========================================
Ejecutar: python manage.py test comercial.test_openpay --verbosity=2
"""
import base64
import json
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client, override_settings
from django.urls import reverse

from comercial.models import Cliente, Cotizacion, ItemCotizacion, Pago, OpenpayTransaccion
from comercial.services_openpay import procesar_webhook_openpay

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
        self.assertEqual(pago.fecha_pago, date(2026, 7, 20))

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
        self.assertIn('no genera Pago', registro.error_detalle)

    def test_order_id_desconocido_no_genera_pago(self):
        payload = {
            'type': 'charge.succeeded',
            'transaction': {'id': 'txsinorden', 'status': 'completed', 'amount': 500.00, 'order_id': 'COT-99999-VENTA'}
        }
        registro = procesar_webhook_openpay(payload)
        self.assertFalse(Pago.objects.filter(referencia='txsinorden').exists())
        self.assertFalse(registro.procesado)
        self.assertIn('No se pudo identificar la cotización', registro.error_detalle)

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
