"""Cambio manual de la cuenta bancaria de un Pago (Issue #499, PR 3). Datos ficticios."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from comercial.models import Cliente, Pago
from core_erp.test_utils import login_superuser_con_totp

from .models import (
    CuentaBancaria,
    CuentaContable,
    EstadoCuentaBancario,
    MovimientoEstadoCuenta,
    Poliza,
    SaldoApertura,
    UnidadNegocio,
)
from .services_cuenta_bancaria import CambioCuentaRechazado, cambiar_cuenta_bancaria
from .tests import _crear_cotizacion


class CambioDeCuentaTest(TestCase):
    def setUp(self):
        self.unidad = UnidadNegocio.objects.get(clave='QUINTA')
        CuentaBancaria.objects.filter(unidad_negocio=self.unidad).update(activa=False)
        self.contable_cobro = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.contable_pago = CuentaContable.objects.get(codigo_sat='102.02.04')
        self.cobro = CuentaBancaria.objects.create(
            nombre='Ingresos', banco='BBVA', clabe='012345678901230001', cuenta_contable=self.contable_cobro,
            unidad_negocio=self.unidad, rol='COBRO')
        self.pagadora = CuentaBancaria.objects.create(
            nombre='Egresos', banco='BBVA', clabe='012345678901230002', cuenta_contable=self.contable_pago,
            unidad_negocio=self.unidad, rol='PAGO')
        self.usuario = get_user_model().objects.create_user('contador_ficticio', password='x')  # noqa: S106
        cot = _crear_cotizacion(Cliente.objects.create(nombre='Cliente Ficticio'), Decimal('11600.00'))
        self.pago = Pago.objects.create(
            cotizacion=cot, monto=Decimal('1000.00'), metodo='TARJETA_DEBITO', comision_tpv=Decimal('23.20'))

    def _lineas(self, origen):
        poliza = Poliza.objects.get(content_type=ContentType.objects.get_for_model(self.pago),
                                    object_id=self.pago.pk, origen=origen)
        return poliza, {m.cuenta.codigo_sat for m in poliza.movimientos.all()}

    def test_sin_conciliar_reapunta_el_cobro_y_su_comision_y_deja_nota(self):
        poliza, _ = self._lineas('PAGO_CLIENTE')
        folio = poliza.folio
        cambiadas = cambiar_cuenta_bancaria(self.pago, self.pagadora, self.usuario)

        self.assertEqual(cambiadas, 2)  # línea del cobro + línea de la comisión TPV
        self.pago.refresh_from_db()
        self.assertEqual(self.pago.cuenta_bancaria, self.pagadora)
        self.assertIn('contador_ficticio', self.pago.notas)
        poliza.refresh_from_db()
        self.assertEqual((poliza.folio, poliza.estado, poliza.total_debe, poliza.total_haber),
                         (folio, 'APLICADA', Decimal('1000.00'), Decimal('1000.00')))
        for origen in ('PAGO_CLIENTE', 'COMISION_TPV'):
            codigos = self._lineas(origen)[1]
            self.assertIn('102.02.04', codigos)
            self.assertNotIn('102.02.01', codigos)

    def test_ya_conciliado_se_rechaza_sin_escribir(self):
        estado = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cobro, banco='BBVA', periodo_mes=11, periodo_anio=2026, formato='PDF',
            estado='PROCESADO', fecha_corte_real=date(2026, 11, 28), saldo_inicial_estado=Decimal('0'),
            saldo_final_estado=Decimal('0'))
        poliza, _ = self._lineas('PAGO_CLIENTE')
        MovimientoEstadoCuenta.objects.create(
            estado_cuenta=estado, fecha=date(2026, 11, 5), descripcion='ABONO', abono=Decimal('1000.00'),
            movimiento_contable=poliza.movimientos.get(cuenta=self.contable_cobro))

        with self.assertRaisesMessage(CambioCuentaRechazado, 'desliga primero'):
            cambiar_cuenta_bancaria(self.pago, self.pagadora, self.usuario)
        self.pago.refresh_from_db()
        self.assertIsNone(self.pago.cuenta_bancaria_id)
        self.assertIn('102.02.01', self._lineas('PAGO_CLIENTE')[1])

    def test_poliza_aplicada_sin_linea_en_la_cuenta_actual_se_rechaza_sin_escribir(self):
        for origen in ('PAGO_CLIENTE', 'COMISION_TPV'):  # pólizas desfasadas: ninguna línea en la cuenta actual
            self._lineas(origen)[0].movimientos.filter(cuenta=self.contable_cobro).update(cuenta=self.contable_pago)
        with self.assertRaisesMessage(CambioCuentaRechazado, 'ninguna línea'):
            cambiar_cuenta_bancaria(self.pago, self.pagadora, self.usuario)
        self.pago.refresh_from_db()
        self.assertIsNone(self.pago.cuenta_bancaria_id)
        self.assertEqual(self.pago.notas, '')

    def test_pago_historico_sin_polizas_si_se_cambia(self):
        Poliza.objects.all().delete()
        self.assertEqual(cambiar_cuenta_bancaria(self.pago, self.pagadora, self.usuario), 0)
        self.pago.refresh_from_db()
        self.assertEqual(self.pago.cuenta_bancaria, self.pagadora)

    def test_periodo_cerrado_se_rechaza(self):
        SaldoApertura.objects.create(
            cuenta_bancaria=self.cobro, fecha_corte=date(2099, 12, 31), saldo_certificado=Decimal('0.00'),
            certificado_por=self.usuario, aplicado=True)
        with self.assertRaisesMessage(CambioCuentaRechazado, 'periodo ya cerrado'):
            cambiar_cuenta_bancaria(self.pago, self.pagadora, self.usuario)

    def test_cuentas_invalidas_o_misma_cuenta_se_rechazan(self):
        with self.assertRaises(CambioCuentaRechazado):
            cambiar_cuenta_bancaria(self.pago, self.cobro, self.usuario)  # ya está ahí (BANCO_PRINCIPAL)
        CuentaBancaria.objects.filter(pk=self.pagadora.pk).update(activa=False)
        self.pagadora.refresh_from_db()
        with self.assertRaises(CambioCuentaRechazado):
            cambiar_cuenta_bancaria(self.pago, self.pagadora, self.usuario)

    def test_el_efectivo_no_tiene_cuenta_bancaria(self):
        efectivo = Pago.objects.create(cotizacion=self.pago.cotizacion, monto=Decimal('100.00'), metodo='EFECTIVO')
        with self.assertRaises(CambioCuentaRechazado):
            cambiar_cuenta_bancaria(efectivo, self.pagadora, self.usuario)


class AccionAdminTest(CambioDeCuentaTest):
    URL = '/admin/comercial/pago/'

    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser('dir_ficticio', 'd@example.com', 'x')  # noqa: S106
        login_superuser_con_totp(self.client, self.admin)

    def test_el_primer_paso_solo_muestra_el_formulario(self):
        r = self.client.post(self.URL, {'action': 'cambiar_cuenta_bancaria', '_selected_action': [self.pago.pk]})
        self.assertContains(r, 'Cuenta bancaria destino')
        self.pago.refresh_from_db()
        self.assertIsNone(self.pago.cuenta_bancaria_id)

    def test_sin_confirmar_o_sin_cuenta_valida_no_escribe(self):
        for datos in ({'confirmar': 'si'}, {'confirmar': 'si', 'cuenta': 99999}, {'cuenta': self.pagadora.pk}):
            self.client.post(self.URL, {'action': 'cambiar_cuenta_bancaria', '_selected_action': [self.pago.pk], **datos})
        self.pago.refresh_from_db()
        self.assertIsNone(self.pago.cuenta_bancaria_id)

    def test_confirmado_cambia_la_cuenta(self):
        r = self.client.post(self.URL, {'action': 'cambiar_cuenta_bancaria', '_selected_action': [self.pago.pk],
                                        'confirmar': 'si', 'cuenta': self.pagadora.pk})
        self.assertEqual(r.status_code, 302)
        self.pago.refresh_from_db()
        self.assertEqual(self.pago.cuenta_bancaria, self.pagadora)
