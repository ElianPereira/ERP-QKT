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

    def test_tarjeta_usa_precio_del_producto(self):
        producto = Producto.objects.create(nombre='Test', precio_venta_fijo=Decimal('500.00'))
        seccion = SeccionCatalogo.objects.create(numero='01', slug='test2', titulo='Test')
        tarjeta = TarjetaCatalogo.objects.create(seccion=seccion, producto=producto)
        self.assertEqual(tarjeta.get_precio(), Decimal('500.00'))

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
