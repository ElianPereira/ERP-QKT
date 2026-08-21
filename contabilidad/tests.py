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
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib import admin
from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from comercial.models import Cliente, Compra, Cotizacion, ItemCotizacion, Pago
from contabilidad.admin import MovimientoContableAdmin
from contabilidad.models import (
    ConciliacionBancaria,
    ConfiguracionContable,
    CuentaBancaria,
    CuentaContable,
    EstadoCuentaBancario,
    MovimientoContable,
    MovimientoEstadoCuenta,
    Poliza,
    SaldoApertura,
    UnidadNegocio,
)
from contabilidad.services import (
    aplicar_saldo_apertura,
    aprobar_regularizacion_arrastre,
    cerrar_historico_contable,
    proponer_regularizacion_arrastre,
)
from contabilidad.services_estados_cuenta import (
    _emparejar_automaticamente,
    generar_conciliacion_preliminar,
)
from core_erp.test_utils import login_superuser_con_totp
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
        'OTROS_INGRESOS_CLIENTE': ('402.02', 'Otros ingresos',    'INGRESO', 'A'),
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

    def test_pago_extra_va_a_otros_ingresos_sin_desglose_iva(self):
        """Un Pago concepto=EXTRA se registra completo contra 'Otros ingresos',
        sin desglosar IVA/anticipo — no es parte del precio de la venta."""
        Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('500.00'),
            metodo='EFECTIVO', usuario=self.user, concepto='EXTRA',
        )
        polizas = Poliza.objects.filter(origen='PAGO_CLIENTE')
        self.assertEqual(polizas.count(), 1)
        p = polizas.first()
        cuenta_otros_ingresos = CuentaContable.objects.get(codigo_sat='402.02')
        movimientos = list(p.movimientos.all())
        self.assertEqual(len(movimientos), 2)
        haber_otros_ingresos = sum(
            m.haber for m in movimientos if m.cuenta_id == cuenta_otros_ingresos.pk
        )
        self.assertEqual(haber_otros_ingresos, Decimal('500.00'))


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


class RenombrarUnidadNegocioEventosAQuintaMigrationTest(TestCase):
    """Regresión: producción arrastraba una UnidadNegocio con clave='EVENTOS'
    (creada a mano, con datos fiscales reales) porque la migración 0002 ya
    se había aplicado antes de que el código usara clave='QUINTA' — nunca se
    creó ni se fusionó una fila 'QUINTA' ahí. La migración 0012 debe
    renombrar esa fila (preservando su PK, y con ella, todo lo que ya la
    referencia) en vez de dejar los gastos/pólizas huérfanos de un
    unidad_negocio__clave='QUINTA' que nunca existió."""

    def _cargar_funcion_migracion(self):
        import importlib
        modulo = importlib.import_module(
            'contabilidad.migrations.0012_renombrar_unidad_negocio_eventos_a_quinta'
        )
        return modulo.renombrar_eventos_a_quinta

    class _AppsFalso:
        """Sustituto mínimo del `apps` histórico que recibe un RunPython:
        el modelo real sirve porque la migración solo toca el campo `clave`,
        que no ha cambiado de forma entre el estado histórico y el actual."""
        def get_model(self, app_label, nombre_modelo):
            return UnidadNegocio

    def test_renombra_eventos_a_quinta_preservando_pk(self):
        UnidadNegocio.objects.filter(clave='QUINTA').delete()
        eventos = UnidadNegocio.objects.create(
            clave='EVENTOS', nombre="Quinta Ko'ox Tanil - Eventos", regimen_fiscal='626',
        )
        pk_original = eventos.pk

        renombrar = self._cargar_funcion_migracion()
        renombrar(self._AppsFalso(), None)

        eventos.refresh_from_db()
        self.assertEqual(eventos.pk, pk_original)  # mismo registro, FKs intactas
        self.assertEqual(eventos.clave, 'QUINTA')
        self.assertFalse(UnidadNegocio.objects.filter(clave='EVENTOS').exists())

    def test_no_hace_nada_si_ya_existe_quinta(self):
        """Instalación fresca (como los tests locales): 0002 ya sembró
        'QUINTA' directamente, nunca existió 'EVENTOS' — la migración debe
        ser un no-op, no crear ni tocar nada."""
        self.assertFalse(UnidadNegocio.objects.filter(clave='EVENTOS').exists())
        quinta_antes = UnidadNegocio.objects.get(clave='QUINTA')

        renombrar = self._cargar_funcion_migracion()
        renombrar(self._AppsFalso(), None)

        quinta_despues = UnidadNegocio.objects.get(clave='QUINTA')
        self.assertEqual(quinta_antes.pk, quinta_despues.pk)


class PolizaSinGuardarTest(TestCase):
    """Regresión: crear una Póliza nueva en estado APLICADA desde el admin
    (formulario + inline de Movimientos en un solo POST) no debe tronar.

    Antes de la póliza guardarse, Poliza.clean() revisaba esta_cuadrada,
    que agrega sobre self.movimientos — un related manager que no se puede
    usar sin pk (ValueError, no ValidationError), tronando el admin con un
    error 500 en vez de una validación normal."""

    def test_full_clean_no_truena_sin_pk(self):
        unidad = UnidadNegocio.objects.get(clave='QUINTA')
        user = User.objects.create_user('u3', password='x')
        poliza = Poliza(
            tipo='I', folio=1, fecha=date.today(), concepto='Test',
            unidad_negocio=unidad, estado='APLICADA', created_by=user,
        )
        poliza.full_clean(exclude=['folio'])  # no debe lanzar ValueError

    def test_total_debe_y_haber_cero_sin_pk(self):
        unidad = UnidadNegocio.objects.get(clave='QUINTA')
        poliza = Poliza(tipo='I', fecha=date.today(), concepto='Test', unidad_negocio=unidad)
        self.assertEqual(poliza.total_debe, Decimal('0.00'))
        self.assertEqual(poliza.total_haber, Decimal('0.00'))


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
        compra = Compra.objects.create(proveedor_nombre="Proveedor X", subtotal=Decimal('1000.00'),
                                        iva=Decimal('160.00'), total=Decimal('1160.00'))
        poliza = Poliza.objects.filter(origen='COMPRA', object_id=compra.pk).first()
        self.assertIsNotNone(poliza)
        self.assertEqual(poliza.estado, 'BORRADOR')

    def test_poliza_aplicada_con_datos_completos(self):
        unidad_airbnb = UnidadNegocio.objects.get(clave='AIRBNB')
        compra = Compra.objects.create(
            proveedor_nombre="Proveedor Airbnb", subtotal=Decimal('500.00'),
            iva=Decimal('80.00'), total=Decimal('580.00'),
            cuenta_pago=self.cuenta_bancaria, unidad_negocio=unidad_airbnb,
        )
        poliza = Poliza.objects.filter(origen='COMPRA', object_id=compra.pk).first()
        self.assertEqual(poliza.estado, 'APLICADA')
        self.assertEqual(poliza.unidad_negocio.clave, 'AIRBNB')  # no debe caer en QUINTA por default


class CompletarPolizaCompraTest(TestCase):
    """
    Una póliza de Compra en BORRADOR por falta de cuenta_pago/unidad_negocio
    debe poder completarse después de corregir la Compra, sin duplicar el
    movimiento de banco ni requerir recapturar el gasto a mano.
    """

    def setUp(self):
        from contabilidad.services import completar_poliza_compra
        self.completar_poliza_compra = completar_poliza_compra

        cuenta_banco = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.cuenta_bancaria = CuentaBancaria.objects.create(
            nombre='BBVA Principal', banco='BBVA',
            clabe='012345678901234580', cuenta_contable=cuenta_banco,
        )
        self.unidad_airbnb = UnidadNegocio.objects.get(clave='AIRBNB')

    def test_completa_poliza_y_queda_balanceada(self):
        compra = Compra.objects.create(
            proveedor_nombre="AUTOZONE DE MEXICO", subtotal=Decimal('139.00'), total=Decimal('139.00'),
        )
        poliza = Poliza.objects.get(origen='COMPRA', object_id=compra.pk)
        self.assertEqual(poliza.estado, 'BORRADOR')
        self.assertFalse(poliza.esta_cuadrada)

        # El usuario corrige la Compra con los datos que faltaban
        compra.cuenta_pago = self.cuenta_bancaria
        compra.unidad_negocio = self.unidad_airbnb
        compra.save()

        self.completar_poliza_compra(poliza)
        poliza.refresh_from_db()

        self.assertTrue(poliza.esta_cuadrada)
        self.assertEqual(poliza.total_debe, Decimal('139.00'))
        self.assertEqual(poliza.total_haber, Decimal('139.00'))
        self.assertEqual(poliza.unidad_negocio.clave, 'AIRBNB')
        self.assertEqual(poliza.estado, 'BORRADOR')  # completar no aplica, solo balancea

    def test_no_duplica_movimiento_si_se_corre_dos_veces(self):
        compra = Compra.objects.create(
            proveedor_nombre="AUTOZONE DE MEXICO", subtotal=Decimal('139.00'), total=Decimal('139.00'),
        )
        poliza = Poliza.objects.get(origen='COMPRA', object_id=compra.pk)
        compra.cuenta_pago = self.cuenta_bancaria
        compra.save()

        self.completar_poliza_compra(poliza)
        poliza.refresh_from_db()
        with self.assertRaises(ValueError):
            self.completar_poliza_compra(poliza)  # ya está cuadrada

    def test_rechaza_si_la_compra_sigue_sin_cuenta_pago(self):
        compra = Compra.objects.create(
            proveedor_nombre="AUTOZONE DE MEXICO", subtotal=Decimal('139.00'), total=Decimal('139.00'),
        )
        poliza = Poliza.objects.get(origen='COMPRA', object_id=compra.pk)
        with self.assertRaises(ValueError):
            self.completar_poliza_compra(poliza)

    def test_rechaza_poliza_aplicada(self):
        compra = Compra.objects.create(
            proveedor_nombre="Proveedor Y", subtotal=Decimal('500.00'), total=Decimal('500.00'),
            cuenta_pago=self.cuenta_bancaria, unidad_negocio=self.unidad_airbnb,
        )
        poliza = Poliza.objects.get(origen='COMPRA', object_id=compra.pk)
        self.assertEqual(poliza.estado, 'APLICADA')
        with self.assertRaises(ValueError):
            self.completar_poliza_compra(poliza)


