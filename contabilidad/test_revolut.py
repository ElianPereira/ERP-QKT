"""
Revolut como cuenta pagadora (Issue #341): importador CSV, traspasos entre
cuentas propias, cuenta pagadora de las compras y reglas de sistema.

El CSV replica la estructura del export real de agosto 2026 (dos productos,
intereses diarios, un envío devuelto, un cargo revertido), con importes y
comercios inventados.
"""
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from comercial.models import Compra

from .models import (
    ConfiguracionContable,
    CuentaBancaria,
    CuentaContable,
    EstadoCuentaBancario,
    MovimientoEstadoCuenta,
    Poliza,
    UnidadNegocio,
)
from .services_estados_cuenta import _parsear_csv_revolut, emparejar_y_asentar, procesar_estado_cuenta
from .services_reglas_banco import clasificar_movimiento, clave_aprendizaje
from .signals import get_cuenta_egreso
from .test_reglas_banco import ReglasBancoBase

ENCABEZADO = 'Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,State,Balance\n'
CSV_AGOSTO = ENCABEZADO + (
    'Transfer,Current,2026-08-01 10:00:00,2026-08-01 10:00:10,SPEI Transfer sent to BBVA MEXICO,-300.00,0.00,MXN,COMPLETED,700.00\n'
    'Card Payment,Current,2026-08-02 09:00:00,2026-08-03 01:00:00,Railway,-109.36,0.00,MXN,COMPLETED,590.64\n'
    'Transfer,Current,2026-08-03 12:00:00,2026-08-03 12:00:00,To Instant Access Savings,-500.00,0.00,MXN,COMPLETED,90.64\n'
    'Transfer,Current,2026-08-04 17:00:00,2026-08-04 17:00:10,SPEI Transfer sent to SANTANDER,-50.00,0.00,MXN,COMPLETED,40.64\n'
    'Refund,Current,2026-08-04 17:00:30,2026-08-04 17:00:30,SPEI transfer refund from STP,50.00,0.00,MXN,COMPLETED,90.64\n'
    'Card Payment,Current,2026-08-05 08:00:00,2026-08-05 09:00:00,Tienda Ejemplo,-20.00,1.50,MXN,COMPLETED,69.14\n'
    'Card Payment,Current,2026-08-31 16:00:00,,Railway,-17.09,0.00,MXN,REVERTED,\n'
    'Interest,Instant Access Savings,2026-08-01 00:10:00,2026-08-01 00:10:00,"Net Interest Paid to \'Instant Access Savings\' for Aug 1, 2026",0.03,0.00,MXN,COMPLETED,200.03\n'
    'Transfer,Instant Access Savings,2026-08-03 12:00:00,2026-08-03 12:00:00,To Instant Access Savings,500.00,0.00,MXN,COMPLETED,700.03\n'
    'Interest,Instant Access Savings,2026-08-04 00:10:00,2026-08-04 00:10:00,"Net Interest Paid to \'Instant Access Savings\' for Aug 4, 2026",0.27,0.02,MXN,COMPLETED,700.28\n'
)
STORAGES_MEMORIA = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "privado": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _csv(texto):
    return BytesIO(texto.encode('utf-8'))


class ParserCsvRevolutTest(TestCase):
    def test_lee_el_export_y_cuadra_saldos_de_los_dos_productos(self):
        movs, inicial, final = _parsear_csv_revolut(_csv(CSV_AGOSTO))
        self.assertEqual(inicial, Decimal('1200.00'))  # 1000 Current + 200 ahorro
        self.assertEqual(final, Decimal('769.42'))     # 69.14 + 700.28
        self.assertEqual(movs[-1]['saldo_parcial'], final)
        descripciones = [m['descripcion'] for m in movs]
        # El paso entre productos, el envío devuelto y el cargo revertido no son movimientos.
        self.assertNotIn('To Instant Access Savings', descripciones)
        self.assertNotIn('SPEI Transfer sent to SANTANDER', descripciones)
        self.assertEqual(descripciones.count('Railway'), 1)

    def test_intereses_en_un_abono_y_la_retencion_en_un_cargo(self):
        movs, _, _ = _parsear_csv_revolut(_csv(CSV_AGOSTO))
        intereses = [m for m in movs if m['referencia'] == 'Interest']
        self.assertEqual([(m['abono'], m['cargo']) for m in intereses],
                         [(Decimal('0.30'), Decimal('0.00')), (Decimal('0.00'), Decimal('0.02'))])

    def test_la_comision_va_como_cargo_aparte(self):
        movs, _, _ = _parsear_csv_revolut(_csv(CSV_AGOSTO))
        self.assertTrue(any(m['descripcion'] == 'Revolut comision Tienda Ejemplo' and m['cargo'] == Decimal('1.50')
                            for m in movs))

    def test_rechaza_otra_moneda(self):
        texto = ENCABEZADO + 'Card Payment,Current,2026-08-02 09:00:00,2026-08-02 09:00:00,Shop,-5.00,0.00,USD,COMPLETED,95.00\n'
        with self.assertRaisesMessage(ValueError, 'USD'):
            _parsear_csv_revolut(_csv(texto))

    def test_rechaza_un_saldo_que_no_cuadra(self):
        texto = ENCABEZADO + (
            'Card Payment,Current,2026-08-02 09:00:00,2026-08-02 09:00:00,A,-5.00,0.00,MXN,COMPLETED,95.00\n'
            'Card Payment,Current,2026-08-03 09:00:00,2026-08-03 09:00:00,B,-5.00,0.00,MXN,COMPLETED,80.00\n'
        )
        with self.assertRaisesMessage(ValueError, 'no cuadra'):
            _parsear_csv_revolut(_csv(texto))

    def test_rechaza_un_archivo_que_no_es_de_revolut(self):
        with self.assertRaisesMessage(ValueError, 'Revolut'):
            _parsear_csv_revolut(_csv('Fecha,Concepto,Importe\n01/08/2026,x,1.00\n'))


