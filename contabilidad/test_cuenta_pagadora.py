"""Cuenta pagadora (Issue #499, PR 1): rol de cuenta, defaults de cobro/pago y conciliación que reapunta.
Todos los datos son ficticios."""
import uuid
from datetime import date
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase

from comercial.models import Cliente, Compra, Pago, Proveedor
from comercial.services_deposito import asegurar_deposito, registrar_recepcion

from .models import (
    ConfiguracionContable,
    CuentaBancaria,
    CuentaContable,
    EstadoCuentaBancario,
    MovimientoEstadoCuenta,
    Poliza,
    SaldoApertura,
    UnidadNegocio,
)
from .services_compras import ReapunteRechazado, reapuntar_banco_de_compra
from .services_estados_cuenta import analizar_conciliacion, emparejar_y_asentar
from .tests import _crear_cotizacion


class PagadoraBase(TestCase):
    def setUp(self):
        self.unidad = UnidadNegocio.objects.get(clave='QUINTA')
        CuentaBancaria.objects.filter(unidad_negocio=self.unidad).update(activa=False)
        self.contable_cobro = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.contable_pago = CuentaContable.objects.get(codigo_sat='102.02.04')
        self.cobro = CuentaBancaria.objects.create(
            nombre='Ingresos', banco='BBVA', clabe='012345678901230001', numero_cuenta='1110000001',
            cuenta_contable=self.contable_cobro, unidad_negocio=self.unidad, rol='COBRO')
        self.proveedor = Proveedor.objects.create(nombre='Hielera Test', rfc='HIE010101AAA')

    def _pagadora(self, **extra):
        return CuentaBancaria.objects.create(
            nombre='Egresos', banco='BBVA', clabe='012345678901230002', numero_cuenta='1110000002',
            cuenta_contable=self.contable_pago, unidad_negocio=self.unidad, rol='PAGO', **extra)

    def _compra(self, total='116.00', **extra):
        extra.setdefault('uuid', str(uuid.uuid4()))
        return Compra.objects.create(
            proveedor=self.proveedor, proveedor_nombre=self.proveedor.nombre, subtotal=Decimal('100.00'),
            iva=Decimal('16.00'), total=Decimal(total), fecha_emision=date(2026, 11, 5),
            unidad_negocio=self.unidad, **extra)

    def _cot(self, precio='11600.00'):
        return _crear_cotizacion(Cliente.objects.create(nombre='Cliente Ficticio'), Decimal(precio))

    def _poliza(self, obj, origen):
        return Poliza.objects.get(
            content_type=ContentType.objects.get_for_model(obj), object_id=obj.pk, origen=origen,
        ).movimientos.all()

    def _estado(self, cuenta, mes=11):
        return EstadoCuentaBancario.objects.create(
            cuenta_bancaria=cuenta, banco='BBVA', periodo_mes=mes, periodo_anio=2026, formato='PDF',
            estado='PROCESADO', fecha_corte_real=date(2026, mes, 28), saldo_inicial_estado=Decimal('0.00'),
            saldo_final_estado=Decimal('0.00'))