class CompraSinCFDINoDeducibleTest(TestCase):
    """Un gasto sin factura timbrada (uuid) se registra igual, pero como no
    deducible: no se acredita IVA y el total completo va al gasto."""

    def setUp(self):
        cuenta_banco = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.cuenta_bancaria = CuentaBancaria.objects.create(
            nombre='BBVA Principal', banco='BBVA',
            clabe='012345678901234569', cuenta_contable=cuenta_banco,
        )
        self.unidad_quinta = UnidadNegocio.objects.get(clave='QUINTA')

    def test_compra_sin_uuid_se_marca_no_deducible_aunque_se_capture_true(self):
        compra = Compra.objects.create(
            proveedor_nombre="Proveedor Extranjero", subtotal=Decimal('1000.00'),
            iva=Decimal('160.00'), total=Decimal('1160.00'),
            cuenta_pago=self.cuenta_bancaria, unidad_negocio=self.unidad_quinta,
            es_deducible=True,  # intento de captura manual, debe forzarse a False
        )
        compra.refresh_from_db()
        self.assertFalse(compra.es_deducible)

    def test_poliza_sin_cfdi_carga_total_completo_sin_iva_acreditable(self):
        compra = Compra.objects.create(
            proveedor_nombre="Proveedor Extranjero", subtotal=Decimal('1000.00'),
            iva=Decimal('160.00'), total=Decimal('1160.00'),
            cuenta_pago=self.cuenta_bancaria, unidad_negocio=self.unidad_quinta,
        )
        poliza = Poliza.objects.filter(origen='COMPRA', object_id=compra.pk).first()
        self.assertEqual(poliza.estado, 'APLICADA')
        self.assertIn('SIN CFDI - NO DEDUCIBLE', poliza.concepto)

        cuenta_iva = CuentaContable.objects.get(codigo_sat='108.01')  # IVA_ACREDITABLE
        self.assertFalse(poliza.movimientos.filter(cuenta=cuenta_iva).exists())

        movimiento_gasto = poliza.movimientos.exclude(cuenta=cuenta_iva).exclude(cuenta=self.cuenta_bancaria.cuenta_contable).first()
        self.assertEqual(movimiento_gasto.debe, Decimal('1160.00'))  # total completo, no solo subtotal

    def test_compra_con_uuid_mantiene_deducible_y_acredita_iva(self):
        compra = Compra.objects.create(
            proveedor_nombre="Proveedor Nacional", subtotal=Decimal('1000.00'),
            iva=Decimal('160.00'), total=Decimal('1160.00'),
            cuenta_pago=self.cuenta_bancaria, unidad_negocio=self.unidad_quinta,
            uuid='11111111-1111-1111-1111-111111111111',
        )
        compra.refresh_from_db()
        self.assertTrue(compra.es_deducible)

        poliza = Poliza.objects.filter(origen='COMPRA', object_id=compra.pk).first()
        self.assertNotIn('SIN CFDI', poliza.concepto)

        cuenta_iva = CuentaContable.objects.get(codigo_sat='108.01')
        mov_iva = poliza.movimientos.filter(cuenta=cuenta_iva).first()
        self.assertIsNotNone(mov_iva)
        self.assertEqual(mov_iva.debe, Decimal('160.00'))

        movimiento_gasto = poliza.movimientos.exclude(cuenta=cuenta_iva).exclude(cuenta=self.cuenta_bancaria.cuenta_contable).first()
        self.assertEqual(movimiento_gasto.debe, Decimal('1000.00'))  # solo subtotal, IVA aparte


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


class GenerarCompraRetroactivaTest(TestCase):
    """Backfill: una póliza de egreso capturada a mano (sin Compra detrás,
    ej. 'FACEBOOK ADS' sin factura) debe poder generar retroactivamente su
    Compra correspondiente, sin duplicar el asiento contable ya existente."""

    def setUp(self):
        from contabilidad.services import generar_compra_retroactiva
        self.generar_compra_retroactiva = generar_compra_retroactiva

        self.user = User.objects.create_user('contador2', password='x')
        self.unidad_quinta = UnidadNegocio.objects.get(clave='QUINTA')
        self.cuenta_gasto = CuentaContable.objects.get(codigo_sat='601.02.05')
        cuenta_banco = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.cuenta_bancaria = CuentaBancaria.objects.create(
            nombre='BBVA Principal', banco='BBVA',
            clabe='012345678901234570', cuenta_contable=cuenta_banco,
        )

    def _crear_poliza_manual(self, concepto, monto, tipo='E', unidad=None):
        poliza = Poliza.objects.create(
            tipo=tipo, folio=Poliza.siguiente_folio(tipo, date.today()),
            fecha=date.today(), concepto=concepto,
            unidad_negocio=unidad or self.unidad_quinta,
            estado='APLICADA', origen='MANUAL', created_by=self.user,
        )
        MovimientoContable.objects.create(
            poliza=poliza, cuenta=self.cuenta_gasto,
            debe=monto, haber=Decimal('0.00'), concepto=concepto,
        )
        MovimientoContable.objects.create(
            poliza=poliza, cuenta=self.cuenta_bancaria.cuenta_contable,
            debe=Decimal('0.00'), haber=monto, concepto=concepto,
        )
        return poliza

    def test_genera_compra_no_deducible_y_vincula_la_poliza_existente(self):
        poliza = self._crear_poliza_manual('FACEBOOK ADS', Decimal('387.20'))

        compra = self.generar_compra_retroactiva(poliza)

        self.assertEqual(compra.proveedor_nombre, 'FACEBOOK ADS')
        self.assertEqual(compra.proveedor.nombre, 'FACEBOOK ADS')  # vinculado al catálogo
        self.assertEqual(compra.total, Decimal('387.20'))
        self.assertFalse(compra.es_deducible)
        self.assertEqual(compra.unidad_negocio, self.unidad_quinta)

        poliza.refresh_from_db()
        self.assertEqual(poliza.origen, 'COMPRA')
        self.assertEqual(poliza.object_id, compra.pk)

    def test_no_duplica_poliza_al_generar_la_compra(self):
        """El punto entero del backfill: la Compra creada NO debe disparar
        una segunda póliza — solo debe existir la póliza manual original."""
        poliza = self._crear_poliza_manual('SUSCRIPCIÓN DE RAILWAY', Decimal('153.15'))
        polizas_antes = Poliza.objects.count()

        self.generar_compra_retroactiva(poliza)

        self.assertEqual(Poliza.objects.count(), polizas_antes)

    def test_poliza_ya_vinculada_no_es_elegible(self):
        poliza = self._crear_poliza_manual('YA VINCULADA', Decimal('100.00'))
        self.generar_compra_retroactiva(poliza)  # la vincula la primera vez

        with self.assertRaises(ValueError):
            self.generar_compra_retroactiva(poliza)

    def test_poliza_de_ingreso_no_es_elegible(self):
        poliza = self._crear_poliza_manual('INGRESO MANUAL', Decimal('100.00'), tipo='I')
        with self.assertRaises(ValueError):
            self.generar_compra_retroactiva(poliza)


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
            fecha_corte_real=date(2026, 7, 1),
            saldo_inicial_estado=Decimal('2000.00'), saldo_final_estado=Decimal('2000.00'),
        )
        conciliacion = generar_conciliacion_preliminar(estado_cuenta, usuario=self.user)
        self.assertEqual(conciliacion.saldo_segun_libros, Decimal('2000.00'))

    def test_sin_saldo_inicial_del_pdf_no_concilia(self):
        """Sin el ancla del banco al inicio del periodo no se puede separar el
        arrastre, y meterlo todo en `diferencia` es justo lo que se corrigió."""
        estado_cuenta = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cuenta_bancaria, banco='BBVA',
            periodo_mes=7, periodo_anio=2026, formato='PDF', estado='PROCESADO',
            fecha_corte_real=date(2026, 7, 31), saldo_final_estado=Decimal('0.00'),
        )
        with self.assertRaises(ValueError):
            generar_conciliacion_preliminar(estado_cuenta, usuario=self.user)


