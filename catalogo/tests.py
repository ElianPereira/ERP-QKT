"""
Tests de la app catalogo
========================
Ejecutar: python manage.py test catalogo --verbosity=2
"""
from decimal import Decimal
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.test import Client, TestCase

from comercial.models import Producto
from catalogo.models import (
    ConfiguracionCatalogo, SeccionCatalogo, TarjetaCatalogo, DescuentoTarjeta,
    PaqueteCatalogo,
)


class CatalogoModelTests(TestCase):
    def test_config_es_singleton(self):
        c1 = ConfiguracionCatalogo.cargar()
        c2 = ConfiguracionCatalogo.cargar()
        self.assertEqual(c1.pk, c2.pk)
        self.assertEqual(ConfiguracionCatalogo.objects.count(), 1)

    def test_tarjeta_sin_producto_no_muestra_precio(self):
        seccion = SeccionCatalogo.objects.create(numero='01', slug='test', titulo='Test')
        tarjeta = TarjetaCatalogo.objects.create(seccion=seccion, titulo='Sin producto')
        self.assertIsNone(tarjeta.get_precio())

    def test_tarjeta_usa_precio_del_producto_con_iva_incluido(self):
        """El precio impreso incluye IVA (16%), igual que en el cotizador."""
        producto = Producto.objects.create(nombre='Test', precio_venta_fijo=Decimal('500.00'))
        seccion = SeccionCatalogo.objects.create(numero='01', slug='test2', titulo='Test')
        tarjeta = TarjetaCatalogo.objects.create(seccion=seccion, producto=producto)
        self.assertEqual(tarjeta.get_precio(), Decimal('580.00'))  # 500 * 1.16

    def test_tarjeta_get_titulo_usa_producto_si_no_hay_titulo_propio(self):
        producto = Producto.objects.create(nombre='Silla Tiffany', precio_venta_fijo=Decimal('600.00'))
        seccion = SeccionCatalogo.objects.create(numero='01', slug='test-titulo', titulo='Test')
        tarjeta = TarjetaCatalogo.objects.create(seccion=seccion, producto=producto)
        self.assertEqual(tarjeta.get_titulo(), 'Silla Tiffany')

    def test_descuento_invalido_si_no_es_menor(self):
        producto = Producto.objects.create(nombre='Test2', precio_venta_fijo=Decimal('100.00'))
        seccion = SeccionCatalogo.objects.create(numero='01', slug='test3', titulo='Test')
        tarjeta = TarjetaCatalogo.objects.create(seccion=seccion, producto=producto)
        descuento = DescuentoTarjeta(
            tarjeta=tarjeta, precio_regular=Decimal('100.00'),
            precio_descuento=Decimal('150.00'), nota_vigencia='x',
        )
        with self.assertRaises(ValidationError):
            descuento.full_clean()

    def test_descuento_fuera_de_vigencia_no_aplica(self):
        producto = Producto.objects.create(nombre='Test3', precio_venta_fijo=Decimal('100.00'))
        seccion = SeccionCatalogo.objects.create(numero='01', slug='test4', titulo='Test')
        tarjeta = TarjetaCatalogo.objects.create(seccion=seccion, producto=producto)
        descuento = DescuentoTarjeta.objects.create(
            tarjeta=tarjeta, precio_regular=Decimal('100.00'),
            precio_descuento=Decimal('80.00'), nota_vigencia='x',
            vigente_hasta=date.today() - timedelta(days=1),
        )
        self.assertFalse(descuento.esta_vigente())

    def test_descuento_dentro_de_vigencia_aplica(self):
        producto = Producto.objects.create(nombre='Test4', precio_venta_fijo=Decimal('100.00'))
        seccion = SeccionCatalogo.objects.create(numero='01', slug='test5', titulo='Test')
        tarjeta = TarjetaCatalogo.objects.create(seccion=seccion, producto=producto)
        descuento = DescuentoTarjeta.objects.create(
            tarjeta=tarjeta, precio_regular=Decimal('100.00'),
            precio_descuento=Decimal('80.00'), nota_vigencia='x',
            vigente_desde=date.today() - timedelta(days=1),
            vigente_hasta=date.today() + timedelta(days=1),
        )
        self.assertTrue(descuento.esta_vigente())

    def test_descuento_inactivo_no_vigente_aunque_fecha_ok(self):
        producto = Producto.objects.create(nombre='Test5', precio_venta_fijo=Decimal('100.00'))
        seccion = SeccionCatalogo.objects.create(numero='01', slug='test6', titulo='Test')
        tarjeta = TarjetaCatalogo.objects.create(seccion=seccion, producto=producto)
        descuento = DescuentoTarjeta.objects.create(
            tarjeta=tarjeta, precio_regular=Decimal('100.00'),
            precio_descuento=Decimal('80.00'), nota_vigencia='x', activo=False,
        )
        self.assertFalse(descuento.esta_vigente())

    def test_paquete_ligado_a_producto_usa_precio_con_iva(self):
        """Paquete propio de la Quinta: precio en vivo desde el Producto, con IVA."""
        producto = Producto.objects.create(
            nombre='Paquete Taquiza 50', es_paquete=True, precio_venta_fijo=Decimal('1000.00'),
        )
        paquete = PaqueteCatalogo.objects.create(nombre='Taquiza 50', producto=producto)
        self.assertEqual(paquete.get_precio(), Decimal('1160.00'))  # 1000 * 1.16

    def test_paquete_sin_producto_usa_precio_manual_sin_recalcular(self):
        """Paquete de proveedor externo: precio manual, se muestra tal cual (sin IVA extra)."""
        paquete = PaqueteCatalogo.objects.create(
            nombre='Tiffany (David Vera)', precio_venta_fijo=Decimal('4400.00'),
        )
        self.assertEqual(paquete.get_precio(), Decimal('4400.00'))

    def test_paquete_producto_gana_sobre_precio_manual(self):
        """Si ambos están definidos, el Producto ligado es la fuente de verdad."""
        producto = Producto.objects.create(
            nombre='Paquete Gourmet 50', es_paquete=True, precio_venta_fijo=Decimal('2000.00'),
        )
        paquete = PaqueteCatalogo.objects.create(
            nombre='Gourmet 50', producto=producto, precio_venta_fijo=Decimal('999.00'),
        )
        self.assertEqual(paquete.get_precio(), Decimal('2320.00'))  # 2000 * 1.16, no 999

    def test_paquete_sin_precio_ni_producto_es_none(self):
        paquete = PaqueteCatalogo.objects.create(nombre='Sin definir')
        self.assertIsNone(paquete.get_precio())


class CatalogoViewTests(TestCase):
    def test_descarga_pdf_responde_200(self):
        client = Client()
        response = client.get('/catalogo.pdf')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')

    def test_descarga_pdf_usa_cache_en_segunda_llamada(self):
        """La segunda petición sin cambios debe servir el mismo PDF cacheado."""
        client = Client()
        r1 = client.get('/catalogo.pdf')
        r2 = client.get('/catalogo.pdf')
        self.assertEqual(r1.content, r2.content)
