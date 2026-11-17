"""Traspasos entre cuentas propias (Issue #499, PR 2): cuenta puente, orden y reclasificación del histórico.
Todos los números de cuenta son ficticios."""
import calendar
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
            estado='PROCESADO', fecha_corte_real=date(2026, mes, calendar.monthrange(2026, mes)[1]), saldo_inicial_estado=Decimal('0.00'),
            saldo_final_estado=Decimal('0.00'))

    def _mov(self, estado, descripcion, *, cargo='0', abono='0', dia=5):
        return MovimientoEstadoCuenta.objects.create(
            estado_cuenta=estado, fecha=date(2026, estado.periodo_mes, dia),
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

    def test_la_cuenta_dentro_de_la_clabe_son_los_digitos_7_a_17(self):
        # CLABE 012 345 67890123000 2: la cuenta de 10 dígitos es clabe[7:17]; los últimos 10 nunca coinciden.
        self.pago.numero_cuenta = ''
        self.pago.save()
        estado = self._estado(self.cobro)
        buena = self._mov(estado, 'SPEI ENVIADO 7890123000 BNET', cargo='100')
        mala = self._mov(estado, 'SPEI ENVIADO 8901230002 BNET', cargo='100')
        self.assertEqual(es_traspaso_propio(buena), self.pago)
        self.assertIsNone(es_traspaso_propio(mala))

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


TEXTO = 'TRASPASO CUENTAS PROPIAS'


class FondeoTraspasoCuentasPropiasTest(TraspasosBase):
    """Formato real de BBVA→BBVA: el concepto es el mismo en las dos cuentas y solo el abono dice el origen."""

    def setUp(self):
        super().setUp()
        self.retiros = ConfiguracionContable.obtener_cuenta('RETIROS_DUENO')

    def _cargo(self, estado, importe='5000', dia=5):
        return self._mov(estado, f'{TEXTO} {NUM_COBRO}', cargo=importe, dia=dia)  # su propio número

    def _abono(self, estado, importe='5000', dia=5):
        return self._mov(estado, f'{TEXTO} {NUM_COBRO}', abono=importe, dia=dia)  # el origen

    def _contrapartida(self, mov):
        mov.refresh_from_db()
        if not mov.movimiento_contable_id:
            return None
        banco = mov.estado_cuenta.cuenta_bancaria.cuenta_contable
        return mov.movimiento_contable.poliza.movimientos.exclude(cuenta=banco).get().cuenta

    def _sin_retiros(self):
        self.assertFalse(self.retiros.movimientos.exists())
        self.assertEqual(saldo_traspasos_en_transito(), Decimal('0.00'))

    def test_las_dos_puntas_en_cualquier_orden_dejan_la_puente_en_cero(self):
        for primero in ('cargo', 'abono'):
            with self.subTest(primero=primero):
                MovimientoEstadoCuenta.objects.all().delete()
                EstadoCuentaBancario.objects.all().delete()
                Poliza.objects.all().delete()
                e_cobro, e_pago = self._estado(self.cobro), self._estado(self.pago)
                cargo, abono = self._cargo(e_cobro), self._abono(e_pago)
                for estado in ((e_cobro, e_pago) if primero == 'cargo' else (e_pago, e_cobro)):
                    emparejar_y_asentar(estado)
                self._sin_retiros()
                self.assertEqual(self._contrapartida(cargo), self.puente)
                self.assertEqual(self._contrapartida(abono), self.puente)

    def test_cargo_sin_pareja_con_ambos_estados_procesados_es_retiro_del_dueno(self):
        e_cobro, e_pago = self._estado(self.cobro), self._estado(self.pago)
        cargo = self._cargo(e_cobro)  # fue a una cuenta personal: la pagadora no recibió nada
        emparejar_y_asentar(e_pago)
        emparejar_y_asentar(e_cobro)
        self.assertEqual(self._contrapartida(cargo), self.retiros)

    def test_cargo_espera_sin_asiento_hasta_que_llegue_el_otro_estado(self):
        e_cobro = self._estado(self.cobro)
        cargo = self._cargo(e_cobro)
        emparejar_y_asentar(e_cobro)
        cargo.refresh_from_db()
        self.assertIsNone(cargo.movimiento_contable_id)
        self.assertFalse(Poliza.objects.filter(object_id=cargo.pk, origen='BANCO').exists())
        self._sin_retiros()

        e_pago = self._estado(self.pago)  # llega sin el abono: no fue un fondeo
        emparejar_y_asentar(e_pago)
        self.assertEqual(self._contrapartida(cargo), self.retiros)

    def test_cargo_del_ultimo_dia_con_abono_en_el_estado_siguiente_se_corrige_solo(self):
        e_cobro, e_pago = self._estado(self.cobro), self._estado(self.pago)
        cargo = self._cargo(e_cobro, dia=30)
        emparejar_y_asentar(e_pago)
        emparejar_y_asentar(e_cobro)  # el estado de noviembre de la pagadora no llega al 1 de diciembre: espera
        self.assertIsNone(self._contrapartida(cargo))

        e_dic = self._estado(self.pago, mes=12)
        MovimientoEstadoCuenta.objects.create(estado_cuenta=e_dic, fecha=date(2026, 12, 1),
                                              descripcion=f'{TEXTO} {NUM_COBRO}', abono=Decimal('5000'))
        emparejar_y_asentar(e_dic)
        self.assertEqual(self._contrapartida(cargo), self.puente)
        self._sin_retiros()

    def test_autocorreccion_de_un_cargo_ya_aplicado_como_retiro(self):
        e_cobro = self._estado(self.cobro)
        cargo = self._cargo(e_cobro, dia=30)
        crear_poliza_desde_movimiento(cargo, self.retiros, usuario_sistema(), aplicar=True)  # como quedaba antes
        poliza = self._poliza_de(cargo)
        folio = poliza.folio

        e_dic = self._estado(self.pago, mes=12)
        MovimientoEstadoCuenta.objects.create(estado_cuenta=e_dic, fecha=date(2026, 12, 1),
                                              descripcion=f'{TEXTO} {NUM_COBRO}', abono=Decimal('5000'))
        emparejar_y_asentar(e_dic)
        self.assertEqual(self._contrapartida(cargo), self.puente)
        poliza.refresh_from_db()
        self.assertEqual((poliza.folio, poliza.estado, poliza.movimientos.count()), (folio, 'APLICADA', 2))
        emparejar_y_asentar(e_dic)  # idempotente
        self.assertEqual(Poliza.objects.filter(object_id=cargo.pk, origen='BANCO').count(), 1)
        self._sin_retiros()

    def test_autocorreccion_respeta_un_periodo_cerrado(self):
        from django.contrib.auth import get_user_model
        e_cobro = self._estado(self.cobro)
        cargo = self._cargo(e_cobro, dia=30)
        crear_poliza_desde_movimiento(cargo, self.retiros, usuario_sistema(), aplicar=True)
        SaldoApertura.objects.create(
            cuenta_bancaria=self.cobro, fecha_corte=date(2026, 12, 31), saldo_certificado=Decimal('0.00'),
            certificado_por=get_user_model().objects.create_user('contador_ficticio', password='x'),  # noqa: S106
            aplicado=True)
        e_dic = self._estado(self.pago, mes=12)
        MovimientoEstadoCuenta.objects.create(estado_cuenta=e_dic, fecha=date(2026, 12, 1),
                                              descripcion=f'{TEXTO} {NUM_COBRO}', abono=Decimal('5000'))
        emparejar_y_asentar(e_dic)
        self.assertEqual(self._contrapartida(cargo), self.retiros)

    def test_dos_fondeos_iguales_el_mismo_dia_se_emparejan_uno_a_uno(self):
        e_cobro, e_pago = self._estado(self.cobro), self._estado(self.pago)
        c1, c2 = self._cargo(e_cobro, '2000'), self._cargo(e_cobro, '2000')
        a1 = self._abono(e_pago, '2000')  # solo llegó un abono: el otro cargo fue a una cuenta personal
        emparejar_y_asentar(e_pago)
        emparejar_y_asentar(e_cobro)
        self.assertEqual(self._contrapartida(a1), self.puente)
        self.assertEqual(sorted(str(self._contrapartida(c)) for c in (c1, c2)),
                         sorted([str(self.puente), str(self.retiros)]))
        self.assertEqual(self._contrapartida(c1), self.puente)  # el de menor id se queda con la pareja

        a2 = self._abono(e_pago, '2000')
        emparejar_y_asentar(e_pago)
        self.assertEqual(self._contrapartida(a2), self.puente)
        self.assertEqual(self._contrapartida(c2), self.puente)  # autocorregido al llegar su pareja
        self._sin_retiros()

    def test_reprocesar_un_estado_no_duplica_polizas(self):
        e_cobro, e_pago = self._estado(self.cobro), self._estado(self.pago)
        cargo, abono = self._cargo(e_cobro), self._abono(e_pago)
        for _ in range(2):
            emparejar_y_asentar(e_pago)
            emparejar_y_asentar(e_cobro)
        for mov in (cargo, abono):
            self.assertEqual(Poliza.objects.filter(object_id=mov.pk, origen='BANCO').count(), 1)
        self._sin_retiros()

    def test_con_una_sola_cuenta_activa_el_comportamiento_es_el_de_antes(self):
        self.pago.activa = False
        self.pago.save()
        e_cobro = self._estado(self.cobro)
        cargo = self._cargo(e_cobro)
        emparejar_y_asentar(e_cobro)
        self.assertEqual(self._contrapartida(cargo), self.retiros)
