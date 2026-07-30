"""
Tests de la integración Openpay (webhook)
=========================================
Ejecutar: python manage.py test comercial.test_openpay --verbosity=2
"""
import base64
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client, override_settings
from django.urls import reverse

from comercial.models import (
    Cliente, Cotizacion, ItemCotizacion, Pago, OpenpayTransaccion, PortalCliente,
)
from comercial.services_openpay import (
    procesar_webhook_openpay, procesar_cargo_tarjeta, procesar_cargo_efectivo,
    procesar_cargo_spei, reembolsar_cargo_openpay, consultar_y_confirmar_cargo,
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
        self.assertIn('no autorizó', resultado['mensaje'])
        self.assertFalse(Pago.objects.filter(cotizacion=cotizacion).exists())
        # El intento fallido queda registrado para auditoría
        self.assertEqual(OpenpayTransaccion.objects.filter(cotizacion=cotizacion, procesado=False).count(), 1)

    @patch('comercial.services_openpay.requests.post')
    def test_tarjeta_robada_loggea_motivo_explicito_pero_no_lo_muestra(self, mock_post):
        """Certificación Openpay: el log debe traer el motivo real del rechazo;
        el cliente sigue viendo un mensaje genérico por seguridad."""
        mock_post.return_value = MagicMock(status_code=402, json=lambda: {
            'error_code': 3004, 'description': 'The card was declined - stolen card',
            'request_id': 'req-robada-001', 'category': 'gateway',
        })
        cotizacion = _crear_cotizacion()
        with self.assertLogs('comercial.services_openpay', level='WARNING') as logs:
            resultado = procesar_cargo_tarjeta(cotizacion, Decimal('500.00'), 'tok119', 'dev119')
        salida = '\n'.join(logs.output)

        # El log trae el motivo explícito, el código, la descripción y el request_id
        self.assertIn('3004', salida)
        self.assertIn('ROBADA', salida)
        self.assertIn('stolen card', salida)
        self.assertIn('req-robada-001', salida)

        # Pero al cliente NO se le filtra la descripción cruda de Openpay
        self.assertFalse(resultado['ok'])
        self.assertNotIn('stolen', resultado['mensaje'].lower())
        self.assertNotIn('request_id', resultado['mensaje'].lower())
        self.assertFalse(Pago.objects.filter(cotizacion=cotizacion).exists())

    @patch('comercial.services_openpay.requests.post')
    def test_antifraude_loggea_motivo_explicito_pero_no_lo_muestra(self, mock_post):
        mock_post.return_value = MagicMock(status_code=402, json=lambda: {
            'error_code': 3005, 'description': 'The card was declined by the fraud system',
            'request_id': 'req-fraude-001', 'category': 'gateway',
        })
        cotizacion = _crear_cotizacion()
        with self.assertLogs('comercial.services_openpay', level='WARNING') as logs:
            resultado = procesar_cargo_tarjeta(cotizacion, Decimal('500.00'), 'tok044', 'dev044')
        salida = '\n'.join(logs.output)

        self.assertIn('3005', salida)
        self.assertIn('ANTIFRAUDE', salida)
        self.assertIn('fraud system', salida)

        self.assertFalse(resultado['ok'])
        self.assertNotIn('fraud', resultado['mensaje'].lower())
        # El motivo explícito también queda persistido para auditoría
        registro = OpenpayTransaccion.objects.get(cotizacion=cotizacion)
        self.assertIn('ANTIFRAUDE', registro.error_detalle)

    @patch('comercial.services_openpay.requests.post')
    def test_cargo_failed_con_http_200_tambien_se_loggea(self, mock_post):
        """Openpay puede rechazar con HTTP 200 y status 'failed'."""
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx-failed-001', 'status': 'failed', 'error_code': 3001,
            'description': 'The issuing bank declined the operation',
        })
        cotizacion = _crear_cotizacion()
        with self.assertLogs('comercial.services_openpay', level='WARNING') as logs:
            resultado = procesar_cargo_tarjeta(cotizacion, Decimal('500.00'), 'tokf', 'devf')
        salida = '\n'.join(logs.output)

        self.assertIn('NO COMPLETADO', salida)
        self.assertIn('failed', salida)
        self.assertIn('declined', salida)

        self.assertFalse(resultado['ok'])
        # No se filtra el estado interno de Openpay al cliente
        self.assertNotIn('failed', resultado['mensaje'].lower())
        self.assertFalse(Pago.objects.filter(cotizacion=cotizacion).exists())

    @patch('comercial.services_openpay.requests.post')
    def test_rechazo_de_efectivo_tambien_se_loggea(self, mock_post):
        mock_post.return_value = MagicMock(status_code=400, json=lambda: {
            'error_code': 1001, 'description': 'The order_id has already been processed',
            'request_id': 'req-store-001',
        })
        cotizacion = _crear_cotizacion()
        with self.assertLogs('comercial.services_openpay', level='WARNING') as logs:
            resultado = procesar_cargo_efectivo(cotizacion, Decimal('500.00'))
        salida = '\n'.join(logs.output)

        self.assertIn('store', salida)
        self.assertIn('already been processed', salida)
        self.assertFalse(resultado['ok'])
        self.assertNotIn('already been processed', resultado['mensaje'])