class ConciliacionDelPeriodoTest(TestCase):
    """
    La conciliación es del periodo del estado de cuenta: lo que ya venía
    descuadrado antes del primer movimiento se aísla en `diferencia_arrastrada`
    y no contamina `diferencia`.
    """

    def setUp(self):
        self.user = User.objects.create_user('contador_periodo', password='x')
        self.unidad = UnidadNegocio.objects.get(clave='QUINTA')
        self.cuenta_contable_banco = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.cuenta_bancaria = CuentaBancaria.objects.create(
            nombre='BBVA Periodo', banco='BBVA',
            clabe='012345678901234572', cuenta_contable=self.cuenta_contable_banco,
        )

    def _poliza(self, fecha, debe=Decimal('0.00'), haber=Decimal('0.00'), concepto='Movimiento'):
        tipo = 'I' if debe else 'E'
        poliza = Poliza.objects.create(
            tipo=tipo, folio=Poliza.siguiente_folio(tipo, fecha),
            fecha=fecha, concepto=concepto, unidad_negocio=self.unidad,
            estado='APLICADA', origen='MANUAL', created_by=self.user,
        )
        return MovimientoContable.objects.create(
            poliza=poliza, cuenta=self.cuenta_contable_banco,
            debe=debe, haber=haber, concepto=concepto,
        )

    def _estado_cuenta(self, saldo_inicial, saldo_final):
        return EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cuenta_bancaria, banco='BBVA',
            periodo_mes=7, periodo_anio=2026, formato='PDF', estado='PROCESADO',
            fecha_corte_real=date(2026, 7, 31),
            saldo_inicial_estado=saldo_inicial, saldo_final_estado=saldo_final,
        )

    def test_periodo_cuadrado_da_cero(self):
        estado_cuenta = self._estado_cuenta(Decimal('0.00'), Decimal('1500.00'))
        mov_contable = self._poliza(date(2026, 7, 10), debe=Decimal('1500.00'))
        MovimientoEstadoCuenta.objects.create(
            estado_cuenta=estado_cuenta, fecha=date(2026, 7, 10),
            descripcion='SPEI RECIBIDO', abono=Decimal('1500.00'),
            movimiento_contable=mov_contable,
        )
        conciliacion = generar_conciliacion_preliminar(estado_cuenta, usuario=self.user)
        self.assertEqual(conciliacion.diferencia, Decimal('0.00'))
        self.assertEqual(conciliacion.diferencia_arrastrada, Decimal('0.00'))
        self.assertTrue(conciliacion.cuadra)

    def test_descuadre_previo_va_a_arrastrada_y_no_a_diferencia(self):
        """Escenario del reporte real: los libros traen saldo de pólizas
        anteriores al primer estado de cuenta cargado. El periodo sí cuadra."""
        self._poliza(date(2026, 3, 5), debe=Decimal('41968.96'), concepto='Histórico sin respaldo')
        estado_cuenta = self._estado_cuenta(Decimal('0.00'), Decimal('1500.00'))
        mov_contable = self._poliza(date(2026, 7, 10), debe=Decimal('1500.00'))
        MovimientoEstadoCuenta.objects.create(
            estado_cuenta=estado_cuenta, fecha=date(2026, 7, 10),
            descripcion='SPEI RECIBIDO', abono=Decimal('1500.00'),
            movimiento_contable=mov_contable,
        )
        conciliacion = generar_conciliacion_preliminar(estado_cuenta, usuario=self.user)
        self.assertEqual(conciliacion.diferencia_arrastrada, Decimal('41968.96'))
        self.assertEqual(conciliacion.diferencia, Decimal('0.00'))

    def test_deposito_en_transito_no_descuadra(self):
        """Póliza del periodo sobre bancos que el banco todavía no acredita."""
        estado_cuenta = self._estado_cuenta(Decimal('0.00'), Decimal('0.00'))
        self._poliza(date(2026, 7, 30), debe=Decimal('3200.00'), concepto='Cobro en tránsito')
        MovimientoEstadoCuenta.objects.create(
            estado_cuenta=estado_cuenta, fecha=date(2026, 7, 1),
            descripcion='Renglón de anclaje', abono=Decimal('0.00'), cargo=Decimal('0.00'),
        )
        conciliacion = generar_conciliacion_preliminar(estado_cuenta, usuario=self.user)
        self.assertEqual(conciliacion.abonos_empresa_no_abonados, Decimal('3200.00'))
        self.assertEqual(conciliacion.cargos_empresa_no_cobrados, Decimal('0.00'))
        self.assertEqual(conciliacion.diferencia, Decimal('0.00'))

    def test_salida_no_cobrada_por_el_banco_no_descuadra(self):
        estado_cuenta = self._estado_cuenta(Decimal('0.00'), Decimal('0.00'))
        self._poliza(date(2026, 7, 30), haber=Decimal('900.00'), concepto='Cheque en circulación')
        MovimientoEstadoCuenta.objects.create(
            estado_cuenta=estado_cuenta, fecha=date(2026, 7, 1),
            descripcion='Renglón de anclaje', abono=Decimal('0.00'), cargo=Decimal('0.00'),
        )
        conciliacion = generar_conciliacion_preliminar(estado_cuenta, usuario=self.user)
        self.assertEqual(conciliacion.cargos_empresa_no_cobrados, Decimal('900.00'))
        self.assertEqual(conciliacion.diferencia, Decimal('0.00'))

    def test_movimiento_del_banco_sin_asiento_sigue_contando(self):
        """No hay regresión en el lado del banco: la comisión que nadie
        registró sigue apareciendo como partida."""
        estado_cuenta = self._estado_cuenta(Decimal('0.00'), Decimal('-500.00'))
        MovimientoEstadoCuenta.objects.create(
            estado_cuenta=estado_cuenta, fecha=date(2026, 7, 5),
            descripcion='COM SERV BCA INTERNET', cargo=Decimal('500.00'),
        )
        conciliacion = generar_conciliacion_preliminar(estado_cuenta, usuario=self.user)
        self.assertEqual(conciliacion.cargos_banco_no_registrados, Decimal('500.00'))
        self.assertEqual(conciliacion.diferencia, Decimal('0.00'))

    def test_comando_diagnostico_no_escribe_nada(self):
        estado_cuenta = self._estado_cuenta(Decimal('0.00'), Decimal('1500.00'))
        self._poliza(date(2026, 7, 10), debe=Decimal('1500.00'))
        MovimientoEstadoCuenta.objects.create(
            estado_cuenta=estado_cuenta, fecha=date(2026, 7, 10),
            descripcion='SPEI RECIBIDO', abono=Decimal('1500.00'),
        )
        antes = (
            ConciliacionBancaria.objects.count(),
            Poliza.objects.count(),
            MovimientoContable.objects.count(),
            MovimientoEstadoCuenta.objects.count(),
        )
        salida = StringIO()
        call_command('diagnosticar_conciliacion', '--estado-cuenta', str(estado_cuenta.pk), stdout=salida)
        despues = (
            ConciliacionBancaria.objects.count(),
            Poliza.objects.count(),
            MovimientoContable.objects.count(),
            MovimientoEstadoCuenta.objects.count(),
        )
        self.assertEqual(antes, despues)
        self.assertIn('DIFERENCIA DEL PERIODO', salida.getvalue())


class EtiquetaMovimientoContableTest(TestCase):
    """La etiqueta del asiento tiene que distinguir dos importes iguales."""

    def setUp(self):
        self.user = User.objects.create_user('contador_etiqueta', password='x')
        self.unidad = UnidadNegocio.objects.get(clave='QUINTA')
        self.cuenta = CuentaContable.objects.get(codigo_sat='102.02.01')

    def test_incluye_folio_fecha_y_concepto(self):
        poliza = Poliza.objects.create(
            tipo='E', folio=Poliza.siguiente_folio('E', date(2026, 7, 12)),
            fecha=date(2026, 7, 12), concepto='Póliza de prueba',
            unidad_negocio=self.unidad, estado='APLICADA', origen='MANUAL',
            created_by=self.user,
        )
        mov = MovimientoContable.objects.create(
            poliza=poliza, cuenta=self.cuenta,
            debe=Decimal('1369.18'), concepto='Pago a proveedor de audio',
        )
        etiqueta = str(mov)
        self.assertIn('12/07/2026', etiqueta)
        self.assertIn('1,369.18', etiqueta)
        self.assertIn('102.02.01', etiqueta)
        self.assertIn('Pago a proveedor de audio', etiqueta)
        self.assertIn(f"E-{str(poliza.folio).zfill(4)}", etiqueta)


