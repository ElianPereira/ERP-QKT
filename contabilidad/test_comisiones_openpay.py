"""Comisiones de Openpay: cuenta puente entre lo descontado de cada depósito y
la factura mensual de Openpay."""
from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import MagicMock, patch

from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase

from comercial.models import Cliente, Compra, OpenpayTransaccion, Pago, Proveedor

from .models import EstadoCuentaBancario, MovimientoContable, MovimientoEstadoCuenta, Poliza
from .signals import crear_poliza_comision_openpay, get_cuenta, get_usuario_sistema
from .test_clasificacion_compras import ClasificacionBase
from .tests import _crear_cotizacion


class ComisionesOpenpayBase(ClasificacionBase):
    def setUp(self):
        super().setUp()
        self.puente = get_cuenta('COMISIONES_POR_COMPROBAR')
        self.banco = get_cuenta('BANCO_PRINCIPAL')
        self.openpay = Proveedor.objects.create(
            nombre='OPENPAY TEST', rfc='OPE010101AAA', descuenta_de_depositos=True,
        )
        cot = _crear_cotizacion(Cliente.objects.create(nombre='Cliente'), Decimal('11600.00'))
        pago = Pago.objects.create(cotizacion=cot, monto=Decimal('1500.00'), metodo='PLATAFORMA', referencia='txpuente')
        self.transaccion = OpenpayTransaccion.objects.create(
            openpay_id='txpuente', metodo='card', monto=Decimal('1500.00'),
            pago=pago, payload_crudo={}, procesado=True,
        )

    def _factura_openpay(self):
        return Compra.objects.create(
            proveedor=self.openpay, proveedor_nombre=self.openpay.nombre,
            subtotal=Decimal('10.53'), iva=Decimal('1.68'), total=Decimal('12.21'),
            uuid='UUID-OPENPAY-0001', fecha_emision=date(2026, 9, 30), unidad_negocio=self.unidad,
        )

    def _poliza(self, compra, **filtros):
        return Poliza.objects.get(
            content_type=ContentType.objects.get_for_model(compra), object_id=compra.pk, origen='COMPRA', **filtros)


class CuentaPuenteTest(ComisionesOpenpayBase):

    def test_la_comision_del_cobro_va_a_la_cuenta_puente_sin_gasto_ni_iva(self):
        poliza = crear_poliza_comision_openpay(self.transaccion, {'amount': 8.00, 'tax': 1.28})
        lineas = {(m.cuenta, m.debe, m.haber) for m in poliza.movimientos.all()}
        self.assertEqual(lineas, {
            (self.puente, Decimal('9.28'), Decimal('0.00')),
            (self.banco, Decimal('0.00'), Decimal('9.28')),
        })

    def test_la_factura_mensual_no_toca_bancos(self):
        compra = self._factura_openpay()
        self.assertIsNone(compra.cuenta_pago)
        poliza = self._poliza(compra)
        self.assertEqual(poliza.estado, 'APLICADA')
        self.assertFalse(poliza.movimientos.filter(cuenta=self.cuenta_banco).exists())
        self.assertEqual(poliza.movimientos.get(cuenta=self.puente).haber, Decimal('12.21'))
        self.assertEqual(poliza.movimientos.get(cuenta=self.iva).debe, Decimal('1.68'))

    def test_la_factura_mensual_no_se_sugiere_contra_un_cargo_del_banco(self):
        from .services_estados_cuenta import compras_candidatas
        self._factura_openpay()
        estado = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cuenta_bancaria, banco='BBVA', periodo_mes=9,
            periodo_anio=2026, formato='PDF', estado='PROCESADO',
        )
        cargo = MovimientoEstadoCuenta.objects.create(
            estado_cuenta=estado, fecha=date(2026, 9, 30), descripcion='CARGO', cargo=Decimal('12.21'),
        )
        self.assertEqual(compras_candidatas(cargo, self.unidad), [])


