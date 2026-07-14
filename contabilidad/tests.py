"""
Tests del módulo Contabilidad
=============================
Cubre signals de pago, reembolsos, reversión por cancelación, la
regularización contable (unidad de negocio/cuenta real en compras,
exclusión de nómina, saldos de apertura), y la carga de estados de
cuenta BBVA con su conciliación preliminar.
"""
import os
import unittest
from decimal import Decimal
from datetime import date, timedelta
from django.test import TestCase
from django.contrib.auth.models import User

from contabilidad.models import (
    CuentaContable, ConfiguracionContable, UnidadNegocio, CuentaBancaria,
    Poliza, MovimientoContable, SaldoApertura,
    EstadoCuentaBancario, MovimientoEstadoCuenta,
)
from contabilidad.services import aplicar_saldo_apertura
from contabilidad.services_estados_cuenta import (
    _emparejar_automaticamente, generar_conciliacion_preliminar,
)
from comercial.models import Cliente, Cotizacion, ItemCotizacion, Pago, Compra
from nomina.models import Empleado, ReciboNomina
from nomina.services import marcar_recibo_como_pagado


def setup_contabilidad_minima():
    """Crea catálogo mínimo y configuración para signals."""
    UnidadNegocio.objects.get_or_create(
        clave='QUINTA', defaults={'nombre': 'Quinta Ko\'ox Tanil - Eventos'}
    )
    cuentas = {
        'CAJA':                  ('101.01', 'Caja',               'ACTIVO',  'D'),
        'BANCO_PRINCIPAL':       ('102.01', 'Bancos',             'ACTIVO',  'D'),
        'ANTICIPO_CLIENTES':     ('206.01', 'Anticipo clientes',  'PASIVO',  'A'),
        'INGRESO_EVENTOS':       ('401.01', 'Ingreso eventos',    'INGRESO', 'A'),
        'IVA_TRASLADADO':        ('208.01', 'IVA trasladado',     'PASIVO',  'A'),
        'ISR_RETENIDO_CLIENTES': ('118.01', 'ISR ret. clientes',  'ACTIVO',  'D'),
    }
    for op, (codigo, nombre, tipo, naturaleza) in cuentas.items():
        cc, _ = CuentaContable.objects.get_or_create(
            codigo_sat=codigo,
            defaults={'nombre': nombre, 'tipo': tipo, 'naturaleza': naturaleza}
        )
        ConfiguracionContable.objects.get_or_create(
            operacion=op, defaults={'cuenta': cc, 'activa': True}
        )


def _crear_cotizacion(cliente, precio):
    cot = Cotizacion.objects.create(
        cliente=cliente,
        nombre_evento='Evento Test',
        fecha_evento=date.today() + timedelta(days=60),
        incluye_refrescos=False, incluye_cerveza=False,
        incluye_licor_nacional=False, incluye_licor_premium=False,
        incluye_cocteleria_basica=False, incluye_cocteleria_premium=False,
    )
    # precio = 11600 → subtotal=10000, iva=1600
    subtotal = (precio / Decimal('1.16')).quantize(Decimal('0.01'))
    iva = precio - subtotal
    Cotizacion.objects.filter(pk=cot.pk).update(
        precio_final=precio, subtotal=subtotal, iva=iva
    )
    cot.refresh_from_db()
    return cot


class PolizaPagoClienteTest(TestCase):

    def setUp(self):
        setup_contabilidad_minima()
        self.user = User.objects.create_user('u', password='x')
        self.cliente = Cliente.objects.create(nombre='Cliente', tipo_persona='FISICA')
        self.cot = _crear_cotizacion(self.cliente, Decimal('11600.00'))

    def test_pago_genera_poliza_ingreso_balanceada(self):
        Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('11600.00'),
            metodo='TRANSFERENCIA', usuario=self.user
        )
        polizas = Poliza.objects.filter(origen='PAGO_CLIENTE')
        self.assertEqual(polizas.count(), 1)
        p = polizas.first()
        debe = sum(m.debe for m in p.movimientos.all())
        haber = sum(m.haber for m in p.movimientos.all())
        self.assertEqual(debe, haber)
        self.assertEqual(debe, Decimal('11600.00'))


