"""Traspasos entre cuentas propias (Issue #499, PR 2): cuenta puente, orden y reclasificación del histórico.
Todos los números de cuenta son ficticios."""
from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from comercial.models import Cliente, Cotizacion, Pago
from comunicacion.models import Responsable

from .models import (
    AbonoIdentificado,
    ConfiguracionContable,
    CuentaBancaria,
    CuentaContable,
    EstadoCuentaBancario,
    MovimientoEstadoCuenta,
    Poliza,
    ReglaConciliacion,
    SaldoApertura,
    UnidadNegocio,
)
from .services_estados_cuenta import analizar_conciliacion, emparejar_y_asentar, saldo_traspasos_en_transito
from .services_reglas_banco import crear_poliza_desde_movimiento, es_traspaso_propio, usuario_sistema
from .tests import _crear_cotizacion

NUM_COBRO = '1110000001'
NUM_PAGO = '1110000002'


class TraspasosBase(TestCase):
    def setUp(self):
        self.unidad = UnidadNegocio.objects.get(clave='QUINTA')
        CuentaBancaria.objects.filter(unidad_negocio=self.unidad).update(activa=False)
        self.contable_cobro = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.contable_pago = CuentaContable.objects.get(codigo_sat='102.02.04')
        self.puente = ConfiguracionContable.obtener_cuenta('TRASPASOS_EN_TRANSITO')
        self.cobro = CuentaBancaria.objects.create(
            nombre='Ingresos', banco='BBVA', clabe='012345678901230001', numero_cuenta=NUM_COBRO,
            cuenta_contable=self.contable_cobro, unidad_negocio=self.unidad, rol='COBRO')
        self.pago = CuentaBancaria.objects.create(
            nombre='Egresos', banco='BBVA', clabe='012345678901230002', numero_cuenta=NUM_PAGO,
            cuenta_contable=self.contable_pago, unidad_negocio=self.unidad, rol='PAGO')
        enviar = patch('comunicacion.services_pendientes._enviar_aviso')
        enviar.start()
        self.addCleanup(enviar.stop)

    def _estado(self, cuenta, mes=11):
        return EstadoCuentaBancario.objects.create(
            cuenta_bancaria=cuenta, banco='BBVA', periodo_mes=mes, periodo_anio=2026, formato='PDF',
            estado='PROCESADO', fecha_corte_real=date(2026, mes, 28), saldo_inicial_estado=Decimal('0.00'),
            saldo_final_estado=Decimal('0.00'))

    def _mov(self, estado, descripcion, *, cargo='0', abono='0', dia=5):
        return MovimientoEstadoCuenta.objects.create(
            estado_cuenta=estado, fecha=date(estado.periodo_anio, estado.periodo_mes, dia),
            descripcion=descripcion, cargo=Decimal(cargo), abono=Decimal(abono))

    def _poliza_de(self, mov):
        return Poliza.objects.get(object_id=mov.pk, origen='BANCO')


class PuenteYDeteccionTest(TraspasosBase):
    def test_cuenta_puente_y_configuracion_sembradas(self):
        cuenta = CuentaContable.objects.get(codigo_sat='102.03')
        self.assertEqual((cuenta.tipo, cuenta.naturaleza, cuenta.nivel, cuenta.padre.codigo_sat), ('ACTIVO', 'D', 3, '102'))
        self.assertEqual(self.puente, cuenta)

    def test_detecta_la_otra_cuenta_por_numero_o_clabe_y_nunca_la_propia(self):
        estado = self._estado(self.cobro)
        a_pago = self._mov(estado, f'SPEI ENVIADO FONDEO {NUM_PAGO} BNET', cargo='5000')
        a_clabe = self._mov(estado, 'SPEI ENVIADO 012345678901230002', cargo='5000')
        a_si_misma = self._mov(estado, f'REFERENCIA {NUM_COBRO}', cargo='5000')
        self.assertEqual(es_traspaso_propio(a_pago), self.pago)
        self.assertEqual(es_traspaso_propio(a_clabe), self.pago)
        self.assertIsNone(es_traspaso_propio(a_si_misma))

    def test_un_numero_dentro_de_una_referencia_mas_larga_no_cuenta(self):
        estado = self._estado(self.cobro)
        mov = self._mov(estado, f'SPEI RECIBIDO REF 99{NUM_PAGO}77 CLIENTE', abono='5000')
        self.assertIsNone(es_traspaso_propio(mov))