class AutocompleteAsientoBancarioTest(TestCase):
    """
    El buscador de asientos solo puede ofrecer candidatos legítimos: el
    autocomplete estándar del admin ofrecía cualquier MovimientoContable,
    incluidos los de cuentas de gastos.
    """

    def setUp(self):
        self.unidad = UnidadNegocio.objects.get(clave='QUINTA')
        self.cuenta_banco = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.cuenta_gasto = CuentaContable.objects.filter(
            tipo='GASTO', permite_movimientos=True
        ).first()
        self.cuenta_bancaria = CuentaBancaria.objects.create(
            nombre='BBVA Autocomplete', banco='BBVA',
            clabe='012345678901234573', cuenta_contable=self.cuenta_banco,
        )
        self.estado_cuenta = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cuenta_bancaria, banco='BBVA',
            periodo_mes=7, periodo_anio=2026, formato='PDF', estado='PROCESADO',
            fecha_corte_real=date(2026, 7, 31),
            saldo_inicial_estado=Decimal('0.00'), saldo_final_estado=Decimal('0.00'),
        )
        MovimientoEstadoCuenta.objects.create(
            estado_cuenta=self.estado_cuenta, fecha=date(2026, 7, 1),
            descripcion='Anclaje', cargo=Decimal('0.00'), abono=Decimal('0.00'),
        )
        self.staff = User.objects.create_user('staff_auto', password='x', is_staff=True)
        self.staff.user_permissions.add(
            Permission.objects.get(codename='view_movimientocontable')
        )
        self.url = reverse('contabilidad:autocomplete_asiento_bancario')

    def _movimiento(self, cuenta, fecha=date(2026, 7, 10), debe=Decimal('1000.00'),
                    estado='APLICADA', concepto='Asiento'):
        poliza = Poliza.objects.create(
            tipo='I', folio=Poliza.siguiente_folio('I', fecha), fecha=fecha,
            concepto=concepto, unidad_negocio=self.unidad, estado=estado,
            origen='MANUAL', created_by=self.staff,
        )
        return MovimientoContable.objects.create(
            poliza=poliza, cuenta=cuenta, debe=debe, concepto=concepto,
        )

    def _ids(self, respuesta):
        return {r['id'] for r in respuesta.json()['results']}

    def test_solo_ofrece_asientos_de_la_cuenta_de_bancos(self):
        del_banco = self._movimiento(self.cuenta_banco)
        de_gastos = self._movimiento(self.cuenta_gasto, concepto='Gasto ajeno al banco')
        self.client.force_login(self.staff)
        respuesta = self.client.get(self.url, {'estado_cuenta': self.estado_cuenta.pk})
        ids = self._ids(respuesta)
        self.assertIn(str(del_banco.pk), ids)
        self.assertNotIn(str(de_gastos.pk), ids)

    def test_excluye_polizas_no_aplicadas_y_ya_emparejadas(self):
        borrador = self._movimiento(self.cuenta_banco, estado='BORRADOR')
        tomado = self._movimiento(self.cuenta_banco, concepto='Ya emparejado')
        MovimientoEstadoCuenta.objects.create(
            estado_cuenta=self.estado_cuenta, fecha=date(2026, 7, 10),
            descripcion='Movimiento tomado', abono=Decimal('1000.00'),
            movimiento_contable=tomado,
        )
        self.client.force_login(self.staff)
        ids = self._ids(self.client.get(self.url, {'estado_cuenta': self.estado_cuenta.pk}))
        self.assertNotIn(str(borrador.pk), ids)
        self.assertNotIn(str(tomado.pk), ids)

    def test_busca_por_importe_y_por_concepto(self):
        objetivo = self._movimiento(self.cuenta_banco, debe=Decimal('1369.18'),
                                    concepto='Pago audio')
        self._movimiento(self.cuenta_banco, debe=Decimal('55.00'), concepto='Otra cosa')
        self.client.force_login(self.staff)
        por_importe = self._ids(self.client.get(
            self.url, {'estado_cuenta': self.estado_cuenta.pk, 'term': '1369.18'}))
        por_concepto = self._ids(self.client.get(
            self.url, {'estado_cuenta': self.estado_cuenta.pk, 'term': 'audio'}))
        self.assertEqual(por_importe, {str(objetivo.pk)})
        self.assertEqual(por_concepto, {str(objetivo.pk)})

    def test_sin_permiso_de_lectura_no_lista_asientos(self):
        sin_permiso = User.objects.create_user('staff_pelado', password='x', is_staff=True)
        self.client.force_login(sin_permiso)
        respuesta = self.client.get(self.url, {'estado_cuenta': self.estado_cuenta.pk})
        self.assertEqual(respuesta.status_code, 403)


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
    def test_procesa_con_storage_sin_soporte_de_rutas_absolutas(self):
        """
        Regresión del Issue #158: con Cloudflare R2 (S3Storage) el archivo no
        existe en el disco del contenedor y FieldFile.path no está soportado.
        """
        from unittest import mock

        from django.core.files import File
        from django.core.files.base import ContentFile
        from django.core.files.storage import Storage

        from contabilidad.services_estados_cuenta import procesar_estado_cuenta

        class StorageSinRutas(Storage):
            def __init__(self):
                self.archivos = {}

            def _open(self, name, mode='rb'):
                return ContentFile(self.archivos[name], name=name)

            def _save(self, name, content):
                self.archivos[name] = b''.join(content.chunks())
                return name

            def exists(self, name):
                return name in self.archivos

            def path(self, name):
                raise NotImplementedError("This backend doesn't support absolute paths.")

        cuenta = CuentaBancaria.objects.create(
            nombre="Cuenta libretón de prueba", banco="BBVA",
            numero_cuenta="1551774893", clabe="111111111111111111",
        )
        campo_archivo = EstadoCuentaBancario._meta.get_field('archivo')
        with mock.patch.object(campo_archivo, 'storage', StorageSinRutas()):
            with open(self.FIXTURE_LIBRETON, 'rb') as f:
                estado_cuenta = EstadoCuentaBancario.objects.create(
                    cuenta_bancaria=cuenta, banco='BBVA',
                    periodo_mes=3, periodo_anio=2026, formato='PDF',
                    archivo=File(f, name='estado_cuenta_bbva_libreton_ejemplo.pdf'),
                )
            estado_cuenta.refresh_from_db()

            procesar_estado_cuenta(estado_cuenta)
            estado_cuenta.refresh_from_db()
            self.assertEqual(estado_cuenta.estado, 'PROCESADO')
            self.assertEqual(estado_cuenta.error_detalle, '')
            self.assertEqual(estado_cuenta.saldo_inicial_estado, Decimal('3546.19'))
            self.assertEqual(estado_cuenta.saldo_final_estado, Decimal('15658.90'))
            self.assertEqual(estado_cuenta.fecha_corte_real, date(2026, 3, 14))
            self.assertEqual(estado_cuenta.movimientos.count(), 53)

            # Reprocesable: reemplaza los movimientos anteriores, no los duplica.
            procesar_estado_cuenta(estado_cuenta)
            estado_cuenta.refresh_from_db()
            self.assertEqual(estado_cuenta.estado, 'PROCESADO')
            self.assertEqual(estado_cuenta.movimientos.count(), 53)

    @unittest.skipUnless(os.path.exists(FIXTURE_LIBRETON), "Falta el fixture real estado_cuenta_bbva_libreton_ejemplo.pdf")
    def test_numero_cuenta_no_coincide_rechaza_la_carga(self):
        """
        Regresión directa del requisito de Elián: nunca debe ser posible que un
        estado de cuenta de una persona se procese contra la CuentaBancaria de otra.
        """
        import tempfile
        from unittest import mock

        from django.core.files import File
        from django.core.files.storage import FileSystemStorage

        from contabilidad.services_estados_cuenta import procesar_estado_cuenta

        cuenta_equivocada = CuentaBancaria.objects.create(
            nombre="Cuenta equivocada de prueba", banco="BBVA",
            numero_cuenta="0000000000", clabe="000000000000000000",
        )
        # El storage por default del proyecto es Cloudflare R2 (producción);
        # en tests se usa un FileSystemStorage local para no depender de
        # credenciales reales al guardar el archivo.
        campo_archivo = EstadoCuentaBancario._meta.get_field('archivo')
        with tempfile.TemporaryDirectory() as tmp_dir, \
                mock.patch.object(campo_archivo, 'storage', FileSystemStorage(location=tmp_dir)):
            with open(self.FIXTURE_LIBRETON, 'rb') as f:
                estado_cuenta = EstadoCuentaBancario.objects.create(
                    cuenta_bancaria=cuenta_equivocada, banco='BBVA',
                    periodo_mes=2, periodo_anio=2026, formato='PDF',
                    archivo=File(f, name='estado_cuenta_bbva_libreton_ejemplo.pdf'),
                )
            estado_cuenta.refresh_from_db()
            with self.assertRaisesMessage(ValueError, 'no coincide con'):
                procesar_estado_cuenta(estado_cuenta)
        estado_cuenta.refresh_from_db()
        self.assertEqual(estado_cuenta.estado, 'ERROR')
        self.assertIn('no coincide con', estado_cuenta.error_detalle)


class PeriodoDevengoComisionDiferidaTest(TestCase):
    """
    Comisión de banca por internet (SPEI/CECOBAN a bancos externos): BBVA la
    cobra a mes vencido (Contrato de Banca en Línea, Cláusula Cuarta), así que
    lo que aparece en el estado de cuenta de un mes corresponde al servicio
    del mes anterior — periodo_devengo debe reflejar eso, no 'fecha'.
    """

    def test_detecta_concepto_serv_banca_internet(self):
        from contabilidad.services_estados_cuenta import _es_comision_diferida
        self.assertTrue(_es_comision_diferida('SERV BANCA INTERNET SPEI ENVIADO'))
        self.assertTrue(_es_comision_diferida('IVA COM SERV BCA INTERNET'))
        self.assertFalse(_es_comision_diferida('SPEI ENVIADO A OTRO BANCO'))
        self.assertFalse(_es_comision_diferida(''))

    def test_periodo_devengo_mes_anterior(self):
        from contabilidad.services_estados_cuenta import _periodo_devengo_mes_anterior
        self.assertEqual(_periodo_devengo_mes_anterior(date(2025, 11, 30)), date(2025, 10, 1))

    def test_periodo_devengo_cambio_de_ano(self):
        from contabilidad.services_estados_cuenta import _periodo_devengo_mes_anterior
        self.assertEqual(_periodo_devengo_mes_anterior(date(2026, 1, 15)), date(2025, 12, 1))

    def test_procesar_estado_cuenta_asigna_periodo_devengo_a_comision(self):
        import tempfile
        from unittest import mock

        from django.core.files.base import ContentFile
        from django.core.files.storage import FileSystemStorage

        from contabilidad.services_estados_cuenta import procesar_estado_cuenta

        cuenta = CuentaBancaria.objects.create(
            nombre="Cuenta de prueba", banco="BBVA",
            numero_cuenta="1234567890", clabe="000000000000000000",
        )
        movimientos_falsos = [
            {'fecha': date(2025, 11, 30), 'descripcion': 'SERV BANCA INTERNET', 'referencia': '',
             'cargo': Decimal('6.50'), 'abono': Decimal('0.00'), 'saldo_parcial': Decimal('1000.00')},
            {'fecha': date(2025, 11, 30), 'descripcion': 'IVA COM SERV BCA INTERNET', 'referencia': '',
             'cargo': Decimal('1.04'), 'abono': Decimal('0.00'), 'saldo_parcial': Decimal('998.96')},
            {'fecha': date(2025, 11, 15), 'descripcion': 'DEPOSITO TRANSFERENCIA CLIENTE', 'referencia': '',
             'cargo': Decimal('0.00'), 'abono': Decimal('500.00'), 'saldo_parcial': Decimal('1005.50')},
        ]

        campo_archivo = EstadoCuentaBancario._meta.get_field('archivo')
        with tempfile.TemporaryDirectory() as tmp_dir, \
                mock.patch.object(campo_archivo, 'storage', FileSystemStorage(location=tmp_dir)), \
                mock.patch(
                    'contabilidad.services_estados_cuenta._parsear_pdf_bbva',
                    return_value=(movimientos_falsos, Decimal('994.96'), Decimal('998.96'), '1234567890', date(2025, 11, 30)),
                ):
            estado_cuenta = EstadoCuentaBancario.objects.create(
                cuenta_bancaria=cuenta, banco='BBVA',
                periodo_mes=11, periodo_anio=2025, formato='PDF',
                archivo=ContentFile(b'%PDF-1.4 fake', name='falso.pdf'),
            )
            procesar_estado_cuenta(estado_cuenta)

        comision = MovimientoEstadoCuenta.objects.get(descripcion='SERV BANCA INTERNET')
        iva_comision = MovimientoEstadoCuenta.objects.get(descripcion='IVA COM SERV BCA INTERNET')
        deposito = MovimientoEstadoCuenta.objects.get(descripcion='DEPOSITO TRANSFERENCIA CLIENTE')

        self.assertEqual(comision.periodo_devengo, date(2025, 10, 1))
        self.assertEqual(iva_comision.periodo_devengo, date(2025, 10, 1))
        self.assertIsNone(deposito.periodo_devengo)
        self.assertEqual(deposito.periodo_contable, date(2025, 11, 1))
        self.assertEqual(comision.periodo_contable, date(2025, 10, 1))


