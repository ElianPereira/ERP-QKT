"""Catálogo de categorías de gasto (Issue #390): una categoría = una cuenta,
comisiones como gasto financiero, staff externo como costo, mobiliario como
activo y no deducibles en la subcuenta de su grupo."""
import importlib
import uuid
from datetime import date
from decimal import Decimal

from django.apps import apps
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from comercial.models import Compra, Gasto, Proveedor

from .categorias_gasto import CATALOGO, NO_DEDUCIBLE_POR_GRUPO
from .models import ConfiguracionContable, CuentaContable, MovimientoContable, Poliza, UnidadNegocio
from .services_compras import reclasificar_compra, reclasificar_historico
from .signals import get_cuenta_por_categoria


def cuenta(codigo):
    return CuentaContable.objects.get(codigo_sat=codigo)


class CatalogoSembradoTest(TestCase):
    """Lo que deja la migración 0038 en una base nueva."""

    def test_cada_categoria_tiene_cuenta_de_detalle(self):
        for clave, _, _, operacion in CATALOGO:
            with self.subTest(categoria=clave):
                c = ConfiguracionContable.obtener_cuenta(operacion)
                self.assertIsNotNone(c, f"{operacion} sin cuenta")
                self.assertTrue(c.permite_movimientos)
                self.assertTrue(c.activa)

    def test_ninguna_categoria_comparte_cuenta_salvo_las_bebidas(self):
        por_cuenta = {}
        for clave, _, _, operacion in CATALOGO:
            por_cuenta.setdefault(ConfiguracionContable.obtener_cuenta(operacion).codigo_sat, []).append(clave)
        compartidas = {codigo: claves for codigo, claves in por_cuenta.items() if len(claves) > 1}
        self.assertEqual(compartidas, {'501.02': ['BEBIDAS_SIN_ALCOHOL', 'BEBIDAS_CON_ALCOHOL']})

    def test_solo_mantenimiento_va_a_mantenimiento(self):
        en_mantenimiento = [clave for clave, _, _, _ in CATALOGO
                            if get_cuenta_por_categoria(clave).codigo_sat == '601.02.05']
        self.assertEqual(en_mantenimiento, ['MANTENIMIENTO'])

    def test_decisiones_del_contador(self):
        esperado = {
            'COMISIONES_BANCARIAS': '602.03', 'NOMINA_EXT': '501.03', 'MOBILIARIO_EQ': '115.03',
            'OTRO': '601.06', 'SIN_CLASIFICAR': '601.99', 'SOFTWARE': '601.03.05',
        }
        for clave, codigo in esperado.items():
            with self.subTest(categoria=clave):
                self.assertEqual(get_cuenta_por_categoria(clave).codigo_sat, codigo)
        self.assertEqual(cuenta('602.03').padre.codigo_sat, '602')
        self.assertEqual(cuenta('602.03').tipo, 'GASTO')

    def test_comisiones_del_estado_de_cuenta_son_gasto_financiero(self):
        self.assertEqual(ConfiguracionContable.obtener_cuenta('GASTO_BANCARIOS').codigo_sat, '602.03')

    def test_no_deducibles_por_grupo(self):
        for grupo, operacion in NO_DEDUCIBLE_POR_GRUPO.items():
            with self.subTest(grupo=grupo):
                self.assertTrue(ConfiguracionContable.obtener_cuenta(operacion).permite_movimientos)
        self.assertFalse(cuenta('601.05').permite_movimientos)

    def test_categoria_retirada_va_a_por_clasificar(self):
        self.assertEqual(get_cuenta_por_categoria('SERVICIOS_ADMON').codigo_sat, '601.99')
        self.assertEqual(get_cuenta_por_categoria(None).codigo_sat, '601.99')


