"""
Depósito en garantía (Issue #318, fase 3): no es Pago, se cobra aparte por
Openpay o a mano, y cada movimiento genera su póliza contra 205.03.

Ejecutar: python manage.py test comercial.test_deposito_garantia
"""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.test import TestCase

from comercial.models import (
    Cliente,
    Cotizacion,
    DepositoGarantia,
    ItemCotizacion,
    MovimientoDeposito,
    OpenpayTransaccion,
    Pago,
    PortalCliente,
)
from comercial.services_deposito import DepositoError, asegurar_deposito, liquidar, registrar_recepcion
from comercial.services_openpay import (
    _confirmar_cargo_completado,
    monto_en_camino,
    procesar_webhook_openpay,
)
from contabilidad.models import Poliza


def _cotizacion():
    cliente = Cliente.objects.create(nombre='Ana Ruiz', tipo_persona='FISICA', email='ana@example.com')
    cot = Cotizacion.objects.create(
        cliente=cliente, nombre_evento='Boda', tipo_servicio='EVENTO',
        fecha_evento=date.today() + timedelta(days=60), incluye_refrescos=False,
    )
    ItemCotizacion.objects.create(cotizacion=cot, descripcion='Evento', cantidad=1, precio_unitario=Decimal('10000.00'))
    cot.identificacion_oficial.name = 'cotizaciones/identificaciones/test-ine.jpg'
    cot.save()
    cot.refresh_from_db()
    return cot


def _movimientos_poliza(mov):
    poliza = Poliza.objects.get(content_type=ContentType.objects.get_for_model(mov), object_id=mov.pk)
    return poliza, {m.cuenta.codigo_sat: (m.debe, m.haber) for m in poliza.movimientos.all()}


class DepositoBase(TestCase):
    def setUp(self):
        self.cot = _cotizacion()
        self.deposito = asegurar_deposito(self.cot, Decimal('1160.00'))


class AsegurarDepositoTest(DepositoBase):
    def test_ajusta_el_monto_solo_mientras_no_se_recibe_nada(self):
        asegurar_deposito(self.cot, Decimal('2000.00'))
        self.deposito.refresh_from_db()
        self.assertEqual(self.deposito.monto, Decimal('2000.00'))
        registrar_recepcion(self.deposito, monto=Decimal('2000.00'), metodo='TRANSFERENCIA')
        asegurar_deposito(self.cot, Decimal('500.00'))
        self.deposito.refresh_from_db()
        self.assertEqual(self.deposito.monto, Decimal('2000.00'))

    def test_monto_cero_no_crea_deposito(self):
        otra = _cotizacion()
        self.assertIsNone(asegurar_deposito(otra, Decimal('0')))
        self.assertFalse(DepositoGarantia.objects.filter(cotizacion=otra).exists())


class RecepcionTest(DepositoBase):
    def test_recepcion_es_pasivo_y_no_toca_el_saldo(self):
        saldo = self.cot.saldo_pendiente()
        mov = registrar_recepcion(self.deposito, monto=Decimal('1160.00'), metodo='TRANSFERENCIA', referencia='SPEI123')

        self.cot.refresh_from_db()
        self.assertEqual(self.cot.saldo_pendiente(), saldo)
        self.assertFalse(Pago.objects.filter(cotizacion=self.cot).exists())
        self.assertEqual(self.deposito.estado, 'EN_CUSTODIA')

        poliza, lineas = _movimientos_poliza(mov)
        self.assertEqual(poliza.tipo, 'I')
        self.assertEqual(lineas['205.03'], (Decimal('0.00'), Decimal('1160.00')))

    def test_no_acepta_mas_de_lo_pendiente(self):
        with self.assertRaisesMessage(DepositoError, 'excede'):
            registrar_recepcion(self.deposito, monto=Decimal('5000.00'), metodo='EFECTIVO')


class LiquidacionTest(DepositoBase):
    def setUp(self):
        super().setUp()
        registrar_recepcion(self.deposito, monto=Decimal('1160.00'), metodo='TRANSFERENCIA')

    def test_devolucion_total(self):
        with self.captureOnCommitCallbacks(execute=True):
            resultado = liquidar(self.deposito, referencia='SPEI-DEV')
        self.assertEqual(resultado['devuelto'], Decimal('1160.00'))
        self.deposito.refresh_from_db()
        self.assertEqual(self.deposito.estado, 'LIQUIDADO')
        dev = self.deposito.movimientos.get(tipo='DEVOLUCION')
        poliza, lineas = _movimientos_poliza(dev)
        self.assertEqual(poliza.tipo, 'E')
        self.assertEqual(lineas['205.03'], (Decimal('1160.00'), Decimal('0.00')))
        self.assertIn('depósito', mail.outbox[-1].subject)

    def test_retenciones_con_y_sin_iva(self):
        liquidar(self.deposito, retencion_danos=Decimal('300.00'),
                 retencion_servicio=Decimal('116.00'), desglose='Silla rota; 1 h de limpieza extra.')
        danos = self.deposito.movimientos.get(tipo='RETENCION_DANOS')
        _, lineas = _movimientos_poliza(danos)
        self.assertEqual(lineas['402.02'], (Decimal('0.00'), Decimal('300.00')))  # sin IVA

        servicio = self.deposito.movimientos.get(tipo='RETENCION_SERVICIO')
        _, lineas = _movimientos_poliza(servicio)
        self.assertEqual(lineas['208.01'], (Decimal('0.00'), Decimal('16.00')))   # IVA
        self.assertEqual(lineas['205.03'], (Decimal('116.00'), Decimal('0.00')))

        self.assertEqual(self.deposito.movimientos.get(tipo='DEVOLUCION').monto, Decimal('744.00'))

    def test_retencion_exige_desglose_y_no_excede_custodia(self):
        with self.assertRaisesMessage(DepositoError, 'desglose'):
            liquidar(self.deposito, retencion_danos=Decimal('100.00'))
        with self.assertRaisesMessage(DepositoError, 'excede'):
            liquidar(self.deposito, retencion_danos=Decimal('5000.00'), desglose='x')

    def test_no_se_liquida_dos_veces(self):
        liquidar(self.deposito)
        with self.assertRaisesMessage(DepositoError, 'ya se liquidó'):
            liquidar(self.deposito)