class PolizaPagoAirbnbTest(TestCase):
    """
    Póliza de un pago de Airbnb: cuadre con el IVA trasladado, regeneración
    al corregir el pago (reimportar el CSV) y reversión si deja de estar
    pagado.
    """

    # base 10,000 · IVA trasladado 1,600 · comisión 420 · ret. ISR 400 ·
    # ret. IVA 800  ->  depósito 9,980 y asiento de 11,600 por lado.
    MONTOS = {
        'monto_bruto': Decimal('10000.00'),
        'iva_trasladado': Decimal('1600.00'),
        'comision_airbnb': Decimal('420.00'),
        'retencion_isr': Decimal('400.00'),
        'retencion_iva': Decimal('800.00'),
        'monto_neto': Decimal('9980.00'),
    }

    def setUp(self):
        # Las cuentas de Airbnb ya vienen sembradas por las migraciones de
        # contabilidad; aquí solo hace falta el resto del catálogo mínimo.
        setup_contabilidad_minima()

    @staticmethod
    def _cuenta(operacion):
        return ConfiguracionContable.objects.get(operacion=operacion).cuenta

    def _crear_pago(self, **extra):
        from airbnb.models import PagoAirbnb
        datos = dict(
            huesped='Huésped Test',
            fecha_checkin=date(2026, 3, 10),
            fecha_checkout=date(2026, 3, 13),
            fecha_pago=date(2026, 3, 11),
            estado='PAGADO',
            **self.MONTOS,
        )
        datos.update(extra)
        return PagoAirbnb.objects.create(**datos)

    def _poliza_de(self, pago):
        return Poliza.objects.filter(origen='PAGO_AIRBNB', object_id=pago.pk).first()

    def test_poliza_cuadra_con_el_iva_trasladado_al_haber(self):
        """
        El depósito trae el IVA que Airbnb cobró al huésped, así que sin la
        línea de IVA trasladado el asiento no cuadraría por esos $1,600.
        """
        pago = self._crear_pago()
        poliza = self._poliza_de(pago)
        self.assertIsNotNone(poliza)
        self.assertEqual(poliza.estado, 'APLICADA')
        self.assertEqual(poliza.total_debe, Decimal('11600.00'))
        self.assertEqual(poliza.total_haber, Decimal('11600.00'))

        cuenta_iva = self._cuenta('IVA_TRASLADADO')
        haber_iva = sum(m.haber for m in poliza.movimientos.filter(cuenta=cuenta_iva))
        self.assertEqual(haber_iva, Decimal('1600.00'))

    def test_corregir_el_pago_regenera_la_poliza_en_sitio(self):
        """Reimportar el CSV corrige montos: la póliza los sigue, sin duplicarse."""
        pago = self._crear_pago()
        poliza = self._poliza_de(pago)
        pk_original, folio_original = poliza.pk, poliza.folio

        # 12,000 - 420 + 1,920 - 400 - 800 = 12,300 de depósito.
        pago.monto_bruto = Decimal('12000.00')
        pago.iva_trasladado = Decimal('1920.00')
        pago.monto_neto = Decimal('12300.00')
        pago.save()

        self.assertEqual(Poliza.objects.filter(origen='PAGO_AIRBNB').count(), 1)
        poliza.refresh_from_db()
        self.assertEqual((poliza.pk, poliza.folio), (pk_original, folio_original))
        self.assertEqual(poliza.total_debe, poliza.total_haber)
        self.assertEqual(poliza.total_haber, Decimal('13920.00'))

    def test_pago_pendiente_genera_poliza_al_marcarse_pagado(self):
        pago = self._crear_pago(estado='PENDIENTE')
        self.assertIsNone(self._poliza_de(pago))

        pago.estado = 'PAGADO'
        pago.save()
        self.assertIsNotNone(self._poliza_de(pago))

    def test_neto_que_no_cuadra_deja_la_poliza_en_borrador(self):
        """
        Un descuadre entre el neto y la fórmula no se aplica a los libros:
        queda en borrador para revisión.
        """
        pago = self._crear_pago(monto_neto=Decimal('9000.00'))
        self.assertEqual(self._poliza_de(pago).estado, 'BORRADOR')

    def test_reembolso_emite_reversion_sin_tocar_la_original(self):
        pago = self._crear_pago()
        poliza = self._poliza_de(pago)

        pago.estado = 'REEMBOLSADO'
        pago.save()

        poliza.refresh_from_db()
        self.assertEqual(poliza.estado, 'APLICADA')
        reversiones = Poliza.objects.filter(origen='AJUSTE', object_id=pago.pk)
        self.assertEqual(reversiones.count(), 1)
        reversion = reversiones.first()
        self.assertEqual(reversion.total_debe, Decimal('11600.00'))
        self.assertEqual(reversion.total_haber, Decimal('11600.00'))

        cuenta_ingreso = self._cuenta('INGRESO_AIRBNB')
        debe_ingreso = sum(
            m.debe for m in reversion.movimientos.filter(cuenta=cuenta_ingreso)
        )
        self.assertEqual(debe_ingreso, Decimal('10000.00'))

    def test_reversion_no_se_duplica_al_volver_a_guardar(self):
        pago = self._crear_pago()
        pago.estado = 'CANCELADO'
        pago.save()
        pago.save()
        self.assertEqual(Poliza.objects.filter(origen='AJUSTE', object_id=pago.pk).count(), 1)

    def test_volver_a_pagado_compensa_la_reversion(self):
        """
        Una reserva cancelada y reexpedida no puede quedar con el ingreso
        restado dos veces: la reactivación anula la reversión.
        """
        pago = self._crear_pago()
        pago.estado = 'CANCELADO'
        pago.save()
        pago.estado = 'PAGADO'
        pago.save()

        ajustes = Poliza.objects.filter(origen='AJUSTE', object_id=pago.pk)
        self.assertEqual(ajustes.count(), 2)
        neto_ajustes = sum(a.total_debe - a.total_haber for a in ajustes)
        self.assertEqual(neto_ajustes, Decimal('0.00'))

        cuenta_ingreso = self._cuenta('INGRESO_AIRBNB')
        movimientos = MovimientoContable.objects.filter(
            cuenta=cuenta_ingreso, poliza__estado='APLICADA'
        )
        saldo_ingreso = sum(m.haber - m.debe for m in movimientos)
        self.assertEqual(saldo_ingreso, Decimal('10000.00'))