@override_settings(STORAGES=STORAGES_MEMORIA)
class ProcesarCsvRevolutTest(ReglasBancoBase):
    def setUp(self):
        super().setUp()
        self.revolut = CuentaBancaria.objects.create(
            nombre='Revolut', banco='Revolut', clabe='848180000000000001', rol='PAGO',
            cuenta_contable=CuentaContable.objects.get(codigo_sat='102.02.03'),
            unidad_negocio=UnidadNegocio.objects.get(clave='QUINTA'),
        )

    def _estado_revolut(self, texto, mes=8):
        estado = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.revolut, banco='Revolut', periodo_mes=mes, periodo_anio=2026, formato='CSV',
            archivo=SimpleUploadedFile('revolut.csv', texto.encode('utf-8'), content_type='text/csv'),
        )
        return procesar_estado_cuenta(estado)

    def test_procesa_el_csv_con_corte_al_fin_de_mes(self):
        estado = self._estado_revolut(CSV_AGOSTO)
        self.assertEqual(estado.estado, 'PROCESADO')
        self.assertEqual(estado.fecha_corte_real, date(2026, 8, 31))
        self.assertEqual(estado.saldo_final_estado, Decimal('769.42'))

    def test_rechaza_movimientos_de_otro_mes(self):
        with self.assertRaisesMessage(ValueError, 'fuera de 09/2026'):
            self._estado_revolut(CSV_AGOSTO, mes=9)

    def test_railway_se_asienta_como_gasto_no_deducible(self):
        estado = self._estado_revolut(CSV_AGOSTO)
        mov = estado.movimientos.get(descripcion='Railway')
        no_deducible = ConfiguracionContable.obtener_cuenta('GASTO_NO_DEDUCIBLE')
        self.assertTrue(mov.movimiento_contable.poliza.movimientos.filter(
            cuenta=no_deducible, debe=Decimal('109.36')).exists())

    def test_intereses_a_ingresos_financieros(self):
        estado = self._estado_revolut(CSV_AGOSTO)
        mov = estado.movimientos.get(abono=Decimal('0.30'))
        self.assertTrue(mov.movimiento_contable.poliza.movimientos.filter(
            cuenta__codigo_sat='402.01', haber=Decimal('0.30')).exists())

    def test_clasificar_un_comercio_de_revolut_lo_recuerda(self):
        estado = self._estado_revolut(CSV_AGOSTO)
        mov = estado.movimientos.get(descripcion='Tienda Ejemplo')
        self.assertEqual(clave_aprendizaje(mov)['patrones'], 'TIENDA EJEMPLO')
        _, regla = clasificar_movimiento(mov, self.gasto, self.usuario)
        self.assertTrue(regla.coincide(mov))

    def test_traspaso_bbva_revolut_con_las_dos_puntas(self):
        abono_bbva = self._mov('SPEI RECIBIDOSTP 0198331268 646 0050826TRASPASO REVOLUT', abono='300.00', dia=1)
        estado = self._estado_revolut(CSV_AGOSTO)
        cargo_revolut = estado.movimientos.get(descripcion='SPEI Transfer sent to BBVA MEXICO')
        abono_bbva.refresh_from_db()
        poliza = cargo_revolut.movimiento_contable.poliza
        self.assertEqual(abono_bbva.movimiento_contable.poliza, poliza)
        self.assertEqual(poliza.tipo, 'D')
        self.assertEqual(poliza.estado, 'APLICADA')
        self.assertEqual(set(poliza.movimientos.values_list('cuenta__codigo_sat', 'debe', 'haber')), {
            ('102.02.01', Decimal('300.00'), Decimal('0.00')),
            ('102.02.03', Decimal('0.00'), Decimal('300.00')),
        })

    def test_el_traspaso_sustituye_la_aportacion_asentada_antes(self):
        abono_bbva = self._mov('SPEI RECIBIDOSTP 0198331268 646 0050826TRASPASO REVOLUT', abono='300.00', dia=1)
        emparejar_y_asentar(self.estado, usuario=self.usuario)  # Revolut aún sin cargar
        abono_bbva.refresh_from_db()
        aportacion = abono_bbva.movimiento_contable.poliza
        self.assertTrue(aportacion.movimientos.filter(cuenta=self.aportaciones).exists())

        self._estado_revolut(CSV_AGOSTO)
        abono_bbva.refresh_from_db()
        aportacion.refresh_from_db()
        self.assertEqual(aportacion.estado, 'CANCELADA')
        self.assertFalse(abono_bbva.movimiento_contable.poliza.movimientos.filter(cuenta=self.aportaciones).exists())

    def test_un_cliente_que_paga_por_stp_no_es_traspaso(self):
        deposito = self._mov('SPEI RECIBIDOSTP 0198331268 646 0050826PAGO EVENTO', abono='450.00', dia=20)
        self._estado_revolut(CSV_AGOSTO)
        deposito.refresh_from_db()
        self.assertIsNone(deposito.movimiento_contable)