class AsientoDeTraspasosTest(TraspasosBase):
    def _lineas(self, mov):
        return {(m.cuenta.codigo_sat, m.debe, m.haber) for m in self._poliza_de(mov).movimientos.all()}

    def test_cada_punta_se_asienta_contra_la_puente_en_cualquier_orden_y_mes(self):
        # El abono de la pagadora llega en un estado ANTERIOR al cargo de la cobro (orden inverso al natural).
        abono = self._mov(self._estado(self.pago, mes=10), f'SPEI RECIBIDO {NUM_COBRO}', abono='5000')
        emparejar_y_asentar(abono.estado_cuenta)
        self.assertEqual(saldo_traspasos_en_transito(), Decimal('-5000.00'))  # una sola punta: puente ≠ 0

        cargo = self._mov(self._estado(self.cobro, mes=11), f'SPEI ENVIADO {NUM_PAGO}', cargo='5000')
        emparejar_y_asentar(cargo.estado_cuenta)
        self.assertEqual(saldo_traspasos_en_transito(), Decimal('0.00'))

        self.assertEqual(self._lineas(cargo), {('102.03', Decimal('5000.00'), Decimal('0.00')),
                                               ('102.02.01', Decimal('0.00'), Decimal('5000.00'))})
        self.assertEqual(self._lineas(abono), {('102.02.04', Decimal('5000.00'), Decimal('0.00')),
                                               ('102.03', Decimal('0.00'), Decimal('5000.00'))})
        for mov in (cargo, abono):
            mov.refresh_from_db()
            self.assertIsNotNone(mov.movimiento_contable_id)
        # Cero gasto e ingreso: solo bancos y la puente.
        self.assertFalse(Poliza.objects.filter(movimientos__cuenta__tipo__in=['GASTO', 'INGRESO', 'COSTO']).exists())

    def test_una_sola_punta_deja_la_puente_con_saldo(self):
        cargo = self._mov(self._estado(self.cobro), f'SPEI ENVIADO {NUM_PAGO}', cargo='3000')
        emparejar_y_asentar(cargo.estado_cuenta)
        self.assertEqual(saldo_traspasos_en_transito(), Decimal('3000.00'))
        self.assertEqual(analizar_conciliacion(cargo.estado_cuenta)['traspasos_en_transito'], Decimal('3000.00'))

    def test_reprocesar_no_duplica(self):
        cargo = self._mov(self._estado(self.cobro), f'SPEI ENVIADO {NUM_PAGO}', cargo='3000')
        emparejar_y_asentar(cargo.estado_cuenta)
        emparejar_y_asentar(cargo.estado_cuenta)
        self.assertEqual(Poliza.objects.filter(object_id=cargo.pk, origen='BANCO').count(), 1)

    def test_un_abono_de_fondeo_no_se_propone_como_pago_de_cliente(self):
        Responsable.objects.create(rol='DIRECCION', nombre='Dir', telefono_wa='525511112222')
        cot = _crear_cotizacion(Cliente.objects.create(nombre='Cliente Ficticio'), Decimal('5000.00'))
        estado = self._estado(self.pago)
        self._mov(estado, f'SPEI RECIBIDO {NUM_COBRO} FONDEO', abono='5000')  # mismo monto que el saldo de la cotización
        emparejar_y_asentar(estado)
        self.assertFalse(AbonoIdentificado.objects.exists())
        self.assertFalse(Pago.objects.filter(cotizacion=cot).exists())
        self.assertEqual(Cotizacion.objects.get(pk=cot.pk).saldo_pendiente(), Decimal('5000.00'))

    def test_el_fallback_por_regla_con_operacion_puente_tambien_asienta(self):
        ReglaConciliacion.objects.create(
            nombre='Fondeo manual', patrones='FONDEO*', operacion='TRASPASOS_EN_TRANSITO', origen='MANUAL')
        estado = self._estado(self.cobro)
        mov = self._mov(estado, 'ENVIO A MI OTRA CUENTA FONDEO', cargo='1000')  # sin número de cuenta
        emparejar_y_asentar(estado)
        self.assertEqual(self._poliza_de(mov).movimientos.get(debe__gt=0).cuenta, self.puente)


class ReclasificacionHistoricaTest(TraspasosBase):
    def setUp(self):
        super().setUp()
        self.retiros = ConfiguracionContable.obtener_cuenta('RETIROS_DUENO')
        self.estado = self._estado(self.cobro)
        self.mov = self._mov(self.estado, f'SPEI ENVIADO {NUM_PAGO}', cargo='4000')
        # Como quedó antes de este PR: retiro del dueño.
        crear_poliza_desde_movimiento(self.mov, self.retiros, usuario_sistema(), aplicar=True)

    def _contrapartida(self):
        return self._poliza_de(self.mov).movimientos.exclude(cuenta=self.contable_cobro).get().cuenta

    def test_simulacion_no_escribe_y_aplicar_reapunta_sin_tocar_importes(self):
        salida = StringIO()
        call_command('reclasificar_traspasos_propios', stdout=salida)
        self.assertEqual(self._contrapartida(), self.retiros)
        self.assertIn('Simulación', salida.getvalue())

        poliza = self._poliza_de(self.mov)
        folio = poliza.folio
        call_command('reclasificar_traspasos_propios', '--aplicar', stdout=StringIO())
        self.assertEqual(self._contrapartida(), self.puente)
        poliza.refresh_from_db()
        self.assertEqual((poliza.folio, poliza.estado, poliza.total_debe, poliza.total_haber),
                         (folio, 'APLICADA', Decimal('4000.00'), Decimal('4000.00')))
        self.assertEqual(Poliza.objects.filter(object_id=self.mov.pk, origen='BANCO').count(), 1)

    def test_periodo_cerrado_se_lista_y_no_se_toca(self):
        from django.contrib.auth import get_user_model
        SaldoApertura.objects.create(
            cuenta_bancaria=self.cobro, fecha_corte=date(2026, 12, 31), saldo_certificado=Decimal('0.00'),
            certificado_por=get_user_model().objects.create_user('contador_ficticio', password='x'),  # noqa: S106
            aplicado=True)
        salida = StringIO()
        call_command('reclasificar_traspasos_propios', '--aplicar', stdout=salida)
        self.assertEqual(self._contrapartida(), self.retiros)
        self.assertIn('periodo ya cerrado', salida.getvalue())

    def test_un_movimiento_que_no_es_traspaso_no_se_toca(self):
        otro = self._mov(self.estado, 'RETIRO CAJERO SIN CUENTA PROPIA', cargo='700', dia=9)
        crear_poliza_desde_movimiento(otro, self.retiros, usuario_sistema(), aplicar=True)
        call_command('reclasificar_traspasos_propios', '--aplicar', stdout=StringIO())
        self.assertEqual(self._poliza_de(otro).movimientos.exclude(cuenta=self.contable_cobro).get().cuenta, self.retiros)