class RolYDefaultsTest(PagadoraBase):
    def test_cuenta_contable_de_egresos_existe(self):
        cuenta = CuentaContable.objects.get(codigo_sat='102.02.04')
        self.assertEqual((cuenta.tipo, cuenta.naturaleza, cuenta.nivel, cuenta.padre.codigo_sat),
                         ('ACTIVO', 'D', 4, '102.02'))

    def test_una_sola_cuenta_es_la_pagadora_aunque_sea_de_cobro(self):
        self.assertEqual(CuentaBancaria.pagadora(self.unidad.pk), self.cobro)
        compra = self._compra()
        self.assertEqual(compra.cuenta_pago, self.cobro)
        self.assertEqual(Poliza.objects.get(origen='COMPRA', object_id=compra.pk).estado, 'APLICADA')

    def test_con_cobro_y_pago_la_compra_va_a_la_pagadora(self):
        pagadora = self._pagadora()
        self.assertEqual(CuentaBancaria.pagadora(self.unidad.pk), pagadora)
        compra = self._compra()
        self.assertEqual(compra.cuenta_pago, pagadora)
        self.assertTrue(self._poliza(compra, 'COMPRA').filter(cuenta=self.contable_pago, haber=Decimal('116.00')))

    def test_dos_cuentas_ambos_no_se_adivina(self):
        CuentaBancaria.objects.filter(pk=self.cobro.pk).update(rol='AMBOS')
        self._pagadora()
        CuentaBancaria.objects.filter(clabe='012345678901230002').update(rol='AMBOS')
        self.assertIsNone(CuentaBancaria.pagadora(self.unidad.pk))
        compra = self._compra()
        self.assertIsNone(compra.cuenta_pago_id)
        self.assertEqual(Poliza.objects.get(origen='COMPRA', object_id=compra.pk).estado, 'BORRADOR')

    def test_dos_ambos_la_compra_sin_cuenta_se_completa_con_la_del_estado(self):
        CuentaBancaria.objects.filter(pk=self.cobro.pk).update(rol='AMBOS')
        otra = self._pagadora()
        CuentaBancaria.objects.filter(pk=otra.pk).update(rol='AMBOS')
        compra = self._compra()
        self.assertIsNone(compra.cuenta_pago_id)
        estado = self._estado(otra)
        cargo = MovimientoEstadoCuenta.objects.create(
            estado_cuenta=estado, fecha=date(2026, 11, 6), descripcion='PAGO HIELERA', cargo=Decimal('116.00'))
        emparejar_y_asentar(estado)
        compra.refresh_from_db()
        cargo.refresh_from_db()
        self.assertEqual(compra.cuenta_pago, otra)
        self.assertEqual(cargo.movimiento_contable.cuenta, self.contable_pago)
        self.assertEqual(Poliza.objects.get(origen='COMPRA', object_id=compra.pk).estado, 'APLICADA')

    def test_dos_pagadoras_no_se_adivina(self):
        self._pagadora()
        CuentaBancaria.objects.filter(pk=self.cobro.pk).update(rol='PAGO')
        self.assertIsNone(CuentaBancaria.pagadora(self.unidad.pk))