class TresDSecureTest(TestCase):
    """Certificación Openpay: el cargo con tarjeta debe pasar por 3D Secure
    y solo autorizarse DESPUÉS de que el cliente se autentique con su banco."""

    @patch('comercial.services_openpay.requests.post')
    def test_manda_use_3d_secure_y_redirect_url(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx3ds001', 'status': 'charge_pending',
            'payment_method': {'type': 'redirect', 'url': 'https://sandbox-api.openpay.mx/3ds/tx3ds001'},
        })
        cotizacion = _crear_cotizacion()
        procesar_cargo_tarjeta(
            cotizacion, Decimal('500.00'), 'tok3ds', 'dev3ds',
            redirect_url='https://erp.quintakooxtanil.com/mi-evento/abc/pago-3ds/',
        )
        payload = mock_post.call_args.kwargs['json']
        self.assertTrue(payload['use_3d_secure'])
        self.assertEqual(payload['redirect_url'], 'https://erp.quintakooxtanil.com/mi-evento/abc/pago-3ds/')

    @patch('comercial.services_openpay.requests.post')
    def test_charge_pending_no_crea_pago_y_devuelve_url_de_redireccion(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx3ds002', 'status': 'charge_pending',
            'payment_method': {'type': 'redirect', 'url': 'https://sandbox-api.openpay.mx/3ds/tx3ds002'},
        })
        cotizacion = _crear_cotizacion()
        resultado = procesar_cargo_tarjeta(
            cotizacion, Decimal('500.00'), 'tok3ds', 'dev3ds', redirect_url='https://x/retorno/',
        )
        self.assertTrue(resultado['ok'])
        self.assertEqual(resultado['redirect_3ds'], 'https://sandbox-api.openpay.mx/3ds/tx3ds002')
        # Clave: el cargo AÚN NO se cobró, así que no debe existir el Pago
        self.assertFalse(Pago.objects.filter(cotizacion=cotizacion).exists())
        self.assertFalse(OpenpayTransaccion.objects.get(openpay_id='tx3ds002').procesado)

    @patch('comercial.services_openpay.requests.get')
    @patch('comercial.services_openpay.requests.post')
    def test_retorno_3ds_completado_crea_el_pago(self, mock_post, mock_get):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx3ds003', 'status': 'charge_pending',
            'payment_method': {'type': 'redirect', 'url': 'https://op/3ds'},
        })
        cotizacion = _crear_cotizacion()
        procesar_cargo_tarjeta(cotizacion, Decimal('500.00'), 'tok', 'dev', redirect_url='https://x/r/')
        self.assertFalse(Pago.objects.filter(cotizacion=cotizacion).exists())

        # El cliente vuelve del banco: ahora el cargo sí está autorizado
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx3ds003', 'status': 'completed', 'amount': 500.00,
            'authorization': '801585',
        })
        resultado = consultar_y_confirmar_cargo(cotizacion, 'tx3ds003')
        self.assertTrue(resultado['ok'])
        self.assertTrue(Pago.objects.filter(cotizacion=cotizacion, referencia='tx3ds003').exists())
        registro = OpenpayTransaccion.objects.get(openpay_id='tx3ds003')
        self.assertTrue(registro.procesado)
        self.assertEqual(registro.estado_openpay, 'completed')

    @patch('comercial.services_openpay.requests.get')
    def test_retorno_3ds_es_idempotente(self, mock_get):
        """Recargar la página de retorno no debe duplicar el Pago."""
        cotizacion = _crear_cotizacion()
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx3ds004', 'status': 'completed', 'amount': 500.00,
        })
        consultar_y_confirmar_cargo(cotizacion, 'tx3ds004')
        consultar_y_confirmar_cargo(cotizacion, 'tx3ds004')
        self.assertEqual(Pago.objects.filter(cotizacion=cotizacion, referencia='tx3ds004').count(), 1)

    @patch('comercial.services_openpay.requests.get')
    def test_retorno_3ds_rechazado_no_crea_pago_ni_filtra_el_motivo(self, mock_get):
        cotizacion = _crear_cotizacion()
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx3ds005', 'status': 'failed', 'amount': 500.00,
            'error_code': 3005, 'description': 'The card was declined by the fraud system',
        })
        with self.assertLogs('comercial.services_openpay', level='WARNING') as logs:
            resultado = consultar_y_confirmar_cargo(cotizacion, 'tx3ds005')
        self.assertFalse(resultado['ok'])
        self.assertFalse(Pago.objects.filter(cotizacion=cotizacion).exists())
        # El motivo real solo en el log, nunca al cliente
        self.assertIn('fraud system', '\n'.join(logs.output))
        self.assertNotIn('fraud', resultado['mensaje'].lower())

    @patch('comercial.services_openpay.requests.get')
    def test_retorno_3ds_sigue_pendiente(self, mock_get):
        cotizacion = _crear_cotizacion()
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx3ds006', 'status': 'charge_pending', 'amount': 500.00,
        })
        with self.assertLogs('comercial.services_openpay', level='WARNING'):
            resultado = consultar_y_confirmar_cargo(cotizacion, 'tx3ds006')
        self.assertFalse(resultado['ok'])
        self.assertTrue(resultado['pendiente'])
        self.assertFalse(Pago.objects.filter(cotizacion=cotizacion).exists())

    @patch('comercial.services_openpay.requests.post')
    def test_charge_pending_sin_url_es_error_controlado(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx3ds007', 'status': 'charge_pending', 'payment_method': {},
        })
        cotizacion = _crear_cotizacion()
        with self.assertLogs('comercial.services_openpay', level='ERROR'):
            resultado = procesar_cargo_tarjeta(
                cotizacion, Decimal('500.00'), 'tok', 'dev', redirect_url='https://x/r/',
            )
        self.assertFalse(resultado['ok'])
        self.assertFalse(Pago.objects.filter(cotizacion=cotizacion).exists())


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

    @patch('comercial.services_openpay.requests.post')
    def test_error_efectivo_no_muestra_description_cruda_en_ingles(self, mock_post):
        # Openpay siempre regresa 'description' en inglés; el mensaje al
        # cliente nunca debe mostrar ese texto crudo, sin importar el intento.
        mock_post.return_value = MagicMock(status_code=400, json=lambda: {
            'error_code': 1001, 'description': 'The order_id has already been processed'
        })
        cotizacion = _crear_cotizacion()
        resultado = procesar_cargo_efectivo(cotizacion, Decimal('500.00'))
        self.assertFalse(resultado['ok'])
        self.assertNotIn('already been processed', resultado['mensaje'])

    @patch('comercial.services_openpay.requests.post')
    def test_error_spei_no_muestra_description_cruda_en_ingles(self, mock_post):
        mock_post.return_value = MagicMock(status_code=400, json=lambda: {
            'error_code': 1001, 'description': 'The order_id has already been processed'
        })
        cotizacion = _crear_cotizacion()
        resultado = procesar_cargo_spei(cotizacion, Decimal('500.00'))
        self.assertFalse(resultado['ok'])
        self.assertNotIn('already been processed', resultado['mensaje'])


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
        # 600 >= 50% de 1160 (precio_final con IVA de _crear_cotizacion) — cumple
        # el mínimo del primer pago.
        response = self.client.post(self.url, secure=True, data={'metodo': 'store', 'monto': '600.00'})
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['reference'], 'OPENPAY05REF')

    def test_segundo_envio_simultaneo_queda_bloqueado_por_candado(self):
        from django.core.cache import cache
        candado = f'pago_openpay_en_curso:{self.cotizacion.id}'
        cache.set(candado, '1', timeout=12)
        try:
            response = self.client.post(self.url, secure=True, data={'metodo': 'store', 'monto': '600.00'})
            data = response.json()
            self.assertFalse(data['ok'])
            self.assertTrue(data['candado'])
            self.assertIn('procesando', data['mensaje'])
        finally:
            cache.delete(candado)

    @patch('comercial.services_openpay.requests.post')
    def test_candado_se_libera_despues_de_procesar_el_pago(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx-candado', 'status': 'in_progress',
            'payment_method': {'reference': 'OPENPAYCANDADOREF'}
        })
        self.client.post(self.url, secure=True, data={'metodo': 'store', 'monto': '600.00'})
        from django.core.cache import cache
        candado = f'pago_openpay_en_curso:{self.cotizacion.id}'
        self.assertIsNone(cache.get(candado))


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


