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
        # 3001 es un rechazo del emisor: el cliente ve el mensaje genérico.
        self.assertIn('No pudimos procesar el pago', resultado['mensaje'])
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
        self.assertIn('robada', salida.lower())
        self.assertIn('stolen card', salida)
        self.assertIn('req-robada-001', salida)

        # Al cliente NO se le filtra la descripción cruda de Openpay...
        self.assertFalse(resultado['ok'])
        self.assertNotIn('The card was declined - stolen card', resultado['mensaje'])
        self.assertNotIn('req-robada-001', resultado['mensaje'])
        # ...ni la traducción: confirmarle "robada"/"retenida" a quien captura
        # la tarjeta le dice qué esquivar en el siguiente intento.
        self.assertEqual(
            resultado['mensaje'],
            'No pudimos procesar el pago con esta tarjeta. Verifica los datos, '
            'intenta con otra tarjeta o comunícate con tu banco.',
        )
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
        # No se filtra la descripción cruda en inglés, ni el request_id, ni el
        # hecho de que fue el antifraude quien rechazó: el cliente ve el mismo
        # mensaje genérico que en cualquier otro rechazo.
        self.assertNotIn('The card was declined by the fraud system', resultado['mensaje'])
        self.assertNotIn('req-fraude-001', resultado['mensaje'])
        self.assertNotIn('fraude', resultado['mensaje'].lower())
        self.assertEqual(
            resultado['mensaje'],
            'No pudimos procesar el pago con esta tarjeta. Verifica los datos, '
            'intenta con otra tarjeta o comunícate con tu banco.',
        )
        # El motivo explícito también queda persistido para auditoría
        registro = OpenpayTransaccion.objects.get(cotizacion=cotizacion)
        self.assertIn('ANTIFRAUDE', registro.error_detalle)

    def test_ningun_mensaje_al_cliente_revela_robo_extravio_o_antifraude(self):
        """
        Candado de la regla: el detalle del rechazo vive en el log y en el
        panel de Openpay, nunca en el portal. Solo se le traduce al cliente lo
        que él mismo puede corregir (vencimiento, fondos, CVV).
        """
        from comercial.services_openpay import (
            MENSAJES_ERROR_TARJETA,
            MOTIVOS_LOG_OPENPAY,
            _mensaje_error_tarjeta,
        )

        self.assertEqual(set(MENSAJES_ERROR_TARJETA), {2002, 2003, 2010, 3002, 3003})

        prohibidas = ('robad', 'extravi', 'fraude', 'retenid', 'bloque', 'riesgo')
        for codigo in MOTIVOS_LOG_OPENPAY:
            mensaje = _mensaje_error_tarjeta({'error_code': codigo}).lower()
            for palabra in prohibidas:
                self.assertNotIn(palabra, mensaje, f'error_code {codigo} filtra "{palabra}"')

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
        self.assertNotIn('The card was declined by the fraud system', resultado['mensaje'])

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
    def test_efectivo_regresa_url_del_recibo_paynet(self, mock_post):
        """
        Openpay exige que el cliente pueda descargar la ficha oficial. La URL
        no viene en la respuesta del cargo: se arma contra el dashboard con el
        merchant y el id de la transacción.
        """
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx-paynet-01', 'status': 'in_progress',
            'payment_method': {'reference': '000020TRT3PGJAWHQHPERTJOQJ0005006',
                               'barcode_url': 'http://x/b.png'},
        })
        cotizacion = _crear_cotizacion()
        resultado = procesar_cargo_efectivo(cotizacion, Decimal('500.00'))
        self.assertTrue(resultado['ok'])
        self.assertIn('/paynet-pdf/', resultado['recibo_url'])
        self.assertIn('dashboard.openpay.mx', resultado['recibo_url'])
        # La ruta de paynet lleva `payment_method.reference`, NO el id de la
        # transacción: usar el id devolvía error y rompía la ficha.
        self.assertTrue(resultado['recibo_url'].endswith('/000020TRT3PGJAWHQHPERTJOQJ0005006'))
        self.assertNotIn('tx-paynet-01', resultado['recibo_url'])

    @patch('comercial.services_openpay.requests.post')
    def test_due_date_no_pasa_de_los_30_dias_que_admite_openpay(self, mock_post):
        """
        La fecha límite puede venir del plan de pagos, y una parcialidad a 45
        días empujaba el `due_date` fuera del máximo documentado por Openpay.
        """
        from datetime import date, datetime, timedelta as td

        from comercial.models import PlanPago

        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx-vigencia', 'status': 'in_progress',
            'payment_method': {'reference': 'REF-V', 'barcode_url': ''},
        })
        cotizacion = _crear_cotizacion()
        # Evento lejano para que el tope por negocio no aplique y quede a la
        # vista únicamente el límite de Openpay.
        cotizacion.fecha_evento = date.today() + td(days=60)
        cotizacion.save()

        plan = PlanPago.objects.create(cotizacion=cotizacion, activo=True)
        plan.parcialidades.create(
            numero=1, monto=Decimal('500.00'), porcentaje=Decimal('100.00'),
            fecha_limite=date.today() + td(days=45), pagada=False,
        )

        procesar_cargo_efectivo(cotizacion, Decimal('500.00'))

        enviado = mock_post.call_args.kwargs['json']['due_date']
        vence = datetime.strptime(enviado, '%Y-%m-%dT%H:%M:%S').date()
        self.assertLessEqual((vence - date.today()).days, 30)

    @patch('comercial.services_openpay.requests.post')
    def test_puntos_de_tarjeta_se_mandan_solo_si_el_cliente_los_acepta(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx-puntos', 'status': 'completed', 'authorization': '999',
            'card_points': {'caption': 'Usaste 500 puntos. Saldo restante: 1,200.'},
        })
        cotizacion = _crear_cotizacion()

        resultado = procesar_cargo_tarjeta(
            cotizacion, Decimal('500.00'), 'tok-p', 'dev-p', use_card_points=True)
        self.assertTrue(mock_post.call_args.kwargs['json']['use_card_points'])
        # La leyenda del emisor debe llegar al cliente: mostrarla en el
        # comprobante es requisito de la guía de Openpay, no un extra.
        self.assertIn('500 puntos', resultado['mensaje_puntos'])

        mock_post.reset_mock()
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx-sin-puntos', 'status': 'completed', 'authorization': '998',
        })
        procesar_cargo_tarjeta(cotizacion, Decimal('100.00'), 'tok-n', 'dev-n')
        self.assertNotIn('use_card_points', mock_post.call_args.kwargs['json'])

    @patch('comercial.services_openpay.requests.post')
    def test_ficha_paynet_propia_se_genera_y_no_se_filtra_entre_clientes(self, mock_post):
        """
        La ficha con marca propia (paso 3.1 de la guía) se sirve con el token
        del portal. Sin filtrar además por cotización, el token de un cliente
        serviría para leer la ficha de cualquier otro cambiando el id.
        """
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx-ficha-01', 'status': 'in_progress',
            'description': 'COT-001 - Boda',
            'payment_method': {'reference': '1010102410925001',
                               'barcode_url': '', 'due_date': '2026-08-10T23:59:00'},
        })
        cotizacion = _crear_cotizacion()
        resultado = procesar_cargo_efectivo(cotizacion, Decimal('1000.00'))
        self.assertEqual(resultado['openpay_id'], 'tx-ficha-01')

        portal = PortalCliente.objects.get(cotizacion=cotizacion)
        url = reverse('portal_ficha_paynet', args=[portal.token, 'tx-ficha-01'])
        respuesta = Client().get(url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        self.assertTrue(respuesta.content.startswith(b'%PDF'))

        # El token de otro cliente no alcanza la ficha ajena.
        otra = _crear_cotizacion()
        ajeno = PortalCliente.objects.get(cotizacion=otra)
        self.assertEqual(
            Client().get(reverse('portal_ficha_paynet', args=[ajeno.token, 'tx-ficha-01'])).status_code,
            404,
        )

    def test_la_ficha_no_imprime_comentarios_de_plantilla(self):
        """
        Regresión: `{# … #}` solo comenta UNA línea. El bloque de varias
        líneas que documentaba la plantilla se imprimía tal cual encima de la
        ficha, a la vista del cliente.
        """
        from pathlib import Path
        import re

        from django.conf import settings
        from django.template.loader import render_to_string

        plantilla = Path(settings.BASE_DIR) / 'templates' / 'portal' / 'ficha_paynet.html'
        fuente = plantilla.read_text(encoding='utf-8')
        # Un `{#` cuyo `#}` no está en el mismo renglón sale impreso.
        for linea in fuente.splitlines():
            if '{#' in linea:
                self.assertIn('#}', linea, f'comentario multilínea sin cerrar: {linea[:60]}')

        cotizacion = _crear_cotizacion()
        html = render_to_string('portal/ficha_paynet.html', {
            'cotizacion': cotizacion, 'cliente': cotizacion.cliente,
            'monto': Decimal('100.00'), 'referencia': 'REF', 'barcode_url': '',
            'due_date': '', 'descripcion': '', 'emitida': None,
            'logo': '', 'logo_paynet': '', 'tiendas': [],
        })
        cuerpo = re.sub(r'<style.*?</style>', '', html, flags=re.S)
        self.assertNotIn('paso 3.1', cuerpo)
        self.assertNotIn('{%', cuerpo)

    def test_las_librerias_de_openpay_vienen_del_origen_documentado(self):
        """
        El bucket de S3 sirve los mismos archivos pero no está documentado: si
        Openpay lo retira, el checkout deja de tokenizar y no se cobra nada.
        """
        from pathlib import Path

        from django.conf import settings

        plantilla = (Path(settings.BASE_DIR) / 'templates' / 'portal' / 'evento.html')
        contenido = plantilla.read_text(encoding='utf-8')
        self.assertIn('https://js.openpay.mx/openpay.v1.min.js', contenido)
        self.assertIn('https://js.openpay.mx/openpay-data.v1.min.js', contenido)
        self.assertNotIn('openpay.s3.amazonaws.com', contenido)

    @patch('comercial.services_openpay.requests.post')
    def test_efectivo_rechaza_montos_arriba_del_tope_de_openpay(self, mock_post):
        """Openpay tope los cargos en tienda a $29,999.99; se avisa antes de
        salir a la red en vez de dejar que la API responda con un error."""
        cotizacion = _crear_cotizacion()
        resultado = procesar_cargo_efectivo(cotizacion, Decimal('30000.00'))
        self.assertFalse(resultado['ok'])
        self.assertIn('29,999.99', resultado['mensaje'])
        mock_post.assert_not_called()

        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx-tope', 'status': 'in_progress',
            'payment_method': {'reference': 'REF-TOPE', 'barcode_url': ''},
        })
        self.assertTrue(procesar_cargo_efectivo(cotizacion, Decimal('29999.99'))['ok'])

    @patch('comercial.services_openpay.requests.post')
    def test_spei_regresa_url_del_recibo(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx-spei-01', 'status': 'in_progress',
            'payment_method': {'bank': 'BBVA Bancomer', 'clabe': '012180001234567890',
                               'agreement': '1411217', 'name': '11094690394055678934'},
        })
        cotizacion = _crear_cotizacion()
        resultado = procesar_cargo_spei(cotizacion, Decimal('500.00'))
        self.assertTrue(resultado['ok'])
        self.assertIn('/spei-pdf/', resultado['recibo_url'])
        self.assertTrue(resultado['recibo_url'].endswith('/tx-spei-01'))
        # El convenio CIE es lo que pide la banca de BBVA; sin él, un cliente
        # de ese banco no puede completar el pago.
        self.assertEqual(resultado['agreement'], '1411217')

    def test_el_portal_lista_las_tiendas_reales_de_paynet(self):
        """
        El listado anterior era de memoria: nombraba OXXO y Farmacias
        Benavides, que no están afiliadas, y omitía la mitad de las que sí.
        Ahora sale del kit oficial y cada logo debe existir en static/.
        """
        from pathlib import Path

        from django.conf import settings

        from comercial.paynet import TIENDAS_PAYNET

        self.assertGreaterEqual(len(TIENDAS_PAYNET), 19)

        nombres = ' '.join(n for _, n in TIENDAS_PAYNET).upper()
        for ajena in ('OXXO', 'BENAVIDES'):
            self.assertNotIn(ajena, nombres, f'{ajena} no pertenece a la red Paynet')

        for slug, _ in TIENDAS_PAYNET:
            logo = Path(settings.BASE_DIR) / 'static' / 'img' / 'pagos' / 'paynet' / f'{slug}.png'
            self.assertTrue(logo.exists(), f'falta el logo {slug}.png')

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
        response = self.client.post(url, secure=True, data={'metodo': 'store', 'monto': '500.00', 'acepta_legales': '1'})
        self.assertEqual(response.status_code, 404)

    def test_monto_invalido_rechazado(self):
        response = self.client.post(self.url, secure=True, data={'metodo': 'store', 'monto': 'abc', 'acepta_legales': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['ok'])

    def test_monto_mayor_al_saldo_rechazado(self):
        response = self.client.post(self.url, secure=True, data={'metodo': 'store', 'monto': '999999.00', 'acepta_legales': '1'})
        self.assertFalse(response.json()['ok'])
        self.assertIn('saldo pendiente', response.json()['mensaje'])

    def test_metodo_desconocido_rechazado(self):
        response = self.client.post(self.url, secure=True, data={'metodo': 'bitcoin', 'monto': '500.00', 'acepta_legales': '1'})
        self.assertFalse(response.json()['ok'])

    def test_tarjeta_sin_token_rechazada_sin_llamar_openpay(self):
        response = self.client.post(self.url, secure=True, data={'metodo': 'card', 'monto': '500.00', 'acepta_legales': '1'})
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
        response = self.client.post(self.url, secure=True, data={'metodo': 'store', 'monto': '600.00', 'acepta_legales': '1'})
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['reference'], 'OPENPAY05REF')

    def test_segundo_envio_simultaneo_queda_bloqueado_por_candado(self):
        from django.core.cache import cache
        candado = f'pago_openpay_en_curso:{self.cotizacion.id}'
        cache.set(candado, '1', timeout=12)
        try:
            response = self.client.post(self.url, secure=True, data={'metodo': 'store', 'monto': '600.00', 'acepta_legales': '1'})
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
        self.client.post(self.url, secure=True, data={'metodo': 'store', 'monto': '600.00', 'acepta_legales': '1'})
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
    Privacidad y Términos y Condiciones, con mención expresa de Openpay.

    Desde el módulo `legal`, el contenido vive en base de datos y las rutas
    públicas se conservaron para no romper los enlaces ya difundidos."""

    def _publicar(self, tipo, contenido):
        from datetime import date as _date
        from legal.models import DocumentoLegal
        return DocumentoLegal.objects.create(
            tipo=tipo, version='1.0', titulo=str(tipo),
            contenido_md=contenido, vigente_desde=_date.today(), vigente=True,
        )

    def test_aviso_privacidad_publico(self):
        from legal.models import TipoDocumento
        self._publicar(TipoDocumento.AVISO_PRIVACIDAD,
                       'Aviso de Privacidad. Transferencias a Openpay, S.A. de C.V.')
        response = self.client.get(reverse('legal:aviso_privacidad'), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Aviso de Privacidad')
        self.assertContains(response, 'Openpay')

    def test_terminos_publicados_mencionan_openpay(self):
        from legal.models import TipoDocumento
        self._publicar(TipoDocumento.TERMINOS,
                       'Las transacciones serán efectuadas mediante la pasarela de Openpay.')
        response = self.client.get(reverse('legal:terminos'), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'pasarela de Openpay')

    def test_el_documento_vigente_incluye_la_mencion_exigida_por_openpay(self):
        """
        El texto que pidió Openpay debe estar en el archivo que se siembra, no
        solo en un fixture de prueba. Se resuelve el archivo desde el propio
        seed en vez de nombrarlo: fijar 'terminos_v2.0.md' hacía que la prueba
        siguiera validando una versión retirada al publicar la siguiente.
        """
        from legal.management.commands.seed_documentos_legales import (
            DIRECTORIO, DOCUMENTOS,
        )
        from legal.models import TipoDocumento

        archivo = next(a for a, (tipo, _) in DOCUMENTOS.items()
                       if tipo == TipoDocumento.TERMINOS)
        ruta = DIRECTORIO / archivo
        self.assertTrue(ruta.exists(), f'falta el documento de términos {archivo}')
        contenido = ruta.read_text(encoding='utf-8')
        self.assertIn(
            'Las transacciones serán efectuadas mediante la pasarela de Openpay.',
            contenido,
            'Openpay exigió este texto literal en la validación técnica.',
        )

    def test_portal_enlaza_las_paginas_legales(self):
        cotizacion = _crear_cotizacion()
        portal = PortalCliente.objects.get(cotizacion=cotizacion)
        response = self.client.get(reverse('portal_evento', args=[portal.token]), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('legal:aviso_privacidad'))
        self.assertContains(response, reverse('legal:terminos'))


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


class ConsistenciaPreciosTest(TestCase):
    """I1: el precio exhibido es exactamente el que se cobra."""

    def test_total_del_cotizador_coincide_con_precio_final(self):
        from core_erp import impuestos
        cotizacion = _crear_cotizacion(monto_items=Decimal('100.05'))
        # El total que el endpoint del cotizador exhibiría para esa misma base
        base = Decimal(cotizacion.subtotal) - Decimal(cotizacion.descuento)
        exhibido = impuestos.total_desde_bases([base])
        self.assertEqual(exhibido, cotizacion.precio_final)

    def test_tres_items_de_100_05_sin_desviacion_visible(self):
        from core_erp import impuestos
        cliente = Cliente.objects.create(nombre='Cliente 3x', tipo_persona='FISICA')
        cot = Cotizacion.objects.create(
            cliente=cliente, nombre_evento='Tres lineas',
            fecha_evento=date.today() + timedelta(days=30), incluye_refrescos=False,
        )
        for i in range(3):
            ItemCotizacion.objects.create(
                cotizacion=cot, descripcion=f'Linea {i}',
                cantidad=1, precio_unitario=Decimal('100.05'),
            )
        cot.save(); cot.refresh_from_db()
        base = Decimal(cot.subtotal) - Decimal(cot.descuento)
        self.assertEqual(impuestos.total_desde_bases([base]), cot.precio_final)
        # Y difiere de sumar linea por linea, que es justo lo que se evita
        por_linea = sum(impuestos.con_iva(Decimal('100.05')) for _ in range(3))
        self.assertNotEqual(por_linea, cot.precio_final)

    def test_monto_enviado_a_openpay_es_el_saldo_exhibido(self):
        cotizacion = _crear_cotizacion()
        portal = PortalCliente.objects.get(cotizacion=cotizacion)
        response = self.client.get(reverse('portal_evento', args=[portal.token]), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['saldo_pendiente'], cotizacion.saldo_pendiente()
        )
        # Es el mismo valor que alimenta el data-max del campo de monto
        self.assertContains(response, 'IVA incluido')


class TotalCotizadorCoincideTest(TestCase):
    """El total exhibido en el cotizador debe ser EXACTAMENTE el de la
    cotización que se crea al enviar (art. 7 BIS LFPC: el precio exhibido es
    el precio cobrado)."""

    def _crear_producto(self, nombre, precio, **kw):
        from comercial.models import Producto
        return Producto.objects.create(
            nombre=nombre, precio_venta_fijo=Decimal(precio),
            visible_cotizador=True, cotizador_evento=True, **kw
        )

    def test_endpoint_incluye_las_horas_extra(self):
        """Regresion: el endpoint solo sumaba paquete + extras, y omitia las
        horas extra que si se cobran, exhibiendo un total MENOR al cobrado."""
        from comercial.views_cotizador import _lineas_cotizador
        paquete = self._crear_producto('Paquete Test', '10000.00', es_paquete=True)
        self._crear_producto('Hora Extra De Arrendamiento', '1000.00')

        sin_extra = _lineas_cotizador(
            servicio='EVENTO', paquete_id=paquete.id, extras_ids=[],
            num_personas=50, horas_evento=6,
        )
        con_extra = _lineas_cotizador(
            servicio='EVENTO', paquete_id=paquete.id, extras_ids=[],
            num_personas=50, horas_evento=9,
        )
        self.assertEqual(len(sin_extra), 1)
        self.assertEqual(len(con_extra), 2, 'faltan las horas extra en el total')
        self.assertEqual(con_extra[1][1], 3, 'deben ser 3 horas extra')

    def test_ruta_personalizar_incluye_el_paquete_esencial(self):
        """Sin paquete elegido, EVENTO agrega Paquete Esencial automaticamente:
        el total exhibido debe contemplarlo."""
        from comercial.views_cotizador import _lineas_cotizador
        self._crear_producto('Paquete Esencial', '5000.00')
        lineas = _lineas_cotizador(
            servicio='EVENTO', paquete_id=None, extras_ids=[],
            num_personas=50, horas_evento=6,
        )
        self.assertEqual(len(lineas), 1)
        self.assertEqual(lineas[0][0].nombre, 'Paquete Esencial')

    def test_pasadia_incluye_su_paquete(self):
        from comercial.views_cotizador import _lineas_cotizador
        p = self._crear_producto('Pasadia', '3000.00')
        p.cotizador_pasadia = True; p.save()
        lineas = _lineas_cotizador(
            servicio='PASADIA', paquete_id=None, extras_ids=[],
            num_personas=50, horas_evento=9,
        )
        self.assertEqual(len(lineas), 1)

    def test_total_exhibido_igual_al_precio_final(self):
        """La prueba de fuego: el total del endpoint == precio_final real."""
        from core_erp import impuestos
        from comercial.views_cotizador import _lineas_cotizador
        paquete = self._crear_producto('Paquete Bodas', '104681.04', es_paquete=True)
        extra = self._crear_producto('Mobiliario', '1045.00')

        lineas = _lineas_cotizador(
            servicio='EVENTO', paquete_id=paquete.id, extras_ids=[extra.id],
            num_personas=50, horas_evento=6,
        )
        bases = [Decimal(str(pr.sugerencia_precio())) * Decimal(q) for pr, q, _ in lineas]
        exhibido = impuestos.total_desde_bases(bases)

        # Se crea la cotizacion con esas mismas lineas
        cliente = Cliente.objects.create(nombre='Cliente 7BIS', tipo_persona='FISICA')
        cot = Cotizacion.objects.create(
            cliente=cliente, nombre_evento='Bodas',
            fecha_evento=date.today() + timedelta(days=60), incluye_refrescos=False,
        )
        for prod, qty, _desc in lineas:
            ItemCotizacion.objects.create(
                cotizacion=cot, producto=prod, descripcion=prod.nombre,
                cantidad=Decimal(qty), precio_unitario=Decimal(str(prod.sugerencia_precio())),
            )
        cot.save(); cot.refresh_from_db()
        self.assertEqual(exhibido, cot.precio_final)


class Retorno3DSExtremoAExtremoTest(TestCase):
    """
    Simula el viaje completo de 3D Secure sin tocar Openpay: el cliente paga,
    Openpay deja el cargo en 'charge_pending', el banco lo autentica y lo
    regresa a /pago-3ds/, y ahí el ERP consulta el cargo y crea el Pago.

    Openpay no documenta con qué nombre devuelve el id del cargo en el query
    string, así que se prueba con cada variante Y sin ninguna: en ese último
    caso la vista debe resolverlo con el cargo pendiente que ella misma
    registró antes de mandar al cliente al banco.
    """

    def setUp(self):
        from django.core.cache import cache
        # El bucket del rate limiting vive en el mismo cache de proceso y no se
        # reinicia entre tests: sin esto, esta clase acumularía las peticiones
        # de las anteriores y acabaría recibiendo 429 en vez del flujo real.
        for llave in ('portal_pago_openpay', 'portal_retorno_3ds'):
            cache.delete(f'rl:{llave}:127.0.0.1')

        self.cotizacion = _crear_cotizacion()
        self.portal = PortalCliente.objects.get(cotizacion=self.cotizacion)
        self.url_pago = reverse('portal_procesar_pago_openpay', args=[self.portal.token])
        self.url_retorno = reverse('portal_retorno_3ds', args=[self.portal.token])

    def _iniciar_cargo(self, mock_post, openpay_id):
        """Paso 1: el cliente envía la tarjeta y Openpay pide autenticación."""
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'id': openpay_id, 'status': 'charge_pending',
            'payment_method': {'type': 'redirect',
                               'url': f'https://sandbox-api.openpay.mx/3ds/{openpay_id}'},
        })
        response = self.client.post(self.url_pago, secure=True, data={
            'metodo': 'card', 'monto': '600.00', 'token_id': 'tok_3ds',
            'device_session_id': 'dev_3ds', 'acepta_legales': '1',
        })
        datos = response.json()
        self.assertTrue(datos['ok'])
        self.assertIn('/3ds/', datos['redirect_3ds'])
        # Todavía no hay Pago: el dinero no se ha cobrado.
        self.assertFalse(Pago.objects.filter(cotizacion=self.cotizacion).exists())

    def _cargo_autorizado(self, openpay_id):
        return MagicMock(status_code=200, json=lambda: {
            'id': openpay_id, 'status': 'completed', 'amount': 600.00,
            'authorization': '801585', 'card': {'brand': 'visa'},
        })

    @patch('comercial.services_openpay.requests.get')
    @patch('comercial.services_openpay.requests.post')
    def test_el_pago_se_registra_venga_como_venga_el_id(self, mock_post, mock_get):
        for i, parametro in enumerate(('id', 'transaction_id', 'charge_id')):
            with self.subTest(parametro=parametro):
                Pago.objects.all().delete()
                OpenpayTransaccion.objects.all().delete()
                openpay_id = f'tx3ds_param_{i}'

                self._iniciar_cargo(mock_post, openpay_id)
                mock_get.return_value = self._cargo_autorizado(openpay_id)

                response = self.client.get(
                    self.url_retorno, {parametro: openpay_id}, secure=True)

                self.assertEqual(response.status_code, 302)
                self.assertIn('pago=exitoso', response.url)
                self.assertTrue(
                    Pago.objects.filter(cotizacion=self.cotizacion,
                                        referencia=openpay_id).exists())

    @patch('comercial.services_openpay.requests.get')
    @patch('comercial.services_openpay.requests.post')
    def test_el_pago_se_registra_aunque_openpay_no_devuelva_el_id(self, mock_post, mock_get):
        """El caso que no se puede predecir sin el sandbox: parámetro con otro
        nombre, o sin parámetros. Se resuelve con el cargo pendiente."""
        self._iniciar_cargo(mock_post, 'tx3ds_sin_param')
        mock_get.return_value = self._cargo_autorizado('tx3ds_sin_param')

        response = self.client.get(
            self.url_retorno, {'nombre_inesperado': 'tx3ds_sin_param'}, secure=True)

        self.assertEqual(response.status_code, 302)
        self.assertIn('pago=exitoso', response.url)
        self.assertTrue(
            Pago.objects.filter(cotizacion=self.cotizacion,
                                referencia='tx3ds_sin_param').exists())

    @patch('comercial.services_openpay.requests.get')
    @patch('comercial.services_openpay.requests.post')
    def test_recargar_la_pagina_de_retorno_no_duplica_el_pago(self, mock_post, mock_get):
        self._iniciar_cargo(mock_post, 'tx3ds_recarga')
        mock_get.return_value = self._cargo_autorizado('tx3ds_recarga')

        self.client.get(self.url_retorno, {'id': 'tx3ds_recarga'}, secure=True)
        self.client.get(self.url_retorno, {'id': 'tx3ds_recarga'}, secure=True)

        self.assertEqual(
            Pago.objects.filter(cotizacion=self.cotizacion,
                                referencia='tx3ds_recarga').count(), 1)

    @patch('comercial.services_openpay.requests.get')
    @patch('comercial.services_openpay.requests.post')
    def test_autenticacion_fallida_no_cobra_y_avisa_al_cliente(self, mock_post, mock_get):
        self._iniciar_cargo(mock_post, 'tx3ds_rechazo')
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {
            'id': 'tx3ds_rechazo', 'status': 'failed', 'amount': 600.00,
            'error_code': 3005,
            'description': 'The card was declined by the fraud system',
        })

        response = self.client.get(
            self.url_retorno, {'id': 'tx3ds_rechazo'}, secure=True)

        self.assertEqual(response.status_code, 302)
        self.assertIn('pago=rechazado', response.url)
        self.assertFalse(Pago.objects.filter(cotizacion=self.cotizacion).exists())

    def test_retorno_sin_cargo_pendiente_no_inventa_un_pago(self):
        response = self.client.get(self.url_retorno, secure=True)

        self.assertEqual(response.status_code, 302)
        self.assertIn('pago=error', response.url)
        self.assertFalse(Pago.objects.filter(cotizacion=self.cotizacion).exists())