class CompraBase(TestCase):
    def setUp(self):
        self.unidad = UnidadNegocio.objects.get(clave='QUINTA')
        self.proveedor = Proveedor.objects.create(nombre='Proveedor Catálogo', rfc='CAT010101AAA')

    def _compra(self, categoria, deducible=True, proveedor=None, fecha=date(2026, 8, 5)):
        proveedor = proveedor or self.proveedor
        return Compra.objects.create(
            proveedor=proveedor, proveedor_nombre=proveedor.nombre, categoria=categoria,
            subtotal=Decimal('100.00'), iva=Decimal('16.00'), total=Decimal('116.00'),
            uuid=str(uuid.uuid4()) if deducible else None,
            fecha_emision=fecha, unidad_negocio=self.unidad,
        )

    def _cuenta_gasto(self, compra):
        poliza = Poliza.objects.get(
            content_type=ContentType.objects.get_for_model(compra), object_id=compra.pk, origen='COMPRA')
        iva = ConfiguracionContable.obtener_cuenta('IVA_ACREDITABLE')
        return poliza.movimientos.filter(debe__gt=0).exclude(cuenta=iva).get().cuenta.codigo_sat


class NoDeduciblesTest(CompraBase):
    def test_sin_cfdi_va_a_la_subcuenta_no_deducible_de_su_grupo(self):
        esperado = {
            'INSUMOS': '501.90', 'NOMINA_EXT': '501.90', 'MANTENIMIENTO': '601.05.01',
            'SOFTWARE': '601.05.02', 'PUBLICIDAD': '601.05.03', 'COMISIONES_BANCARIAS': '602.90',
            'OTRO': '601.05.04',
        }
        for categoria, codigo in esperado.items():
            with self.subTest(categoria=categoria):
                self.assertEqual(self._cuenta_gasto(self._compra(categoria, deducible=False)), codigo)

    def test_activo_y_por_clasificar_no_cambian_sin_cfdi(self):
        self.assertEqual(self._cuenta_gasto(self._compra('MOBILIARIO_EQ', deducible=False)), '115.03')
        self.assertEqual(self._cuenta_gasto(self._compra('SIN_CLASIFICAR', deducible=False)), '601.99')

    def test_con_cfdi_va_a_su_cuenta(self):
        self.assertEqual(self._cuenta_gasto(self._compra('MANTENIMIENTO')), '601.02.05')

    def test_cuenta_exacta_del_proveedor_sin_cfdi_usa_el_grupo_de_su_categoria(self):
        cfe = Proveedor.objects.create(nombre='CFE Catálogo', categoria_gasto='LUZ', cuenta_gasto=cuenta('601.02.01'))
        self.assertEqual(self._cuenta_gasto(self._compra('SIN_CLASIFICAR', proveedor=cfe)), '601.02.01')
        self.assertEqual(self._cuenta_gasto(self._compra('SIN_CLASIFICAR', deducible=False, proveedor=cfe)),
                         '601.05.01')

    def test_dejar_de_ser_deducible_reclasifica(self):
        compra = self._compra('PUBLICIDAD')
        compra.es_deducible = False
        compra.save(update_fields=['es_deducible'])
        self.assertEqual(reclasificar_compra(compra), 1)
        self.assertEqual(self._cuenta_gasto(compra), '601.05.03')