class CorregirPolizasAirbnbIvaTest(TestCase):
    """
    Reemisión de las pólizas de Airbnb que se generaron sin IVA trasladado.

    Son las anteriores a que el modelo distinguiera ese IVA de las
    retenciones: cargaban el depósito completo a bancos —que ya lo incluye—
    pero solo abonaban el ingreso, así que el asiento descuadraba justo por
    el IVA que el anfitrión tiene que enterar.
    """

    MONTOS = PolizaPagoAirbnbTest.MONTOS

    def setUp(self):
        setup_contabilidad_minima()

    @staticmethod
    def _cuenta(operacion):
        return ConfiguracionContable.objects.get(operacion=operacion).cuenta

    def _pago_con_poliza_vieja(self):
        """Reproduce el asiento tal como se emitía antes de la corrección."""
        from airbnb.models import PagoAirbnb

        pago = PagoAirbnb.objects.create(
            huesped='Huésped Test',
            codigo_confirmacion='HMVIEJO',
            fecha_checkin=date(2026, 3, 10),
            fecha_checkout=date(2026, 3, 13),
            fecha_pago=date(2026, 3, 11),
            estado='PAGADO',
            **self.MONTOS,
        )
        poliza = Poliza.objects.get(origen='PAGO_AIRBNB', object_id=pago.pk)
        poliza.movimientos.filter(cuenta=self._cuenta('IVA_TRASLADADO')).delete()
        return pago, poliza

    @staticmethod
    def _correr(*argumentos):
        from io import StringIO

        from django.core.management import call_command

        salida = StringIO()
        call_command('corregir_polizas_airbnb_iva', *argumentos,
                     stdout=salida, stderr=salida)
        return salida.getvalue()

    def test_la_simulacion_no_escribe_nada(self):
        pago, poliza = self._pago_con_poliza_vieja()

        self._correr()

        poliza.refresh_from_db()
        self.assertEqual(poliza.estado, 'APLICADA')
        self.assertEqual(
            Poliza.objects.filter(object_id=pago.pk).count(), 1)

    def test_reemite_la_poliza_con_el_iva_trasladado(self):
        pago, vieja = self._pago_con_poliza_vieja()
        self.assertEqual(vieja.total_debe - vieja.total_haber, Decimal('1600.00'))

        self._correr('--aplicar')

        vieja.refresh_from_db()
        self.assertEqual(vieja.estado, 'CANCELADA')
        nueva = (Poliza.objects
                 .filter(origen='PAGO_AIRBNB', object_id=pago.pk)
                 .exclude(pk=vieja.pk).get())
        self.assertEqual(nueva.estado, 'APLICADA')
        self.assertEqual(nueva.total_debe, nueva.total_haber)
        self.assertEqual(
            nueva.movimientos.get(cuenta=self._cuenta('IVA_TRASLADADO')).haber,
            Decimal('1600.00'))

    def test_no_borra_ni_edita_el_asiento_original(self):
        """Queda cancelado y con su motivo, pero con sus movimientos intactos."""
        pago, vieja = self._pago_con_poliza_vieja()
        movimientos_antes = list(
            vieja.movimientos.values_list('cuenta_id', 'debe', 'haber'))

        self._correr('--aplicar')

        vieja.refresh_from_db()
        self.assertEqual(
            list(vieja.movimientos.values_list('cuenta_id', 'debe', 'haber')),
            movimientos_antes)
        self.assertIn('IVA trasladado', vieja.motivo_cancelacion)

    def test_el_mayor_queda_cuadrado_despues_de_corregir(self):
        """
        Lo que importa: sumando original, reversión y póliza nueva, el debe y
        el haber del mayor coinciden. Antes faltaban $1,600 del lado del haber.
        """
        self._pago_con_poliza_vieja()

        self._correr('--aplicar')

        movimientos = MovimientoContable.objects.filter(
            poliza__estado='APLICADA')
        debe = sum(m.debe for m in movimientos)
        haber = sum(m.haber for m in movimientos)
        self.assertEqual(debe, haber)

    def test_el_iva_trasladado_queda_registrado_una_sola_vez(self):
        pago, _ = self._pago_con_poliza_vieja()

        self._correr('--aplicar')

        movimientos = MovimientoContable.objects.filter(
            cuenta=self._cuenta('IVA_TRASLADADO'), poliza__estado='APLICADA')
        saldo = sum(m.haber - m.debe for m in movimientos)
        self.assertEqual(saldo, Decimal('1600.00'))

    def test_correrlo_dos_veces_no_duplica_nada(self):
        pago, _ = self._pago_con_poliza_vieja()

        self._correr('--aplicar')
        polizas = Poliza.objects.filter(object_id=pago.pk).count()
        self._correr('--aplicar')

        self.assertEqual(Poliza.objects.filter(object_id=pago.pk).count(),
                         polizas)

    def test_no_toca_las_polizas_que_ya_traen_el_iva(self):
        from airbnb.models import PagoAirbnb

        pago = PagoAirbnb.objects.create(
            huesped='Huésped Test', codigo_confirmacion='HMNUEVO',
            fecha_checkin=date(2026, 3, 10), fecha_checkout=date(2026, 3, 13),
            fecha_pago=date(2026, 3, 11), estado='PAGADO', **self.MONTOS,
        )

        self._correr('--aplicar')

        self.assertEqual(Poliza.objects.filter(object_id=pago.pk).count(), 1)
        self.assertEqual(
            Poliza.objects.get(object_id=pago.pk).estado, 'APLICADA')


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "privado": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class PantallasConciliacionAdminTest(TestCase):
    """
    Smoke test de las dos pantallas que usa quien concilia. Cubre que los
    fieldsets, los displays de solo lectura y el widget acotado del inline
    rendericen — un nombre de campo mal escrito en `fieldsets` solo revienta
    al abrir la página, no en `manage.py check`.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser('admin_conc', 'a@b.mx', 'x')
        self.unidad = UnidadNegocio.objects.get(clave='QUINTA')
        cuenta_contable = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.cuenta_bancaria = CuentaBancaria.objects.create(
            nombre='BBVA Pantallas', banco='BBVA',
            clabe='012345678901234574', cuenta_contable=cuenta_contable,
        )
        self.estado_cuenta = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cuenta_bancaria, banco='BBVA',
            periodo_mes=7, periodo_anio=2026, formato='PDF', estado='PROCESADO',
            fecha_corte_real=date(2026, 7, 31),
            saldo_inicial_estado=Decimal('0.00'), saldo_final_estado=Decimal('500.00'),
            archivo=SimpleUploadedFile('edo.pdf', b'%PDF-1.4 fake', content_type='application/pdf'),
        )
        poliza = Poliza.objects.create(
            tipo='I', folio=Poliza.siguiente_folio('I', date(2026, 7, 10)),
            fecha=date(2026, 7, 10), concepto='Cobro', unidad_negocio=self.unidad,
            estado='APLICADA', origen='MANUAL', created_by=self.admin,
        )
        self.mov_contable = MovimientoContable.objects.create(
            poliza=poliza, cuenta=cuenta_contable, debe=Decimal('500.00'), concepto='Cobro',
        )
        self.mov_banco = MovimientoEstadoCuenta.objects.create(
            estado_cuenta=self.estado_cuenta, fecha=date(2026, 7, 10),
            descripcion='SPEI RECIBIDO SANTANDER MUY LARGO PARA PROBAR EL TRUNCADO',
            abono=Decimal('500.00'), movimiento_contable=self.mov_contable,
        )
        login_superuser_con_totp(self.client, self.admin)

    def test_pantalla_estado_de_cuenta_abre(self):
        url = f'/admin/contabilidad/estadocuentabancario/{self.estado_cuenta.pk}/change/'
        respuesta = self.client.get(url)
        self.assertEqual(respuesta.status_code, 200)
        contenido = respuesta.content.decode()
        # El buscador de asientos apunta a la vista acotada, no al autocomplete
        # genérico del admin.
        self.assertIn(
            f"asiento-bancario/?estado_cuenta={self.estado_cuenta.pk}", contenido
        )
        self.assertIn('Confirmado', contenido)

    def test_pantalla_conciliacion_explica_el_cuadre(self):
        conciliacion = generar_conciliacion_preliminar(self.estado_cuenta, usuario=self.admin)
        url = f'/admin/contabilidad/conciliacionbancaria/{conciliacion.pk}/change/'
        respuesta = self.client.get(url)
        self.assertEqual(respuesta.status_code, 200)
        contenido = respuesta.content.decode()
        self.assertIn('Depósitos en tránsito', contenido)
        self.assertIn('Diferencia del periodo', contenido)

    def test_no_se_pueden_editar_los_importes_del_banco(self):
        """Los importes son copia del PDF: el formulario no los expone, así que
        un POST que intente cambiarlos no altera el dato."""
        url = f'/admin/contabilidad/estadocuentabancario/{self.estado_cuenta.pk}/change/'
        prefijo = 'movimientos'
        respuesta = self.client.post(url, {
            'cuenta_bancaria': self.cuenta_bancaria.pk,
            'banco': 'BBVA', 'periodo_mes': 7, 'periodo_anio': 2026,
            'formato': 'PDF', 'origen': 'MANUAL',
            f'{prefijo}-TOTAL_FORMS': '1', f'{prefijo}-INITIAL_FORMS': '1',
            f'{prefijo}-MIN_NUM_FORMS': '0', f'{prefijo}-MAX_NUM_FORMS': '1000',
            f'{prefijo}-0-id': self.mov_banco.pk,
            f'{prefijo}-0-estado_cuenta': self.estado_cuenta.pk,
            f'{prefijo}-0-abono': '999999.00',
            f'{prefijo}-0-movimiento_contable': self.mov_contable.pk,
            f'{prefijo}-0-confirmado': 'on',
        })
        # El POST tiene que haber sido aceptado: si el formulario no valida, la
        # prueba pasaría sin haber demostrado que el campo es de solo lectura.
        self.assertEqual(respuesta.status_code, 302)
        self.mov_banco.refresh_from_db()
        self.assertTrue(self.mov_banco.confirmado)
        self.assertEqual(self.mov_banco.abono, Decimal('500.00'))


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "privado": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class ReasignarAsientoTest(TestCase):
    """
    Corregir un emparejamiento mal hecho: quitar el asiento y poner otro. Cubre
    los dos candados que evitan que la corrección rompa algo por el camino.

    El estado de cuenta lleva archivo de verdad (en InMemoryStorage) porque sin
    él el formulario del admin no valida y el POST no llegaría a guardar nada:
    la prueba pasaría sin haber probado nada.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser('admin_reasig', 'r@b.mx', 'x')
        self.unidad = UnidadNegocio.objects.get(clave='QUINTA')
        self.cuenta_contable = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.cuenta_bancaria = CuentaBancaria.objects.create(
            nombre='BBVA Reasignar', banco='BBVA',
            clabe='012345678901234575', cuenta_contable=self.cuenta_contable,
        )
        self.estado_cuenta = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cuenta_bancaria, banco='BBVA',
            periodo_mes=7, periodo_anio=2026, formato='PDF', estado='PROCESADO',
            fecha_corte_real=date(2026, 7, 31),
            saldo_inicial_estado=Decimal('0.00'), saldo_final_estado=Decimal('0.00'),
            archivo=SimpleUploadedFile('edo.pdf', b'%PDF-1.4 fake', content_type='application/pdf'),
        )
        self.asiento_a = self._asiento('Pago proveedor audio')
        self.asiento_b = self._asiento('Pago proveedor flores')
        self.mov_1 = MovimientoEstadoCuenta.objects.create(
            estado_cuenta=self.estado_cuenta, fecha=date(2026, 7, 10),
            descripcion='SPEI ENVIADO UNO', cargo=Decimal('500.00'),
            movimiento_contable=self.asiento_a,
        )
        self.mov_2 = MovimientoEstadoCuenta.objects.create(
            estado_cuenta=self.estado_cuenta, fecha=date(2026, 7, 11),
            descripcion='SPEI ENVIADO DOS', cargo=Decimal('500.00'),
            movimiento_contable=self.asiento_b,
        )
        login_superuser_con_totp(self.client, self.admin)
        self.url = f'/admin/contabilidad/estadocuentabancario/{self.estado_cuenta.pk}/change/'

    def _asiento(self, concepto):
        poliza = Poliza.objects.create(
            tipo='E', folio=Poliza.siguiente_folio('E', date(2026, 7, 10)),
            fecha=date(2026, 7, 10), concepto=concepto, unidad_negocio=self.unidad,
            estado='APLICADA', origen='MANUAL', created_by=self.admin,
        )
        return MovimientoContable.objects.create(
            poliza=poliza, cuenta=self.cuenta_contable,
            haber=Decimal('500.00'), concepto=concepto,
        )

    def _post(self, asiento_1, asiento_2):
        return self.client.post(self.url, {
            'cuenta_bancaria': self.cuenta_bancaria.pk, 'banco': 'BBVA',
            'periodo_mes': 7, 'periodo_anio': 2026, 'formato': 'PDF', 'origen': 'MANUAL',
            'movimientos-TOTAL_FORMS': '2', 'movimientos-INITIAL_FORMS': '2',
            'movimientos-MIN_NUM_FORMS': '0', 'movimientos-MAX_NUM_FORMS': '1000',
            'movimientos-0-id': self.mov_1.pk,
            'movimientos-0-estado_cuenta': self.estado_cuenta.pk,
            'movimientos-0-movimiento_contable': asiento_1,
            'movimientos-1-id': self.mov_2.pk,
            'movimientos-1-estado_cuenta': self.estado_cuenta.pk,
            'movimientos-1-movimiento_contable': asiento_2,
        })

    def test_se_puede_quitar_el_asiento_dejando_el_campo_vacio(self):
        """Es lo que hace la × del buscador: mandar el campo en blanco."""
        self._post('', self.asiento_b.pk)
        self.mov_1.refresh_from_db()
        self.assertIsNone(self.mov_1.movimiento_contable)

    def test_no_se_puede_asignar_el_mismo_asiento_a_dos_movimientos(self):
        """Un asiento contado dos veces desaparece de las partidas de la
        conciliación y su descuadre no se ve por ningún lado."""
        respuesta = self._post(self.asiento_a.pk, self.asiento_a.pk)
        self.assertEqual(respuesta.status_code, 200)  # vuelve a mostrar el form
        self.mov_2.refresh_from_db()
        self.assertEqual(self.mov_2.movimiento_contable, self.asiento_b)
        self.assertContains(respuesta, 'ya está asignado al movimiento', status_code=200)

    def test_el_buscador_no_ofrece_borrar_el_asiento(self):
        """La X roja del widget de Django borra el MovimientoContable de la BD,
        no lo deselecciona: en esta pantalla no debe existir."""
        contenido = self.client.get(self.url).content.decode()
        inline = contenido[contenido.index('id="movimientos-group"'):]
        self.assertNotIn('delete-related', inline)
        self.assertFalse(
            MovimientoContableAdmin(MovimientoContable, admin.site)
            .has_delete_permission(None)
        )