class ReembolsoClienteTest(TestCase):

    def setUp(self):
        setup_contabilidad_minima()
        self.user = User.objects.create_user('u', password='x')
        self.cliente = Cliente.objects.create(nombre='Cliente', tipo_persona='FISICA')
        self.cot = _crear_cotizacion(self.cliente, Decimal('11600.00'))
        self.pago = Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('11600.00'),
            metodo='TRANSFERENCIA', usuario=self.user
        )

    def test_reembolso_genera_poliza_egreso_inversa(self):
        Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('5000.00'),
            metodo='TRANSFERENCIA', tipo='REEMBOLSO', usuario=self.user
        )
        polizas_e = Poliza.objects.filter(tipo='E', origen='PAGO_CLIENTE')
        self.assertEqual(polizas_e.count(), 1)
        p = polizas_e.first()
        debe = sum(m.debe for m in p.movimientos.all())
        haber = sum(m.haber for m in p.movimientos.all())
        self.assertEqual(debe, haber)
        self.assertEqual(haber, Decimal('5000.00'))

    def test_reembolso_excedente_rechazado(self):
        from django.core.exceptions import ValidationError
        pago = Pago(
            cotizacion=self.cot, monto=Decimal('20000.00'),
            metodo='EFECTIVO', tipo='REEMBOLSO', usuario=self.user
        )
        with self.assertRaises(ValidationError):
            pago.clean()

    def test_total_pagado_neto_descuenta_reembolso(self):
        Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('3000.00'),
            metodo='EFECTIVO', tipo='REEMBOLSO', usuario=self.user
        )
        self.assertEqual(self.cot.total_pagado(), Decimal('8600.00'))


class ReversionCancelacionTest(TestCase):

    def setUp(self):
        setup_contabilidad_minima()
        self.user = User.objects.create_user('u', password='x')
        self.cliente = Cliente.objects.create(nombre='Cliente', tipo_persona='FISICA')
        self.cot = _crear_cotizacion(self.cliente, Decimal('11600.00'))
        ItemCotizacion.objects.create(
            cotizacion=self.cot, descripcion='X',
            cantidad=1, precio_unitario=Decimal('10000.00')
        )
        Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('5800.00'),
            metodo='TRANSFERENCIA', usuario=self.user
        )

    def test_cancelacion_crea_poliza_reversion(self):
        ok, _ = self.cot.cambiar_estado('CANCELADA', self.user, motivo='Cliente desistió')
        self.assertTrue(ok)
        reversiones = Poliza.objects.filter(origen='AJUSTE')
        self.assertEqual(reversiones.count(), 1)
        rev = reversiones.first()
        debe = sum(m.debe for m in rev.movimientos.all())
        haber = sum(m.haber for m in rev.movimientos.all())
        self.assertEqual(debe, haber)
        self.assertEqual(debe, Decimal('5800.00'))

    def test_cancelacion_idempotente(self):
        from contabilidad.signals import crear_polizas_reversion_cancelacion
        self.cot.cambiar_estado('CANCELADA', self.user, motivo='X')
        crear_polizas_reversion_cancelacion(self.cot, self.user, motivo='X')
        self.assertEqual(Poliza.objects.filter(origen='AJUSTE').count(), 1)


# ==========================================
# REGULARIZACIÓN CONTABLE
# ==========================================

class ClaveUnidadNegocioTest(TestCase):
    """Regresión del bug: 'EVENTOS' no debe usarse en ningún lado; 'QUINTA' sí debe existir."""

    def test_quinta_existe_y_eventos_no(self):
        self.assertTrue(UnidadNegocio.objects.filter(clave='QUINTA').exists())
        self.assertFalse(UnidadNegocio.objects.filter(clave='EVENTOS').exists())