class OpenpayDepositoTest(DepositoBase):
    def _registro(self, metodo='card', openpay_id='tx-dep-1', **kwargs):
        return OpenpayTransaccion.objects.create(
            openpay_id=openpay_id, metodo=metodo, monto=Decimal('1160.00'),
            cotizacion=self.cot, payload_crudo={}, destino='DEPOSITO', **kwargs,
        )

    def test_cargo_con_tarjeta_registra_movimiento_y_no_pago(self):
        registro = self._registro(procesado=True, estado_openpay='completed')
        _confirmar_cargo_completado(registro, self.cot, Decimal('1160.00'), {}, 'card')
        registro.refresh_from_db()
        self.assertTrue(registro.procesado)
        self.assertIsNone(registro.pago_id)
        self.assertEqual(registro.movimiento_deposito.monto, Decimal('1160.00'))
        # Idempotente: un segundo aviso no duplica.
        _confirmar_cargo_completado(registro, self.cot, Decimal('1160.00'), {}, 'card')
        self.assertEqual(MovimientoDeposito.objects.count(), 1)

    def test_webhook_infiere_el_destino_del_order_id(self):
        procesar_webhook_openpay({'type': 'charge.succeeded', 'transaction': {
            'id': 'tx-spei-dep', 'status': 'completed', 'amount': 1160.00,
            'method': 'bank_account', 'order_id': f'COT-{self.cot.id}-DEPabc123def456',
        }})
        registro = OpenpayTransaccion.objects.get(openpay_id='tx-spei-dep')
        self.assertEqual(registro.destino, 'DEPOSITO')
        self.assertTrue(hasattr(registro, 'movimiento_deposito'))
        self.assertFalse(Pago.objects.filter(cotizacion=self.cot).exists())

    def test_referencias_del_deposito_no_bloquean_el_saldo(self):
        self._registro(metodo='store', openpay_id='ref-dep', estado_openpay='in_progress')
        self.assertEqual(monto_en_camino(self.cot), Decimal('0.00'))
        self.assertEqual(monto_en_camino(self.cot, 'DEPOSITO'), Decimal('1160.00'))

    @patch('comercial.services_openpay.requests.post')
    def test_devolucion_por_openpay_reembolsa_el_monto(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {})
        registro = self._registro(procesado=True, estado_openpay='completed')
        _confirmar_cargo_completado(registro, self.cot, Decimal('1160.00'), {}, 'card')

        liquidar(self.deposito, retencion_danos=Decimal('160.00'), desglose='Mantel', reembolsar_openpay=True)
        url, = mock_post.call_args.args
        self.assertTrue(url.endswith('/tx-dep-1/refund'))
        self.assertEqual(mock_post.call_args.kwargs['json']['amount'], 1000.0)
        self.assertEqual(self.deposito.movimientos.get(tipo='DEVOLUCION').metodo, 'PLATAFORMA')

    def test_devolucion_openpay_sin_tarjeta_se_rechaza(self):
        registrar_recepcion(self.deposito, monto=Decimal('1160.00'), metodo='EFECTIVO')
        with self.assertRaisesMessage(DepositoError, 'con tarjeta'):
            liquidar(self.deposito, reembolsar_openpay=True)


class CheckoutDepositoTest(DepositoBase):
    def setUp(self):
        super().setUp()
        self.portal = PortalCliente.objects.get_or_create(cotizacion=self.cot)[0]
        self.url = f'/mi-evento/{self.portal.token}/pagar-openpay/'

    def _post(self, monto):
        return self.client.post(self.url, {
            'metodo': 'bank_account', 'monto': monto, 'destino': 'DEPOSITO', 'acepta_legales': '1',
        }).json()

    def test_exige_el_monto_exacto_del_deposito(self):
        self.assertIn('1,160.00', self._post('500.00')['mensaje'])

    @patch('comercial.views_openpay.procesar_cargo_spei')
    def test_cobra_el_deposito_con_destino(self, mock_spei):
        mock_spei.return_value = {'ok': True}
        self.assertTrue(self._post('1160.00')['ok'])
        self.assertEqual(mock_spei.call_args.kwargs['destino'], 'DEPOSITO')

    def test_el_portal_muestra_el_deposito(self):
        respuesta = self.client.get(f'/mi-evento/{self.portal.token}/')
        self.assertContains(respuesta, 'Depósito en garantía')