class PagosYReembolsosTest(PagadoraBase):
    def test_cobro_sin_cuenta_va_a_banco_principal(self):
        pagadora = self._pagadora()
        pago = Pago.objects.create(cotizacion=self._cot(), monto=Decimal('1000.00'), metodo='TRANSFERENCIA')
        self.assertIsNone(pago.cuenta_bancaria_id)
        lineas = self._poliza(pago, 'PAGO_CLIENTE')
        self.assertTrue(lineas.filter(cuenta=self.contable_cobro, debe=Decimal('1000.00')))
        self.assertFalse(lineas.filter(cuenta=pagadora.cuenta_contable))

    def test_cobro_recibido_en_la_pagadora_asienta_en_su_cuenta_y_la_comision_tpv_la_sigue(self):
        pagadora = self._pagadora()
        pago = Pago.objects.create(
            cotizacion=self._cot(), monto=Decimal('1000.00'), metodo='TARJETA_DEBITO',
            cuenta_bancaria=pagadora, comision_tpv=Decimal('23.20'))
        self.assertTrue(self._poliza(pago, 'PAGO_CLIENTE').filter(cuenta=self.contable_pago, debe=Decimal('1000.00')))
        self.assertTrue(self._poliza(pago, 'COMISION_TPV').filter(cuenta=self.contable_pago, haber=Decimal('23.20')))

    def test_reembolso_por_transferencia_sale_de_la_pagadora(self):
        pagadora = self._pagadora()
        cot = self._cot()
        Pago.objects.create(cotizacion=cot, monto=Decimal('1000.00'), metodo='TRANSFERENCIA')
        reembolso = Pago.objects.create(
            cotizacion=cot, tipo='REEMBOLSO', monto=Decimal('400.00'), metodo='TRANSFERENCIA')
        self.assertEqual(reembolso.cuenta_bancaria, pagadora)
        self.assertTrue(self._poliza(reembolso, 'PAGO_CLIENTE').filter(cuenta=self.contable_pago, haber=Decimal('400.00')))

    def test_reembolso_de_tarjeta_sale_de_donde_entro_el_cobro(self):
        self._pagadora()
        cot = self._cot()
        Pago.objects.create(cotizacion=cot, monto=Decimal('1000.00'), metodo='PLATAFORMA')
        reembolso = Pago.objects.create(cotizacion=cot, tipo='REEMBOLSO', monto=Decimal('400.00'), metodo='PLATAFORMA')
        self.assertIsNone(reembolso.cuenta_bancaria_id)
        self.assertTrue(self._poliza(reembolso, 'PAGO_CLIENTE').filter(cuenta=self.contable_cobro, haber=Decimal('400.00')))

    def test_efectivo_con_cuenta_bancaria_es_error(self):
        pago = Pago(cotizacion=self._cot(), monto=Decimal('100.00'), metodo='EFECTIVO', cuenta_bancaria=self.cobro)
        with self.assertRaises(ValidationError):
            pago.full_clean()
        with self.assertRaises(ValidationError):
            pago.save()

    def test_cuenta_inactiva_es_error(self):
        CuentaBancaria.objects.filter(pk=self.cobro.pk).update(activa=False)
        self.cobro.refresh_from_db()
        pago = Pago(cotizacion=self._cot(), monto=Decimal('100.00'), metodo='TRANSFERENCIA', cuenta_bancaria=self.cobro)
        with self.assertRaises(ValidationError):
            pago.full_clean()

    def test_devolucion_de_deposito_por_transferencia_sale_de_la_pagadora(self):
        pagadora = self._pagadora()
        deposito = asegurar_deposito(self._cot(), Decimal('1000.00'))
        registrar_recepcion(deposito, monto=Decimal('1000.00'), metodo='TRANSFERENCIA')
        from comercial.models import MovimientoDeposito
        recepcion = MovimientoDeposito.objects.get(deposito=deposito, tipo='RECEPCION')
        self.assertIsNone(recepcion.cuenta_bancaria_id)
        self.assertTrue(self._poliza(recepcion, 'DEPOSITO_GARANTIA').filter(cuenta=self.contable_cobro))
        devolucion = MovimientoDeposito.objects.create(
            deposito=deposito, tipo='DEVOLUCION', monto=Decimal('1000.00'), metodo='TRANSFERENCIA')
        self.assertEqual(devolucion.cuenta_bancaria, pagadora)
        self.assertTrue(self._poliza(devolucion, 'DEPOSITO_GARANTIA').filter(cuenta=self.contable_pago, haber=Decimal('1000.00')))


