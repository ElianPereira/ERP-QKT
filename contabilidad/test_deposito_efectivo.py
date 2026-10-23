"""
Regla «Depósito de efectivo a banco»: traspaso Caja → Bancos solo cuando el
abono no tiene asiento propio y Caja tiene saldo para respaldarlo.
"""
from datetime import date
from decimal import Decimal

from comercial.models import Cliente, Pago

from .models import ConfiguracionContable, MovimientoContable, Poliza
from .services_estados_cuenta import emparejar_y_asentar
from .test_reglas_banco import ReglasBancoBase
from .tests import _crear_cotizacion

DEPOSITO_PRACTICAJA = 'DEPOSITO EFECTIVO PRACTIC 0012345'


class DepositoEfectivoTest(ReglasBancoBase):

    def setUp(self):
        super().setUp()
        self.caja = ConfiguracionContable.obtener_cuenta('CAJA')
        self.cliente = Cliente.objects.create(nombre='Cliente Efectivo', tipo_persona='FISICA')
        self.cot = _crear_cotizacion(self.cliente, Decimal('1000.00'))

    def _pago(self, metodo, monto='1000.00'):
        return Pago.objects.create(
            cotizacion=self.cot, monto=Decimal(monto), metodo=metodo,
            fecha_pago=date(2026, 8, 10), usuario=self.usuario,
        )

    def test_efectivo_cobrado_y_luego_depositado_genera_traspaso(self):
        self._pago('EFECTIVO')
        mov = self._mov(DEPOSITO_PRACTICAJA, abono='1000.00')
        resumen = emparejar_y_asentar(self.estado, usuario=self.usuario)

        self.assertEqual(resumen['aplicadas'], [mov])
        poliza = self._polizas_banco(mov).get()
        self.assertTrue(poliza.movimientos.filter(cuenta=self.caja, haber=Decimal('1000.00')).exists())
        self.assertTrue(poliza.movimientos.filter(cuenta=self.cuenta_banco, debe=Decimal('1000.00')).exists())

    def test_deposito_directo_del_cliente_se_liga_al_pago_sin_traspaso(self):
        self._pago('DEPOSITO')
        mov = self._mov(DEPOSITO_PRACTICAJA, abono='1000.00')
        emparejar_y_asentar(self.estado, usuario=self.usuario)

        mov.refresh_from_db()
        self.assertIsNotNone(mov.movimiento_contable_id)
        self.assertEqual(mov.movimiento_contable.poliza.origen, 'PAGO_CLIENTE')
        self.assertFalse(self._polizas_banco(mov).exists())

    def test_abono_sin_pago_y_sin_saldo_en_caja_no_genera_poliza(self):
        mov = self._mov(DEPOSITO_PRACTICAJA, abono='1000.00')
        resumen = emparejar_y_asentar(self.estado, usuario=self.usuario)

        self.assertEqual(resumen['sin_saldo_caja'], [mov])
        self.assertFalse(self._polizas_banco(mov).exists())
        mov.refresh_from_db()
        self.assertIsNone(mov.movimiento_contable_id)
        self.assertEqual(MovimientoContable.objects.filter(cuenta=self.caja, haber=Decimal('1000.00')).count(), 0)
        self.assertEqual(Poliza.objects.filter(origen='BANCO').count(), 0)