class ComisionOpenpayTest(TestCase):
    """La comisión que Openpay reporta en `fee` genera su póliza automática
    (Comisión + IVA acreditable vs. Banco, igual que la comisión de terminal)."""

    def _webhook_con_fee(self, cotizacion, openpay_id='txfee01', amount=1000.00, fee=None):
        return {
            'type': 'charge.succeeded',
            'transaction': {
                'id': openpay_id, 'status': 'completed', 'amount': amount,
                'method': 'store', 'order_id': f'COT-{cotizacion.id}-1',
                'fee': fee if fee is not None else {'amount': 31.5, 'tax': 5.04, 'currency': 'MXN'},
            }
        }

    def test_webhook_con_fee_genera_poliza_de_comision(self):
        from contabilidad.models import Poliza
        cotizacion = _crear_cotizacion()
        registro = procesar_webhook_openpay(self._webhook_con_fee(cotizacion))
        self.assertTrue(registro.procesado)

        poliza = Poliza.objects.filter(origen='COMISION_OPENPAY', object_id=registro.pk).first()
        self.assertIsNotNone(poliza)
        self.assertEqual(poliza.tipo, 'E')
        movimientos = list(poliza.movimientos.all())
        total_debe = sum(m.debe for m in movimientos)
        total_haber = sum(m.haber for m in movimientos)
        self.assertEqual(total_debe, Decimal('36.54'))  # 31.50 comisión + 5.04 IVA
        self.assertEqual(total_haber, Decimal('36.54'))

    def test_webhook_repetido_no_duplica_poliza_de_comision(self):
        from contabilidad.models import Poliza
        cotizacion = _crear_cotizacion()
        payload = self._webhook_con_fee(cotizacion, openpay_id='txfee02')
        procesar_webhook_openpay(payload)
        procesar_webhook_openpay(payload)  # reintento de Openpay
        registro = OpenpayTransaccion.objects.get(openpay_id='txfee02')
        self.assertEqual(Poliza.objects.filter(origen='COMISION_OPENPAY', object_id=registro.pk).count(), 1)

    def test_webhook_sin_fee_no_genera_poliza_pero_si_pago(self):
        from contabilidad.models import Poliza
        cotizacion = _crear_cotizacion()
        payload = self._webhook_con_fee(cotizacion, openpay_id='txsinfee')
        del payload['transaction']['fee']
        registro = procesar_webhook_openpay(payload)
        self.assertTrue(registro.procesado)
        self.assertTrue(Pago.objects.filter(referencia='txsinfee').exists())
        self.assertFalse(Poliza.objects.filter(origen='COMISION_OPENPAY', object_id=registro.pk).exists())

    @patch('comercial.services_openpay.requests.post')
    def test_cargo_tarjeta_con_fee_genera_poliza_de_comision(self, mock_post):
        from contabilidad.models import Poliza
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'txfeecard', 'status': 'completed', 'amount': 500.00,
            'fee': {'amount': 17.0, 'tax': 2.72, 'currency': 'MXN'},
        })
        cotizacion = _crear_cotizacion()
        resultado = procesar_cargo_tarjeta(cotizacion, Decimal('500.00'), 'tok123', 'dev123')
        self.assertTrue(resultado['ok'])
        registro = OpenpayTransaccion.objects.get(openpay_id='txfeecard')
        poliza = Poliza.objects.filter(origen='COMISION_OPENPAY', object_id=registro.pk).first()
        self.assertIsNotNone(poliza)
        self.assertEqual(sum(m.haber for m in poliza.movimientos.all()), Decimal('19.72'))