class RegularizacionArrastreTest(TestCase):
    """
    La diferencia arrastrada se cancela con una póliza que nace en BORRADOR y
    solo Dirección puede aplicar.
    """

    def setUp(self):
        self.contador = User.objects.create_user(
            'contador_regu', password='x', is_staff=True,
        )
        for codename in ('view_poliza', 'change_poliza',
                         'view_conciliacionbancaria', 'change_conciliacionbancaria'):
            self.contador.user_permissions.add(Permission.objects.get(codename=codename))
        self.direccion = User.objects.create_superuser('direccion_regu', 'd@qkt.mx', 'x')

        self.unidad = UnidadNegocio.objects.get(clave='QUINTA')
        self.cuenta_banco = CuentaContable.objects.get(codigo_sat='102.02.01')
        ConfiguracionContable.objects.get_or_create(
            operacion='AJUSTE_APERTURA',
            defaults={'cuenta': CuentaContable.objects.get(codigo_sat='304.01'), 'activa': True},
        )
        self.cuenta_bancaria = CuentaBancaria.objects.create(
            nombre='BBVA Regularización', banco='BBVA',
            clabe='012345678901234576', cuenta_contable=self.cuenta_banco,
        )
        self.estado_cuenta = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cuenta_bancaria, banco='BBVA',
            periodo_mes=7, periodo_anio=2026, formato='PDF', estado='PROCESADO',
            fecha_corte_real=date(2026, 7, 31),
            saldo_inicial_estado=Decimal('0.00'), saldo_final_estado=Decimal('1500.00'),
        )
        # Histórico sin respaldo bancario: el arrastre del caso real.
        self._poliza(date(2026, 3, 5), debe=Decimal('41968.96'), concepto='Histórico')
        mov_contable = self._poliza(date(2026, 7, 10), debe=Decimal('1500.00'))
        MovimientoEstadoCuenta.objects.create(
            estado_cuenta=self.estado_cuenta, fecha=date(2026, 7, 10),
            descripcion='SPEI RECIBIDO', abono=Decimal('1500.00'),
            movimiento_contable=mov_contable,
        )
        self.conciliacion = generar_conciliacion_preliminar(
            self.estado_cuenta, usuario=self.direccion
        )
        self.url = '/admin/contabilidad/conciliacionbancaria/'

    def _poliza(self, fecha, debe=Decimal('0.00'), haber=Decimal('0.00'), concepto='Movimiento'):
        tipo = 'I' if debe else 'E'
        poliza = Poliza.objects.create(
            tipo=tipo, folio=Poliza.siguiente_folio(tipo, fecha), fecha=fecha,
            concepto=concepto, unidad_negocio=self.unidad, estado='APLICADA',
            origen='MANUAL', created_by=self.direccion,
        )
        return MovimientoContable.objects.create(
            poliza=poliza, cuenta=self.cuenta_banco, debe=debe, haber=haber, concepto=concepto,
        )

    def _accion(self, usuario, accion):
        if usuario.is_superuser:
            login_superuser_con_totp(self.client, usuario)
        else:
            self.client.force_login(usuario)
        return self.client.post(self.url, {
            'action': accion,
            '_selected_action': [str(self.conciliacion.pk)],
            'confirmar': 'si',
        }, follow=True)

    # -- La propuesta -------------------------------------------------

    def test_la_propuesta_nace_en_borrador_y_no_mueve_saldos(self):
        saldo_antes = self.cuenta_bancaria.saldo_a_fecha(date(2026, 7, 31))

        poliza = proponer_regularizacion_arrastre(self.conciliacion, usuario=self.contador)

        self.assertEqual(poliza.estado, 'BORRADOR')
        self.assertEqual(poliza.origen, 'APERTURA')
        self.assertTrue(poliza.esta_cuadrada)
        self.assertEqual(poliza.total_debe, Decimal('41968.96'))
        # Una póliza en borrador no entra en ningún saldo del ERP.
        self.assertEqual(self.cuenta_bancaria.saldo_a_fecha(date(2026, 7, 31)), saldo_antes)

    def test_se_fecha_el_dia_anterior_al_periodo(self):
        """Solo con esa fecha la cancela: el arrastre se mide al cierre del día
        anterior al primer movimiento."""
        poliza = proponer_regularizacion_arrastre(self.conciliacion, usuario=self.contador)
        self.assertEqual(poliza.fecha, self.conciliacion.fecha_inicio_periodo - timedelta(days=1))

    def test_abona_a_bancos_cuando_los_libros_traen_de_mas(self):
        poliza = proponer_regularizacion_arrastre(self.conciliacion, usuario=self.contador)
        mov_banco = poliza.movimientos.get(cuenta=self.cuenta_banco)
        self.assertEqual(mov_banco.haber, Decimal('41968.96'))
        self.assertEqual(mov_banco.debe, Decimal('0.00'))

    def test_no_duplica_la_propuesta(self):
        primera = proponer_regularizacion_arrastre(self.conciliacion, usuario=self.contador)
        segunda = proponer_regularizacion_arrastre(self.conciliacion, usuario=self.contador)
        self.assertEqual(primera.pk, segunda.pk)
        self.assertEqual(segunda.movimientos.count(), 2)

    def test_sin_arrastre_no_propone_nada(self):
        self.conciliacion.diferencia_arrastrada = Decimal('0.00')
        self.conciliacion.save()
        with self.assertRaises(ValueError):
            proponer_regularizacion_arrastre(self.conciliacion, usuario=self.contador)

    # -- La autorización ----------------------------------------------

    def test_el_contador_no_puede_autorizar(self):
        proponer_regularizacion_arrastre(self.conciliacion, usuario=self.contador)

        self._accion(self.contador, 'aprobar_regularizacion')

        poliza = Poliza.objects.get(origen='APERTURA')
        self.assertEqual(poliza.estado, 'BORRADOR')

    def test_el_contador_tampoco_puede_aplicarla_desde_la_pantalla_de_polizas(self):
        """El candado no puede depender de una sola pantalla."""
        poliza = proponer_regularizacion_arrastre(self.conciliacion, usuario=self.contador)

        self.client.force_login(self.contador)
        self.client.post('/admin/contabilidad/poliza/', {
            'action': 'aplicar_polizas',
            '_selected_action': [str(poliza.pk)],
            'confirmar': 'si',
        }, follow=True)

        poliza.refresh_from_db()
        self.assertEqual(poliza.estado, 'BORRADOR')

    def test_sin_confirmar_no_autoriza_nada(self):
        """SEC-BIZ-002: un solo POST sin 'confirmar=si' no debe aplicar la
        regularización, aunque sea Dirección quien lo mande."""
        proponer_regularizacion_arrastre(self.conciliacion, usuario=self.contador)

        login_superuser_con_totp(self.client, self.direccion)
        respuesta = self.client.post(self.url, {
            'action': 'aprobar_regularizacion',
            '_selected_action': [str(self.conciliacion.pk)],
        }, follow=True)

        poliza = Poliza.objects.get(origen='APERTURA')
        self.assertEqual(poliza.estado, 'BORRADOR')
        self.assertContains(respuesta, '¿Confirmar esta acción?')

    def test_direccion_autoriza_y_queda_asentado_quien_fue(self):
        proponer_regularizacion_arrastre(self.conciliacion, usuario=self.contador)

        self._accion(self.direccion, 'aprobar_regularizacion')

        poliza = Poliza.objects.get(origen='APERTURA')
        self.assertEqual(poliza.estado, 'APLICADA')
        self.assertEqual(poliza.aplicada_por, self.direccion)
        self.assertIsNotNone(poliza.fecha_aplicacion)

    def test_tras_autorizar_la_conciliacion_queda_sin_arrastre(self):
        proponer_regularizacion_arrastre(self.conciliacion, usuario=self.contador)
        self._accion(self.direccion, 'aprobar_regularizacion')

        conciliacion = generar_conciliacion_preliminar(
            self.estado_cuenta, usuario=self.direccion
        )

        self.assertEqual(conciliacion.diferencia_arrastrada, Decimal('0.00'))
        self.assertEqual(conciliacion.diferencia, Decimal('0.00'))
        self.assertTrue(conciliacion.cuadra)

    def test_no_se_autoriza_dos_veces(self):
        poliza = proponer_regularizacion_arrastre(self.conciliacion, usuario=self.contador)
        aprobar_regularizacion_arrastre(poliza, usuario=self.direccion)
        with self.assertRaises(ValueError):
            aprobar_regularizacion_arrastre(poliza, usuario=self.direccion)

    def test_no_autoriza_una_poliza_que_no_es_regularizacion(self):
        poliza = Poliza.objects.create(
            tipo='D', folio=Poliza.siguiente_folio('D', date(2026, 7, 15)),
            fecha=date(2026, 7, 15), concepto='Cualquier cosa', unidad_negocio=self.unidad,
            estado='BORRADOR', origen='MANUAL', created_by=self.contador,
        )
        with self.assertRaises(ValueError):
            aprobar_regularizacion_arrastre(poliza, usuario=self.direccion)


