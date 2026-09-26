"""Compras clasificadas por proveedor: configuración, aprendizaje y póliza."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from comercial.models import Compra, Proveedor
from core_erp.test_utils import login_superuser_con_totp

from .models import ConfiguracionContable, CuentaBancaria, CuentaContable, Poliza, UnidadNegocio
from .services_compras import aplicar_clasificacion_proveedor, reclasificar_compra, recordar_en_proveedor
from .services_estados_cuenta import emparejar_y_asentar
from .test_reglas_banco import ReglasBancoBase


class ClasificacionBase(TestCase):
    def setUp(self):
        self.cuenta_banco = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.unidad = UnidadNegocio.objects.get(clave='QUINTA')
        CuentaBancaria.objects.filter(unidad_negocio=self.unidad).update(activa=False)
        self.cuenta_bancaria = CuentaBancaria.objects.create(
            nombre='BBVA Clasificación', banco='BBVA', clabe='012345678901234777',
            cuenta_contable=self.cuenta_banco, unidad_negocio=self.unidad,
        )
        self.generales = CuentaContable.objects.create(
            codigo_sat='699.81', nombre='Generales test', tipo='GASTO', naturaleza='D', nivel=3)
        self.insumos = CuentaContable.objects.create(
            codigo_sat='599.81', nombre='Insumos test', tipo='COSTO', naturaleza='D', nivel=3)
        self.luz = CuentaContable.objects.create(
            codigo_sat='699.82', nombre='Energía test', tipo='GASTO', naturaleza='D', nivel=3)
        self.iva = CuentaContable.objects.create(
            codigo_sat='199.81', nombre='IVA test', tipo='ACTIVO', naturaleza='D', nivel=3)
        for operacion, cuenta in [('GASTOS_GENERALES', self.generales), ('GASTO_INSUMOS', self.insumos),
                                  ('IVA_ACREDITABLE', self.iva)]:
            ConfiguracionContable.objects.update_or_create(
                operacion=operacion, defaults={'cuenta': cuenta, 'activa': True})
        self.proveedor = Proveedor.objects.create(nombre='Hielera Test', rfc='HIE010101AAA')

    def _compra(self, total='116.00', proveedor=None, **extra):
        return Compra.objects.create(
            proveedor=proveedor or self.proveedor, proveedor_nombre=(proveedor or self.proveedor).nombre,
            subtotal=Decimal('100.00'), iva=Decimal('16.00'), total=Decimal(total), uuid=None,
            fecha_emision=date(2026, 8, 5), unidad_negocio=self.unidad, **extra,
        )

    def _cuenta_gasto(self, compra):
        poliza = Poliza.objects.get(
            content_type=ContentType.objects.get_for_model(compra), object_id=compra.pk, origen='COMPRA')
        return poliza.movimientos.filter(debe__gt=0).exclude(cuenta=self.iva).get().cuenta


class ClasificacionPorProveedorTest(ClasificacionBase):
    def test_sin_configuracion_cae_en_generales(self):
        compra = self._compra()
        self.assertEqual(compra.categoria, 'SIN_CLASIFICAR')
        self.assertEqual(self._cuenta_gasto(compra), self.generales)

    def test_categoria_del_proveedor_entra_sola(self):
        self.proveedor.categoria_gasto = 'INSUMOS'
        self.proveedor.save()
        compra = self._compra()
        self.assertEqual(compra.categoria, 'INSUMOS')
        self.assertEqual(self._cuenta_gasto(compra), self.insumos)
        self.assertIn('Insumos para eventos', Poliza.objects.get(object_id=compra.pk, origen='COMPRA').concepto)

    def test_cuenta_exacta_del_proveedor_manda(self):
        cfe = Proveedor.objects.create(nombre='CFE Test', categoria_gasto='SERVICIOS_ADMON', cuenta_gasto=self.luz)
        self.assertEqual(self._cuenta_gasto(self._compra(proveedor=cfe)), self.luz)

    def test_clasificacion_distinta_a_la_del_proveedor_gana_a_su_cuenta(self):
        self.proveedor.categoria_gasto = 'OTRO'
        self.proveedor.cuenta_gasto = self.luz
        self.proveedor.save()
        compra = self._compra(categoria='INSUMOS')
        self.assertEqual(self._cuenta_gasto(compra), self.insumos)

    def test_bebidas_van_a_insumos(self):
        self.assertEqual(self._cuenta_gasto(self._compra(categoria='BEBIDAS_CON_ALCOHOL')), self.insumos)


class AprendizajeProveedorTest(ClasificacionBase):
    def test_clasificar_una_compra_reclasifica_poliza_y_ensena_al_proveedor(self):
        primera, segunda = self._compra(), self._compra(total='232.00')
        primera.categoria = 'INSUMOS'
        primera.save(update_fields=['categoria'])

        self.assertEqual(reclasificar_compra(primera), 1)
        self.assertEqual(self._cuenta_gasto(primera), self.insumos)
        poliza = Poliza.objects.get(object_id=primera.pk, origen='COMPRA')
        self.assertTrue(poliza.esta_cuadrada)
        self.assertEqual(poliza.estado, 'APLICADA')

        self.assertEqual(recordar_en_proveedor(primera), 1)
        self.proveedor.refresh_from_db()
        segunda.refresh_from_db()
        self.assertEqual(self.proveedor.categoria_gasto, 'INSUMOS')
        self.assertEqual(segunda.categoria, 'INSUMOS')
        self.assertEqual(self._cuenta_gasto(segunda), self.insumos)
        # La siguiente factura ya entra clasificada.
        self.assertEqual(self._compra(total='348.00').categoria, 'INSUMOS')

    def test_no_pisa_una_categoria_ya_configurada(self):
        self.proveedor.categoria_gasto = 'LIMPIEZA'
        self.proveedor.save()
        compra = self._compra(categoria='INSUMOS')
        self.assertEqual(recordar_en_proveedor(compra), 0)
        self.proveedor.refresh_from_db()
        self.assertEqual(self.proveedor.categoria_gasto, 'LIMPIEZA')

    def test_configurar_proveedor_no_toca_compras_clasificadas_distinto(self):
        propia = self._compra(categoria='LIMPIEZA')
        pendiente = self._compra(total='232.00')
        self.proveedor.categoria_gasto = 'INSUMOS'
        self.proveedor.save()
        self.assertEqual(aplicar_clasificacion_proveedor(self.proveedor), 1)
        propia.refresh_from_db()
        pendiente.refresh_from_db()
        self.assertEqual(propia.categoria, 'LIMPIEZA')
        self.assertEqual(pendiente.categoria, 'INSUMOS')

    def test_lineas_de_gasto_toman_la_categoria(self):
        compra = self._compra()
        compra.gastos.create(descripcion='Hielo', total_linea=Decimal('116.00'))
        compra.categoria = 'INSUMOS'
        compra.save(update_fields=['categoria'])
        reclasificar_compra(compra)
        self.assertEqual(compra.gastos.get().categoria, 'INSUMOS')


class ClasificacionAdminTest(ClasificacionBase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser('dir_clasif', 'd@x.mx', 'x')
        login_superuser_con_totp(self.client, self.admin)

    def test_cambiar_categoria_desde_la_lista_reclasifica(self):
        compra = self._compra()
        otra = self._compra(total='232.00')
        url = reverse('admin:comercial_compra_changelist')
        datos = {
            'form-TOTAL_FORMS': '2', 'form-INITIAL_FORMS': '2', '_save': 'Guardar',
            'form-0-id': str(otra.pk), 'form-0-categoria': 'SIN_CLASIFICAR', 'form-0-es_deducible': 'on',
            'form-1-id': str(compra.pk), 'form-1-categoria': 'INSUMOS', 'form-1-es_deducible': 'on',
        }
        respuesta = self.client.post(url, datos)
        self.assertEqual(respuesta.status_code, 302)
        compra.refresh_from_db()
        self.assertEqual(compra.categoria, 'INSUMOS')
        self.assertEqual(self._cuenta_gasto(compra), self.insumos)
        self.proveedor.refresh_from_db()
        self.assertEqual(self.proveedor.categoria_gasto, 'INSUMOS')


class ProvisionalHeredaCuentaTest(ReglasBancoBase):
    def test_compra_sin_clasificar_hereda_la_cuenta_de_la_palabra_clave(self):
        insumos = CuentaContable.objects.create(
            codigo_sat='599.82', nombre='Insumos test', tipo='COSTO', naturaleza='D', nivel=3)
        ConfiguracionContable.objects.update_or_create(
            operacion='GASTO_INSUMOS', defaults={'cuenta': insumos, 'activa': True})
        mov = self._mov('PAGO CUENTA DE TERCERO 0089101760 BNET 0467646666 INS bolis', cargo='359.60', dia=8)
        emparejar_y_asentar(self.estado, usuario=self.usuario)

        compra = Compra.objects.create(
            proveedor_nombre='Bolis Test', subtotal=Decimal('359.60'), total=Decimal('359.60'),
            fecha_emision=date(2026, 8, 7), unidad_negocio=self.cuenta_bancaria.unidad_negocio,
        )
        emparejar_y_asentar(self.estado, usuario=self.usuario)
        mov.refresh_from_db()
        poliza = mov.movimiento_contable.poliza
        self.assertEqual(poliza.object_id, compra.pk)
        self.assertTrue(poliza.movimientos.filter(cuenta=insumos, debe=Decimal('359.60')).exists())