class RegularizarComisionesOpenpayTest(ComisionesOpenpayBase):
    """Lo registrado antes del cambio: comisión como gasto + IVA y factura de
    Openpay pagada desde el banco."""

    def setUp(self):
        super().setUp()
        self.vieja = Poliza.objects.create(
            tipo='E', folio=Poliza.siguiente_folio('E', self.transaccion.pago.fecha_pago),
            fecha=self.transaccion.pago.fecha_pago, concepto='Comisión Openpay (esquema anterior)',
            unidad_negocio=self.unidad, estado='APLICADA', origen='COMISION_OPENPAY',
            content_type=ContentType.objects.get_for_model(self.transaccion), object_id=self.transaccion.pk,
            created_by=get_usuario_sistema(),
        )
        for cuenta, debe, haber in [(self.generales, '8.00', '0'), (self.iva, '1.28', '0'), (self.banco, '0', '9.28')]:
            MovimientoContable.objects.create(
                poliza=self.vieja, cuenta=cuenta, debe=Decimal(debe), haber=Decimal(haber), concepto='x')

        self.openpay.descuenta_de_depositos = False
        self.openpay.save()
        self.compra = self._factura_openpay()
        self.openpay.descuenta_de_depositos = True
        self.openpay.save()
        self.factura_vieja = self._poliza(self.compra)

    def test_simulacion_no_escribe(self):
        call_command('regularizar_comisiones_openpay', stdout=StringIO())
        self.vieja.refresh_from_db()
        self.factura_vieja.refresh_from_db()
        self.assertEqual(self.vieja.estado, 'APLICADA')
        self.assertEqual(self.factura_vieja.estado, 'APLICADA')

    def test_aplicar_cancela_y_reemite_contra_la_cuenta_puente(self):
        call_command('regularizar_comisiones_openpay', '--aplicar', stdout=StringIO())
        self.vieja.refresh_from_db()
        self.factura_vieja.refresh_from_db()
        self.assertEqual(self.vieja.estado, 'CANCELADA')
        self.assertEqual(self.factura_vieja.estado, 'CANCELADA')

        nueva = Poliza.objects.get(origen='COMISION_OPENPAY', estado='APLICADA')
        self.assertEqual(nueva.movimientos.get(cuenta=self.puente).debe, Decimal('9.28'))
        nueva_factura = self._poliza(self.compra, estado='APLICADA')
        self.assertFalse(nueva_factura.movimientos.filter(cuenta=self.cuenta_banco).exists())
        self.assertEqual(nueva_factura.movimientos.get(cuenta=self.puente).haber, Decimal('12.21'))
        self.compra.refresh_from_db()
        self.assertIsNone(self.compra.cuenta_pago)

    def test_aplicar_dos_veces_no_duplica(self):
        call_command('regularizar_comisiones_openpay', '--aplicar', stdout=StringIO())
        call_command('regularizar_comisiones_openpay', '--aplicar', stdout=StringIO())
        self.assertEqual(Poliza.objects.filter(origen='COMISION_OPENPAY', estado='APLICADA').count(), 1)
        self.assertEqual(Poliza.objects.filter(
            origen='COMPRA', object_id=self.compra.pk, estado='APLICADA').count(), 1)


CARGOS_SEPTIEMBRE = [
    {'id': 'txpuente', 'status': 'completed', 'method': 'bank_account', 'fee': {'amount': 8.00, 'tax': 1.28}},
    {'id': 'txajeno', 'status': 'completed', 'method': 'card', 'fee': {'amount': 2.53, 'tax': 0.40}},
    {'id': 'txfallido', 'status': 'failed', 'method': 'card', 'fee': {'amount': 2.53, 'tax': 0.40}},
]


@patch('contabilidad.management.commands.sincronizar_comisiones_openpay.listar_cargos_openpay',
       return_value=CARGOS_SEPTIEMBRE)
class SincronizarComisionesOpenpayTest(ComisionesOpenpayBase):

    def _correr(self, *args):
        salida = StringIO()
        call_command('sincronizar_comisiones_openpay', '--mes', '2026-09', *args, stdout=salida)
        return salida.getvalue()

    def _comisiones(self):
        return Poliza.objects.filter(origen='COMISION_OPENPAY').exclude(estado='CANCELADA')

    def test_simulacion_reporta_sin_escribir(self, _listar):
        salida = self._correr()
        self.assertIn('$12.21', salida)  # 9.28 + 2.93; el fallido no cuenta
        self.assertFalse(self._comisiones().exists())

    def test_aplicar_registra_la_faltante(self, _listar):
        salida = self._correr('--aplicar')
        poliza = self._comisiones().get()
        self.assertEqual(poliza.movimientos.get(cuenta=self.puente).debe, Decimal('9.28'))
        self.assertIn('1 cobrados en Openpay sin registro', salida)

    def test_no_duplica_la_que_ya_existe(self, _listar):
        crear_poliza_comision_openpay(self.transaccion, {'amount': 8.00, 'tax': 1.28})
        self._correr('--aplicar')
        self.assertEqual(self._comisiones().count(), 1)

    def test_mes_anterior_por_default(self, listar):
        with patch('django.utils.timezone.localdate', return_value=date(2026, 10, 2)):
            call_command('sincronizar_comisiones_openpay', stdout=StringIO())
        listar.assert_called_once_with(date(2026, 9, 1), date(2026, 9, 30))


class ListarCargosOpenpayTest(TestCase):

    @patch('comercial.services_openpay.requests.get')
    def test_recorre_todas_las_paginas(self, get):
        from comercial.services_openpay import listar_cargos_openpay
        paginas = [[{'id': f'a{i}'} for i in range(100)], [{'id': 'b0'}]]
        get.side_effect = [MagicMock(json=MagicMock(return_value=p)) for p in paginas]
        cargos = listar_cargos_openpay(date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(len(cargos), 101)
        self.assertEqual(get.call_args_list[1].kwargs['params']['offset'], 100)
        self.assertEqual(get.call_args_list[0].kwargs['params']['creation[gte]'], '2026-09-01')