class CompraSinDatosCompletosTest(TestCase):
    """Una Compra sin cuenta_pago y/o unidad_negocio debe generar póliza en BORRADOR."""

    def setUp(self):
        # BANCO_PRINCIPAL, GASTOS_GENERALES e IVA_ACREDITABLE ya vienen precargados
        # por las migraciones de datos (0002/0005); solo falta la CuentaBancaria real.
        cuenta_banco = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.cuenta_bancaria = CuentaBancaria.objects.create(
            nombre='BBVA Principal', banco='BBVA',
            clabe='012345678901234567', cuenta_contable=cuenta_banco,
        )

    def test_poliza_queda_en_borrador_sin_datos(self):
        compra = Compra.objects.create(proveedor="Proveedor X", subtotal=Decimal('1000.00'),
                                        iva=Decimal('160.00'), total=Decimal('1160.00'))
        poliza = Poliza.objects.filter(origen='COMPRA', object_id=compra.pk).first()
        self.assertIsNotNone(poliza)
        self.assertEqual(poliza.estado, 'BORRADOR')

    def test_poliza_aplicada_con_datos_completos(self):
        unidad_airbnb = UnidadNegocio.objects.get(clave='AIRBNB')
        compra = Compra.objects.create(
            proveedor="Proveedor Airbnb", subtotal=Decimal('500.00'),
            iva=Decimal('80.00'), total=Decimal('580.00'),
            cuenta_pago=self.cuenta_bancaria, unidad_negocio=unidad_airbnb,
        )
        poliza = Poliza.objects.filter(origen='COMPRA', object_id=compra.pk).first()
        self.assertEqual(poliza.estado, 'APLICADA')
        self.assertEqual(poliza.unidad_negocio.clave, 'AIRBNB')  # no debe caer en QUINTA por default


class NominaNuncaGeneraPolizaTest(TestCase):
    """Nómina está excluida de contabilidad: ni calculada ni pagada debe generar póliza."""

    def test_sin_poliza_en_creacion(self):
        empleado = Empleado.objects.create(nombre="Arcadio Pech May")
        recibo = ReciboNomina.objects.create(
            empleado=empleado, periodo="Semana 1", horas_trabajadas=Decimal('40'),
            tarifa_aplicada=Decimal('50'), total_pagado=Decimal('2000.00'),
        )
        self.assertEqual(recibo.estado, 'CALCULADO')
        self.assertFalse(Poliza.objects.filter(origen='NOMINA', object_id=recibo.pk).exists())

    def test_sin_poliza_al_marcar_como_pagado(self):
        empleado = Empleado.objects.create(nombre="Kevin Abisai Canche Montuy")
        recibo = ReciboNomina.objects.create(
            empleado=empleado, periodo="Semana 1", horas_trabajadas=Decimal('40'),
            tarifa_aplicada=Decimal('50'), total_pagado=Decimal('2000.00'),
        )
        marcar_recibo_como_pagado(recibo, fecha_pago=date.today())
        recibo.refresh_from_db()
        self.assertEqual(recibo.estado, 'PAGADO')
        self.assertEqual(Poliza.objects.count(), 0)


class SaldoAperturaTest(TestCase):
    """La póliza de apertura debe cuadrar exactamente contra el saldo certificado."""

    def setUp(self):
        self.user = User.objects.create_user('contador', password='x')
        cuenta_banco_contable = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.cuenta_bancaria = CuentaBancaria.objects.create(
            nombre='BBVA Principal', banco='BBVA',
            clabe='012345678901234568', cuenta_contable=cuenta_banco_contable,
        )
        cuenta_ajuste = CuentaContable.objects.get(codigo_sat='304.01')
        ConfiguracionContable.objects.get_or_create(
            operacion='AJUSTE_APERTURA', defaults={'cuenta': cuenta_ajuste, 'activa': True}
        )

    def test_diferencia_cero_no_genera_movimientos(self):
        saldo = SaldoApertura.objects.create(
            cuenta_bancaria=self.cuenta_bancaria, fecha_corte=date(2026, 7, 1),
            saldo_certificado=Decimal('0.00'), certificado_por=self.user,
        )
        poliza = aplicar_saldo_apertura(saldo, usuario=self.user)
        self.assertEqual(poliza.movimientos.count(), 0)
        saldo.refresh_from_db()
        self.assertTrue(saldo.aplicado)
        self.assertEqual(saldo.poliza, poliza)

    def test_diferencia_genera_ajuste_balanceado(self):
        saldo = SaldoApertura.objects.create(
            cuenta_bancaria=self.cuenta_bancaria, fecha_corte=date(2026, 7, 1),
            saldo_certificado=Decimal('15000.00'), certificado_por=self.user,
        )
        poliza = aplicar_saldo_apertura(saldo, usuario=self.user)
        debe = sum(m.debe for m in poliza.movimientos.all())
        haber = sum(m.haber for m in poliza.movimientos.all())
        self.assertEqual(debe, haber)
        self.assertEqual(debe, Decimal('15000.00'))


