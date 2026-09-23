"""
Doble cobro por referencias de efectivo/SPEI vigentes.

Origen: la simulación del flujo completo mostró que con una ficha de efectivo
vigente por el total, el portal aceptaba además el pago con tarjeta; al pagar
la ficha después el dinero entraba a Openpay, el Pago no se registraba (rebasa
el saldo) y nadie se enteraba.

Ejecutar: python manage.py test comercial.test_sobrepago_openpay --verbosity=2
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, ItemCotizacion, OpenpayTransaccion, Pago
from comercial.services_openpay import monto_en_camino, procesar_webhook_openpay


def _cotizacion():
    cliente = Cliente.objects.create(nombre='Cliente Sobrepago', telefono='5555550001')
    cot = Cotizacion.objects.create(
        cliente=cliente, nombre_evento='Evento',
        fecha_evento=timezone.localdate() + timedelta(days=60), incluye_refrescos=False,
    )
    ItemCotizacion.objects.create(
        cotizacion=cot, descripcion='Servicio', cantidad=1, precio_unitario=Decimal('10000.00'),
    )
    cot.identificacion_oficial.name = 'cotizaciones/identificaciones/ine.jpg'
    cot.save()
    cot.refresh_from_db()
    return cot  # precio_final = 11,600.00


def _ficha(cot, monto, openpay_id='tr_ficha', horas=72):
    vence = (timezone.localtime() + timedelta(hours=horas)).strftime('%Y-%m-%dT%H:%M:%S')
    return OpenpayTransaccion.objects.create(
        openpay_id=openpay_id, metodo='store', estado_openpay='in_progress',
        monto=Decimal(monto), cotizacion=cot,
        payload_crudo={'payment_method': {'reference': 'REF', 'due_date': vence}},
    )


def _respuesta(data):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = data
    return r


@override_settings(OPENPAY_MERCHANT_ID='m', OPENPAY_PUBLIC_KEY='pk', OPENPAY_PRIVATE_KEY='sk')
class PortalDescuentaReferenciasVigentesTest(TestCase):

    def setUp(self):
        cache.clear()
        self.cot = _cotizacion()

    def _pagar_tarjeta(self, monto):
        with patch('comercial.services_openpay.requests.post',
                   return_value=_respuesta({'id': 'tr_card', 'status': 'completed'})) as post:
            r = self.client.post(f'/mi-evento/{self.cot.portal.token}/pagar-openpay/', {
                'metodo': 'card', 'monto': str(monto), 'token_id': 't',
                'device_session_id': 'd', 'acepta_legales': '1',
            })
        return r.json(), post

    def test_monto_en_camino_suma_todas_las_vigentes_e_ignora_vencidas(self):
        _ficha(self.cot, '3000.00', 'a')
        _ficha(self.cot, '2000.00', 'b')
        _ficha(self.cot, '9999.00', 'c', horas=-1)
        self.assertEqual(monto_en_camino(self.cot), Decimal('5000.00'))

    def test_ficha_por_el_total_bloquea_pagar_otra_vez_con_tarjeta(self):
        _ficha(self.cot, '11600.00')
        res, post = self._pagar_tarjeta('11600.00')
        self.assertFalse(res['ok'])
        self.assertIn('referencia de pago vigente', res['mensaje'])
        post.assert_not_called()

    def test_con_ficha_parcial_se_puede_pagar_la_diferencia(self):
        _ficha(self.cot, '5800.00')
        res, _ = self._pagar_tarjeta('5800.00')
        self.assertTrue(res['ok'], res)

    def test_ficha_vencida_ya_no_bloquea(self):
        _ficha(self.cot, '11600.00', horas=-1)
        res, _ = self._pagar_tarjeta('11600.00')
        self.assertTrue(res['ok'], res)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class WebhookCobroSinRegistrarTest(TestCase):

    def test_cobro_que_rebasa_el_saldo_alerta_al_equipo(self):
        cot = _cotizacion()
        _ficha(cot, '11600.00')
        Pago.objects.create(cotizacion=cot, monto=Decimal('11600.00'), metodo='TRANSFERENCIA')
        registro = procesar_webhook_openpay({
            'type': 'charge.succeeded',
            'transaction': {'id': 'tr_ficha', 'status': 'completed', 'amount': 11600.0},
        })
        self.assertIsNone(registro.pago_id)
        self.assertIn('excede', registro.error_detalle)
        self.assertTrue(any('sin registrar' in m.subject for m in mail.outbox))