class PaginasLegalesTest(TestCase):
    """Certificación Openpay: el sitio y el portal deben publicar Aviso de
    Privacidad y Términos y Condiciones, con mención expresa de Openpay."""

    def test_aviso_privacidad_publico(self):
        response = self.client.get(reverse('aviso_privacidad'), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Aviso de Privacidad')
        self.assertContains(response, 'Openpay')

    def test_terminos_mencionan_openpay_con_el_texto_requerido(self):
        response = self.client.get(reverse('terminos_condiciones'), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Las transacciones ser')
        self.assertContains(response, 'pasarela de Openpay')

    def test_portal_enlaza_las_paginas_legales(self):
        cotizacion = _crear_cotizacion()
        portal = PortalCliente.objects.get(cotizacion=cotizacion)
        response = self.client.get(reverse('portal_evento', args=[portal.token]), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('aviso_privacidad'))
        self.assertContains(response, reverse('terminos_condiciones'))


class DueDateReferenciaTest(TestCase):
    """La referencia de efectivo/SPEI debe llevar fecha de vencimiento."""

    @patch('comercial.services_openpay.requests.post')
    def test_efectivo_manda_due_date(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'txdd001', 'status': 'in_progress',
            'payment_method': {'reference': 'REF001'},
        })
        cotizacion = _crear_cotizacion()
        procesar_cargo_efectivo(cotizacion, Decimal('500.00'))
        payload = mock_post.call_args.kwargs['json']
        self.assertIn('due_date', payload)
        # Formato ISO 8601 que espera Openpay
        datetime.strptime(payload['due_date'], '%Y-%m-%dT%H:%M:%S')

    @patch('comercial.services_openpay.requests.post')
    def test_spei_manda_due_date_y_no_excede_la_fecha_del_evento(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'txdd002', 'status': 'in_progress',
            'payment_method': {'clabe': '646180111812345678'},
        })
        # Evento mañana: la referencia no puede vencer después del evento
        cotizacion = _crear_cotizacion()
        cotizacion.fecha_evento = date.today() + timedelta(days=1)
        cotizacion.save()

        procesar_cargo_spei(cotizacion, Decimal('500.00'))
        payload = mock_post.call_args.kwargs['json']
        vence = datetime.strptime(payload['due_date'], '%Y-%m-%dT%H:%M:%S').date()
        self.assertLessEqual(vence, cotizacion.fecha_evento)