# ==========================================
# ESTADOS DE CUENTA BANCARIOS Y CONCILIACIÓN
# ==========================================

class EmparejamientoAutomaticoTest(TestCase):
    """El emparejamiento debe respetar monto exacto, tolerancia de fecha, y no duplicar matches."""

    def setUp(self):
        self.user = User.objects.create_user('contador_ec', password='x')
        self.unidad = UnidadNegocio.objects.get(clave='QUINTA')
        self.cuenta_contable_banco = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.cuenta_bancaria = CuentaBancaria.objects.create(
            nombre='BBVA Principal (test emparejamiento)', banco='BBVA',
            clabe='012345678901234570', cuenta_contable=self.cuenta_contable_banco,
        )
        self.estado_cuenta = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cuenta_bancaria, banco='BBVA',
            periodo_mes=7, periodo_anio=2026, formato='PDF', estado='PROCESADO',
        )

    def _crear_movimiento_contable(self, fecha, debe=Decimal('0.00'), haber=Decimal('0.00')):
        tipo = 'I' if debe else 'E'
        poliza = Poliza.objects.create(
            tipo=tipo, folio=Poliza.siguiente_folio(tipo, fecha),
            fecha=fecha, concepto='Movimiento de prueba',
            unidad_negocio=self.unidad, estado='APLICADA',
            origen='MANUAL', created_by=self.user,
        )
        return MovimientoContable.objects.create(
            poliza=poliza, cuenta=self.cuenta_contable_banco,
            debe=debe, haber=haber, concepto='Prueba',
        )

    def test_emparejamiento_exacto_dentro_de_tolerancia(self):
        mov_contable = self._crear_movimiento_contable(date(2026, 7, 1), debe=Decimal('1000.00'))
        mov_banco = MovimientoEstadoCuenta.objects.create(
            estado_cuenta=self.estado_cuenta, fecha=date(2026, 7, 3),
            descripcion='Depósito', abono=Decimal('1000.00'),
        )
        _emparejar_automaticamente(self.estado_cuenta)
        mov_banco.refresh_from_db()
        self.assertEqual(mov_banco.movimiento_contable, mov_contable)
        self.assertTrue(mov_banco.match_automatico)
        self.assertFalse(mov_banco.confirmado)

    def test_no_empareja_dos_veces_el_mismo_movimiento_contable(self):
        mov_contable = self._crear_movimiento_contable(date(2026, 7, 1), debe=Decimal('500.00'))
        mov_banco_1 = MovimientoEstadoCuenta.objects.create(
            estado_cuenta=self.estado_cuenta, fecha=date(2026, 7, 2),
            descripcion='Depósito 1', abono=Decimal('500.00'),
        )
        mov_banco_2 = MovimientoEstadoCuenta.objects.create(
            estado_cuenta=self.estado_cuenta, fecha=date(2026, 7, 2),
            descripcion='Depósito 2', abono=Decimal('500.00'),
        )
        _emparejar_automaticamente(self.estado_cuenta)
        mov_banco_1.refresh_from_db()
        mov_banco_2.refresh_from_db()
        emparejados = [m for m in (mov_banco_1, mov_banco_2) if m.movimiento_contable_id]
        self.assertEqual(len(emparejados), 1)
        self.assertEqual(emparejados[0].movimiento_contable, mov_contable)

    def test_fuera_de_tolerancia_no_empareja(self):
        self._crear_movimiento_contable(date(2026, 7, 1), debe=Decimal('750.00'))
        mov_banco = MovimientoEstadoCuenta.objects.create(
            estado_cuenta=self.estado_cuenta, fecha=date(2026, 7, 20),
            descripcion='Depósito tardío', abono=Decimal('750.00'),
        )
        _emparejar_automaticamente(self.estado_cuenta)
        mov_banco.refresh_from_db()
        self.assertIsNone(mov_banco.movimiento_contable)
        self.assertFalse(mov_banco.match_automatico)