class CuentaPagadoraTest(TestCase):
    def setUp(self):
        self.unidad = UnidadNegocio.objects.get(clave='QUINTA')
        CuentaBancaria.objects.filter(unidad_negocio=self.unidad).update(activa=False)
        self.bbva = CuentaBancaria.objects.create(
            nombre='BBVA', banco='BBVA', clabe='012345678901230001', unidad_negocio=self.unidad,
            cuenta_contable=CuentaContable.objects.get(codigo_sat='102.02.01'),
        )

    def _compra(self):
        return Compra.objects.create(
            proveedor_nombre='Proveedor', subtotal=Decimal('100.00'), iva=Decimal('16.00'), total=Decimal('116.00'),
            fecha_emision=date(2026, 9, 5), unidad_negocio=self.unidad,
        )

    def test_con_una_sola_cuenta_se_queda_como_antes(self):
        self.assertEqual(self._compra().cuenta_pago, self.bbva)
        self.assertEqual(get_cuenta_egreso(), ConfiguracionContable.obtener_cuenta('BANCO_PRINCIPAL'))

    def test_la_cuenta_pagadora_recibe_las_compras_y_las_devoluciones(self):
        revolut = CuentaBancaria.objects.create(
            nombre='Revolut', banco='Revolut', clabe='848180000000000002', unidad_negocio=self.unidad, rol='PAGO',
            cuenta_contable=CuentaContable.objects.get(codigo_sat='102.02.03'),
        )
        self.assertEqual(revolut.textos_traspaso, 'BBVA MEXICO')
        self.assertEqual(self.bbva.textos_traspaso, '*STP|REVOLUT')
        self.assertEqual(self._compra().cuenta_pago, revolut)
        self.assertEqual(get_cuenta_egreso(), revolut.cuenta_contable)

    def test_con_dos_cuentas_y_ninguna_pagadora_no_se_adivina(self):
        CuentaBancaria.objects.create(
            nombre='Otra', banco='Revolut', clabe='848180000000000003', unidad_negocio=self.unidad,
        )
        self.assertIsNone(self._compra().cuenta_pago)


class TraspasoNoDuplicaPolizasTest(ReglasBancoBase):
    """Un traspaso ya emparejado no vuelve a asentarse al re-emparejar."""

    def test_reemparejar_es_idempotente(self):
        revolut = CuentaBancaria.objects.create(
            nombre='Revolut', banco='Revolut', clabe='848180000000000004',
            cuenta_contable=CuentaContable.objects.get(codigo_sat='102.02.03'),
        )
        estado_rev = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=revolut, banco='Revolut', periodo_mes=8, periodo_anio=2026, formato='CSV',
            estado='PROCESADO', fecha_corte_real=date(2026, 8, 31),
        )
        self._mov('SPEI RECIBIDOSTP 646 TRASPASO', abono='300.00', dia=1)
        MovimientoEstadoCuenta.objects.create(
            estado_cuenta=estado_rev, fecha=date(2026, 8, 1), descripcion='SPEI Transfer sent to BBVA MEXICO',
            cargo=Decimal('300.00'),
        )
        emparejar_y_asentar(estado_rev, usuario=self.usuario)
        emparejar_y_asentar(self.estado, usuario=self.usuario)
        emparejar_y_asentar(estado_rev, usuario=self.usuario)
        tipo = ContentType.objects.get_for_model(MovimientoEstadoCuenta)
        self.assertEqual(Poliza.objects.filter(content_type=tipo, tipo='D', estado='APLICADA').count(), 1)