class CerrarHistoricoContableTest(TestCase):
    """
    Arrancar los libros del ERP en una fecha: cancela lo anterior, no lo borra.
    """

    def setUp(self):
        self.direccion = User.objects.create_superuser('direccion_cierre', 'd@qkt.mx', 'x')
        self.contador = User.objects.create_user(
            'contador_cierre', password='x', is_staff=True,
        )
        self.unidad = UnidadNegocio.objects.get(clave='QUINTA')
        self.cuenta_banco = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.cuenta_bancaria = CuentaBancaria.objects.create(
            nombre='BBVA Cierre', banco='BBVA',
            clabe='012345678901234577', cuenta_contable=self.cuenta_banco,
        )
        self.vieja = self._poliza(date(2026, 3, 5), debe=Decimal('41968.96'))
        self.borrador_viejo = self._poliza(
            date(2026, 5, 20), debe=Decimal('100.00'), estado='BORRADOR',
        )
        self.nueva = self._poliza(date(2026, 7, 10), debe=Decimal('1500.00'))
        self.url = reverse('contabilidad:cerrar_historico')

    def _poliza(self, fecha, debe=Decimal('0.00'), estado='APLICADA'):
        poliza = Poliza.objects.create(
            tipo='I', folio=Poliza.siguiente_folio('I', fecha), fecha=fecha,
            concepto='Movimiento', unidad_negocio=self.unidad, estado=estado,
            origen='MANUAL', created_by=self.direccion,
        )
        MovimientoContable.objects.create(
            poliza=poliza, cuenta=self.cuenta_banco, debe=debe, concepto='x',
        )
        return poliza

    # -- El servicio ---------------------------------------------------

    def test_simular_no_escribe_nada(self):
        informe = cerrar_historico_contable(
            date(2026, 6, 30), usuario=self.direccion, aplicar=False,
        )
        self.assertEqual(informe['total'], 2)
        self.assertFalse(informe['aplicado'])
        self.vieja.refresh_from_db()
        self.assertEqual(self.vieja.estado, 'APLICADA')

    def test_cancela_lo_anterior_y_respeta_lo_posterior(self):
        cerrar_historico_contable(date(2026, 6, 30), usuario=self.direccion, aplicar=True)

        self.vieja.refresh_from_db()
        self.borrador_viejo.refresh_from_db()
        self.nueva.refresh_from_db()
        self.assertEqual(self.vieja.estado, 'CANCELADA')
        # Los borradores viejos también: si no, alguien podría aplicarlos
        # después y volver a meter movimiento en un periodo ya cerrado.
        self.assertEqual(self.borrador_viejo.estado, 'CANCELADA')
        self.assertEqual(self.nueva.estado, 'APLICADA')

    def test_no_borra_las_polizas_ni_sus_movimientos(self):
        polizas_antes = Poliza.objects.count()
        movimientos_antes = MovimientoContable.objects.count()

        cerrar_historico_contable(date(2026, 6, 30), usuario=self.direccion, aplicar=True)

        self.assertEqual(Poliza.objects.count(), polizas_antes)
        self.assertEqual(MovimientoContable.objects.count(), movimientos_antes)
        self.assertEqual(self.vieja.movimientos.count(), 1)

    def test_asienta_quien_lo_hizo_y_por_que(self):
        cerrar_historico_contable(date(2026, 6, 30), usuario=self.direccion, aplicar=True)

        self.vieja.refresh_from_db()
        self.assertEqual(self.vieja.cancelada_por, self.direccion)
        self.assertIsNotNone(self.vieja.fecha_cancelacion)
        self.assertIn('Cierre de histórico', self.vieja.motivo_cancelacion)

    def test_el_saldo_de_bancos_deja_de_arrastrar_el_historico(self):
        antes = self.cuenta_bancaria.saldo_a_fecha(date(2026, 6, 30))
        self.assertEqual(antes, Decimal('41968.96'))

        cerrar_historico_contable(date(2026, 6, 30), usuario=self.direccion, aplicar=True)

        self.assertEqual(self.cuenta_bancaria.saldo_a_fecha(date(2026, 6, 30)), Decimal('0.00'))
        # Lo de julio sigue intacto.
        self.assertEqual(self.cuenta_bancaria.saldo_a_fecha(date(2026, 7, 31)), Decimal('1500.00'))

    def test_rechaza_una_fecha_futura(self):
        with self.assertRaises(ValueError):
            cerrar_historico_contable(
                date.today() + timedelta(days=1), usuario=self.direccion, aplicar=True,
            )

    def test_es_idempotente(self):
        cerrar_historico_contable(date(2026, 6, 30), usuario=self.direccion, aplicar=True)
        segundo = cerrar_historico_contable(
            date(2026, 6, 30), usuario=self.direccion, aplicar=True,
        )
        self.assertEqual(segundo['total'], 0)

    # -- La pantalla ---------------------------------------------------

    def test_solo_direccion_entra(self):
        self.client.force_login(self.contador)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(
            self.client.post(self.url, {'fecha_corte': '2026-06-30', 'accion': 'aplicar'}).status_code,
            403,
        )
        self.vieja.refresh_from_db()
        self.assertEqual(self.vieja.estado, 'APLICADA')

    def test_direccion_simula_desde_la_pantalla(self):
        login_superuser_con_totp(self.client, self.direccion)
        respuesta = self.client.post(
            self.url, {'fecha_corte': '2026-06-30', 'accion': 'simular'},
        )
        self.assertEqual(respuesta.status_code, 200)
        self.vieja.refresh_from_db()
        self.assertEqual(self.vieja.estado, 'APLICADA')

    def test_direccion_aplica_desde_la_pantalla(self):
        login_superuser_con_totp(self.client, self.direccion)
        respuesta = self.client.post(
            self.url, {'fecha_corte': '2026-06-30', 'accion': 'aplicar'},
        )
        self.assertEqual(respuesta.status_code, 200)
        self.vieja.refresh_from_db()
        self.assertEqual(self.vieja.estado, 'CANCELADA')

    def test_una_fecha_invalida_no_revienta_la_pantalla(self):
        login_superuser_con_totp(self.client, self.direccion)
        respuesta = self.client.post(
            self.url, {'fecha_corte': 'no-es-fecha', 'accion': 'simular'},
        )
        self.assertEqual(respuesta.status_code, 200)
        self.vieja.refresh_from_db()
        self.assertEqual(self.vieja.estado, 'APLICADA')

    def test_el_comando_simula_por_defecto(self):
        salida = StringIO()
        call_command('cerrar_historico_contable', '--hasta', '2026-06-30', stdout=salida)
        self.assertIn('SIMULACIÓN', salida.getvalue())
        self.vieja.refresh_from_db()
        self.assertEqual(self.vieja.estado, 'APLICADA')

    def test_el_comando_aplica_con_la_bandera(self):
        salida = StringIO()
        call_command(
            'cerrar_historico_contable', '--hasta', '2026-06-30', '--aplicar',
            '--usuario', 'direccion_cierre', stdout=salida,
        )
        self.vieja.refresh_from_db()
        self.assertEqual(self.vieja.estado, 'CANCELADA')
        self.assertEqual(self.vieja.cancelada_por, self.direccion)


class PolizaAdminAccionesConfirmacionTest(TestCase):
    """SEC-BIZ-002: 'Aplicar'/'Cancelar pólizas' desde el admin exigen un
    segundo POST con 'confirmar=si' — un solo POST directo no las dispara."""

    def setUp(self):
        self.direccion = User.objects.create_superuser('direccion_confirma', 'd@qkt.mx', 'x')
        login_superuser_con_totp(self.client, self.direccion)
        self.unidad = UnidadNegocio.objects.get(clave='QUINTA')
        self.cuenta = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.contra = CuentaContable.objects.get(codigo_sat='402.02')
        self.poliza = Poliza.objects.create(
            tipo='I', folio=Poliza.siguiente_folio('I', date(2026, 8, 1)),
            fecha=date(2026, 8, 1), concepto='Prueba', unidad_negocio=self.unidad,
            estado='BORRADOR', origen='MANUAL', created_by=self.direccion,
        )
        MovimientoContable.objects.create(poliza=self.poliza, cuenta=self.cuenta, debe=Decimal('100.00'))
        MovimientoContable.objects.create(poliza=self.poliza, cuenta=self.contra, haber=Decimal('100.00'))
        self.url = '/admin/contabilidad/poliza/'

    def test_aplicar_sin_confirmar_no_cambia_el_estado(self):
        respuesta = self.client.post(self.url, {
            'action': 'aplicar_polizas',
            '_selected_action': [str(self.poliza.pk)],
        }, follow=True)

        self.poliza.refresh_from_db()
        self.assertEqual(self.poliza.estado, 'BORRADOR')
        self.assertContains(respuesta, '¿Confirmar esta acción?')

    def test_aplicar_con_confirmar_si_aplica(self):
        self.client.post(self.url, {
            'action': 'aplicar_polizas',
            '_selected_action': [str(self.poliza.pk)],
            'confirmar': 'si',
        }, follow=True)

        self.poliza.refresh_from_db()
        self.assertEqual(self.poliza.estado, 'APLICADA')

    def test_cancelar_sin_confirmar_no_cambia_el_estado(self):
        self.poliza.estado = 'APLICADA'
        self.poliza.save(update_fields=['estado'])

        respuesta = self.client.post(self.url, {
            'action': 'cancelar_polizas',
            '_selected_action': [str(self.poliza.pk)],
        }, follow=True)

        self.poliza.refresh_from_db()
        self.assertEqual(self.poliza.estado, 'APLICADA')
        self.assertContains(respuesta, '¿Confirmar esta acción?')

    def test_cancelar_con_confirmar_si_cancela(self):
        self.poliza.estado = 'APLICADA'
        self.poliza.save(update_fields=['estado'])

        self.client.post(self.url, {
            'action': 'cancelar_polizas',
            '_selected_action': [str(self.poliza.pk)],
            'confirmar': 'si',
        }, follow=True)

        self.poliza.refresh_from_db()
        self.assertEqual(self.poliza.estado, 'CANCELADA')