class ConciliacionPreliminarTest(TestCase):
    """generar_conciliacion_preliminar usa saldo_a_fecha, no saldo_actual corrido a hoy."""

    def setUp(self):
        self.user = User.objects.create_user('contador_conc', password='x')
        self.unidad = UnidadNegocio.objects.get(clave='QUINTA')
        self.cuenta_contable_banco = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.cuenta_bancaria = CuentaBancaria.objects.create(
            nombre='BBVA Principal (test conciliación)', banco='BBVA',
            clabe='012345678901234571', cuenta_contable=self.cuenta_contable_banco,
        )

    def _poliza(self, fecha, debe=Decimal('0.00'), haber=Decimal('0.00')):
        tipo = 'I' if debe else 'E'
        poliza = Poliza.objects.create(
            tipo=tipo, folio=Poliza.siguiente_folio(tipo, fecha),
            fecha=fecha, concepto='Movimiento', unidad_negocio=self.unidad,
            estado='APLICADA', origen='MANUAL', created_by=self.user,
        )
        MovimientoContable.objects.create(
            poliza=poliza, cuenta=self.cuenta_contable_banco, debe=debe, haber=haber, concepto='x',
        )

    def test_usa_saldo_a_fecha_no_saldo_actual(self):
        self._poliza(date(2026, 6, 15), debe=Decimal('2000.00'))   # antes del corte: sí cuenta
        self._poliza(date(2026, 8, 1), debe=Decimal('99999.00'))   # después del corte: NO debe contar

        estado_cuenta = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cuenta_bancaria, banco='BBVA',
            periodo_mes=7, periodo_anio=2026, formato='PDF', estado='PROCESADO',
            fecha_corte_real=date(2026, 7, 1), saldo_final_estado=Decimal('2000.00'),
        )
        conciliacion = generar_conciliacion_preliminar(estado_cuenta, usuario=self.user)
        self.assertEqual(conciliacion.saldo_segun_libros, Decimal('2000.00'))