class ReclasificarHistoricoTest(CompraBase):
    def setUp(self):
        super().setUp()
        self.usuario = User.objects.create_user('contador_catalogo')
        # Como quedaron antes del catálogo: limpieza asentada en mantenimiento.
        self.compra = self._compra('LIMPIEZA')
        self.vieja = self._compra('LIMPIEZA', fecha=date(2026, 6, 5))
        MovimientoContable.objects.filter(cuenta__codigo_sat='601.02.07').update(cuenta=cuenta('601.02.05'))
        # Comisión del estado de cuenta asentada en la cuenta retirada.
        self.poliza = Poliza.objects.create(
            tipo='E', folio=Poliza.siguiente_folio('E', date(2026, 8, 3)), fecha=date(2026, 8, 3),
            concepto='Comisión bancaria', unidad_negocio=self.unidad, estado='APLICADA', origen='MANUAL',
            created_by=self.usuario,
        )
        self.comision = MovimientoContable.objects.create(
            poliza=self.poliza, cuenta=cuenta('601.03.04'), debe=Decimal('15.00'), haber=Decimal('0.00'))
        MovimientoContable.objects.create(
            poliza=self.poliza, cuenta=cuenta('102.02.01'), debe=Decimal('0.00'), haber=Decimal('15.00'))

    def test_simular_no_escribe(self):
        resultado = reclasificar_historico(desde=date(2026, 7, 1))
        self.assertEqual([c.pk for c, _ in resultado['compras']], [self.compra.pk])
        self.assertEqual([(m.pk, n.codigo_sat) for m, n in resultado['asientos']], [(self.comision.pk, '602.03')])
        self.assertEqual(self._cuenta_gasto(self.compra), '601.02.05')
        self.comision.refresh_from_db()
        self.assertEqual(self.comision.cuenta.codigo_sat, '601.03.04')

    def test_aplicar_respeta_desde_y_conserva_importes(self):
        reclasificar_historico(desde=date(2026, 7, 1), aplicar=True)
        self.assertEqual(self._cuenta_gasto(self.compra), '601.02.07')
        self.assertEqual(self._cuenta_gasto(self.vieja), '601.02.05')
        self.comision.refresh_from_db()
        self.assertEqual((self.comision.cuenta.codigo_sat, self.comision.debe), ('602.03', Decimal('15.00')))
        self.assertEqual(reclasificar_historico(desde=date(2026, 7, 1)), {'compras': [], 'asientos': []})


class MigracionCategoriasRetiradasTest(TestCase):
    """comercial.0115: SERVICIOS_ADMON/SERVICIO_EXTERNO dejan de existir."""

    def test_bancos_a_comisiones_y_el_resto_sin_clasificar(self):
        unidad = UnidadNegocio.objects.get(clave='QUINTA')
        openpay = Proveedor.objects.create(nombre='Openpay Cat', descuenta_de_depositos=True)
        otro = Proveedor.objects.create(nombre='Despacho Cat')
        Proveedor.objects.filter(pk__in=[openpay.pk, otro.pk]).update(categoria_gasto='SERVICIOS_ADMON')
        compras = {}
        for nombre, proveedor in (('openpay', openpay), ('otro', otro)):
            compras[nombre] = Compra.objects.create(
                proveedor=proveedor, proveedor_nombre=proveedor.nombre, subtotal=Decimal('10.00'),
                total=Decimal('10.00'), fecha_emision=date(2026, 8, 5), unidad_negocio=unidad)
            Compra.objects.filter(pk=compras[nombre].pk).update(categoria='SERVICIO_EXTERNO')
            Gasto.objects.create(compra=compras[nombre], descripcion='x', cantidad=1,
                                 precio_unitario=Decimal('10.00'), total_linea=Decimal('10.00'),
                                 categoria='SERVICIOS_ADMON')

        migracion = importlib.import_module('comercial.migrations.0115_catalogo_categorias_gasto')
        migracion.reasignar(apps, None)

        openpay.refresh_from_db()
        otro.refresh_from_db()
        self.assertEqual((openpay.categoria_gasto, otro.categoria_gasto), ('COMISIONES_BANCARIAS', ''))
        self.assertEqual(Compra.objects.get(pk=compras['openpay'].pk).categoria, 'COMISIONES_BANCARIAS')
        self.assertEqual(Compra.objects.get(pk=compras['otro'].pk).categoria, 'SIN_CLASIFICAR')
        self.assertEqual(Gasto.objects.get(compra=compras['openpay']).categoria, 'COMISIONES_BANCARIAS')
        self.assertEqual(Gasto.objects.get(compra=compras['otro']).categoria, 'SIN_CLASIFICAR')