class ConciliacionReapuntaTest(PagadoraBase):
    """El estado de cuenta manda: una compra asignada a la pagadora que se pagó desde la de cobro
    (la tarjeta aún no llega) se reapunta sola al conciliar."""

    def setUp(self):
        super().setUp()
        self.pagadora = self._pagadora()
        self.compra = self._compra()  # default: la pagadora
        self.estado = self._estado(self.cobro)
        self.cargo = MovimientoEstadoCuenta.objects.create(
            estado_cuenta=self.estado, fecha=date(2026, 11, 6), descripcion='PAGO HIELERA', cargo=Decimal('116.00'))

    def test_el_cargo_en_la_cuenta_de_cobro_reapunta_la_linea_y_se_liga(self):
        poliza = Poliza.objects.get(origen='COMPRA', object_id=self.compra.pk)
        folio, total = poliza.folio, poliza.movimientos.count()
        emparejar_y_asentar(self.estado)

        self.compra.refresh_from_db()
        self.cargo.refresh_from_db()
        self.assertEqual(self.compra.cuenta_pago, self.cobro)
        self.assertIsNotNone(self.cargo.movimiento_contable_id)
        self.assertEqual(self.cargo.movimiento_contable.cuenta, self.contable_cobro)
        poliza.refresh_from_db()
        self.assertEqual((poliza.folio, poliza.estado, poliza.movimientos.count()), (folio, 'APLICADA', total))
        self.assertEqual(poliza.total_debe, poliza.total_haber)
        self.assertEqual(Poliza.objects.filter(origen='COMPRA', object_id=self.compra.pk).count(), 1)

    def _sin_descuadre(self, estado, saldo_final):
        EstadoCuentaBancario.objects.filter(pk=estado.pk).update(saldo_final_estado=saldo_final)
        r = analizar_conciliacion(EstadoCuentaBancario.objects.get(pk=estado.pk))
        self.assertEqual(r['diferencia_arrastrada'], Decimal('0.00'))
        self.assertEqual(r['saldo_segun_banco'] - r['saldo_segun_libros'], Decimal('0.00'))
        self.assertFalse(r['detalle_banco_sin_asiento'])
        self.assertFalse(r['detalle_libros_sin_banco'])

    def test_ambas_conciliaciones_quedan_sin_descuadre(self):
        emparejar_y_asentar(self.estado)
        self._sin_descuadre(self.estado, Decimal('-116.00'))
        self._sin_descuadre(self._estado(self.pagadora), Decimal('0.00'))
        self.assertEqual(self.pagadora.saldo_a_fecha(date(2026, 11, 30)), Decimal('0.00'))
        self.assertEqual(self.cobro.saldo_a_fecha(date(2026, 11, 30)), Decimal('-116.00'))

    def test_si_la_linea_ya_esta_emparejada_en_otro_estado_se_rechaza(self):
        otro = self._estado(self.pagadora)
        linea = self._poliza(self.compra, 'COMPRA').get(cuenta=self.contable_pago)
        MovimientoEstadoCuenta.objects.create(
            estado_cuenta=otro, fecha=date(2026, 11, 6), descripcion='CARGO', cargo=Decimal('116.00'),
            movimiento_contable=linea)
        with self.assertRaises(ReapunteRechazado):
            reapuntar_banco_de_compra(self.compra, self.cobro)
        self.compra.refresh_from_db()
        self.assertEqual(self.compra.cuenta_pago, self.pagadora)

    def test_periodo_cerrado_se_rechaza(self):
        SaldoApertura.objects.create(
            cuenta_bancaria=self.pagadora, fecha_corte=date(2026, 12, 31), saldo_certificado=Decimal('0.00'),
            certificado_por=self._usuario(), aplicado=True)
        with self.assertRaises(ReapunteRechazado):
            reapuntar_banco_de_compra(self.compra, self.cobro)

    def _usuario(self):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.create_user('contador_ficticio', password='x')  # noqa: S106

    def test_reapuntar_a_la_misma_cuenta_no_hace_nada(self):
        self.assertFalse(reapuntar_banco_de_compra(self.compra, self.pagadora))


class ConfiguracionBancoPrincipalTest(PagadoraBase):
    def test_banco_principal_debe_ser_una_cuenta_bancaria_activa_y_no_solo_egresos(self):
        self._pagadora()
        config = ConfiguracionContable.objects.get(operacion='BANCO_PRINCIPAL')
        config.cuenta = self.contable_cobro
        config.full_clean()  # cobro: válida
        config.cuenta = self.contable_pago
        with self.assertRaises(ValidationError):
            config.full_clean()
        config.cuenta = CuentaContable.objects.create(
            codigo_sat='102.02.97', nombre='Sin banco', tipo='ACTIVO', naturaleza='D', nivel=4)
        with self.assertRaises(ValidationError):
            config.full_clean()