class ParserBBVATest(TestCase):
    """
    Valida el parser contra los dos PDFs reales de muestra (Libretón Básico y
    Maestra PYME). Coloca los archivos en contabilidad/tests_fixtures/ antes
    de correr — ver nombres exactos abajo. Sin ellos, estos tests se saltan
    (no fallan) para no romper la suite mientras no estén disponibles.

    Las cifras esperadas son EXACTAMENTE las que imprime el propio estado de
    cuenta en su sección "Total de Movimientos" — si algún día BBVA cambia su
    formato y estos tests empiezan a fallar, es la señal de que el parser
    necesita recalibrarse contra el nuevo formato, no de que el test esté mal.
    """
    FIXTURE_LIBRETON = os.path.join(os.path.dirname(__file__), 'tests_fixtures', 'estado_cuenta_bbva_libreton_ejemplo.pdf')
    FIXTURE_MAESTRA_PYME = os.path.join(os.path.dirname(__file__), 'tests_fixtures', 'estado_cuenta_bbva_maestra_pyme_ejemplo.pdf')

    @unittest.skipUnless(os.path.exists(FIXTURE_LIBRETON), "Falta el fixture real estado_cuenta_bbva_libreton_ejemplo.pdf")
    def test_parser_libreton_basico_totales_exactos(self):
        from contabilidad.services_estados_cuenta import _parsear_pdf_bbva
        movs, saldo_inicial, saldo_final, numero_cuenta, fecha_corte_real = _parsear_pdf_bbva(self.FIXTURE_LIBRETON)

        self.assertEqual(numero_cuenta, '1551774893')
        self.assertEqual(saldo_inicial, Decimal('3546.19'))
        self.assertEqual(saldo_final, Decimal('15658.90'))
        self.assertEqual(fecha_corte_real, date(2026, 3, 14))  # corte a mitad de mes, no fin de mes
        self.assertEqual(len(movs), 53)

        total_cargo = sum((m['cargo'] for m in movs), Decimal('0.00'))
        total_abono = sum((m['abono'] for m in movs), Decimal('0.00'))
        n_cargo = sum(1 for m in movs if m['cargo'] > 0)
        n_abono = sum(1 for m in movs if m['abono'] > 0)

        self.assertEqual(total_cargo, Decimal('20366.38'))
        self.assertEqual(n_cargo, 44)
        self.assertEqual(total_abono, Decimal('32479.09'))
        self.assertEqual(n_abono, 9)

    @unittest.skipUnless(os.path.exists(FIXTURE_MAESTRA_PYME), "Falta el fixture real estado_cuenta_bbva_maestra_pyme_ejemplo.pdf")
    def test_parser_maestra_pyme_totales_exactos(self):
        from contabilidad.services_estados_cuenta import _parsear_pdf_bbva
        movs, saldo_inicial, saldo_final, numero_cuenta, fecha_corte_real = _parsear_pdf_bbva(self.FIXTURE_MAESTRA_PYME)

        self.assertEqual(numero_cuenta, '0489570314')
        self.assertEqual(saldo_inicial, Decimal('6624.34'))
        self.assertEqual(saldo_final, Decimal('0.21'))
        self.assertEqual(fecha_corte_real, date(2026, 4, 30))  # corte fin de mes
        self.assertEqual(len(movs), 39)

        total_cargo = sum((m['cargo'] for m in movs), Decimal('0.00'))
        total_abono = sum((m['abono'] for m in movs), Decimal('0.00'))
        n_cargo = sum(1 for m in movs if m['cargo'] > 0)
        n_abono = sum(1 for m in movs if m['abono'] > 0)

        self.assertEqual(total_cargo, Decimal('18071.02'))
        self.assertEqual(n_cargo, 34)
        self.assertEqual(total_abono, Decimal('11446.89'))
        self.assertEqual(n_abono, 5)

    @unittest.skipUnless(os.path.exists(FIXTURE_LIBRETON), "Falta el fixture real estado_cuenta_bbva_libreton_ejemplo.pdf")
    def test_numero_cuenta_no_coincide_rechaza_la_carga(self):
        """
        Regresión directa del requisito de Elián: nunca debe ser posible que un
        estado de cuenta de una persona se procese contra la CuentaBancaria de otra.
        """
        from contabilidad.services_estados_cuenta import procesar_estado_cuenta
        from django.core.files import File

        cuenta_equivocada = CuentaBancaria.objects.create(
            nombre="Cuenta equivocada de prueba", banco="BBVA",
            numero_cuenta="0000000000", clabe="000000000000000000",
        )
        with open(self.FIXTURE_LIBRETON, 'rb') as f:
            estado_cuenta = EstadoCuentaBancario.objects.create(
                cuenta_bancaria=cuenta_equivocada, banco='BBVA',
                periodo_mes=2, periodo_anio=2026, formato='PDF',
                archivo=File(f, name='estado_cuenta_bbva_libreton_ejemplo.pdf'),
            )
        with self.assertRaises(ValueError):
            procesar_estado_cuenta(estado_cuenta)
        estado_cuenta.refresh_from_db()
        self.assertEqual(estado_cuenta.estado, 'ERROR')
