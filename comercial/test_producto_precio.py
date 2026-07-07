"""
Tests de precio_venta_fijo en Producto
=======================================
Ejecutar: python manage.py test comercial.test_producto_precio --verbosity=2
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from comercial.models import (
    ComponenteProducto, Insumo, Producto, RecetaSubProducto, SubProducto,
)


class SugerenciaPrecioTests(TestCase):

    def test_precio_fijo_tiene_prioridad(self):
        p = Producto.objects.create(
            nombre='Silla Tiffany', margen_ganancia=Decimal('0.30'),
            precio_venta_fijo=Decimal('600.00'),
        )
        self.assertEqual(p.sugerencia_precio(), Decimal('600.00'))

    def test_sin_precio_fijo_usa_costo_por_margen(self):
        insumo = Insumo.objects.create(
            nombre='Tela', unidad_medida='m', costo_unitario=Decimal('10.00'),
        )
        sub = SubProducto.objects.create(nombre='Tapizado')
        RecetaSubProducto.objects.create(subproducto=sub, insumo=insumo, cantidad=Decimal('2'))
        p = Producto.objects.create(nombre='Silla X', margen_ganancia=Decimal('0.50'))
        ComponenteProducto.objects.create(producto=p, subproducto=sub, cantidad=Decimal('1'))
        # costo = 10*2 = 20; precio = 20*1.5 = 30.00
        self.assertEqual(p.sugerencia_precio(), Decimal('30.00'))

    def test_precio_fijo_sin_costo_no_bloquea(self):
        # Caso Tiffany/Crossback/Vintage: sin receta (costo=0), precio fijo > 0 → válido
        p = Producto(nombre='Vintage', precio_venta_fijo=Decimal('1150.00'))
        p.full_clean()  # no debe lanzar ValidationError
        p.save()
        self.assertEqual(p.sugerencia_precio(), Decimal('1150.00'))

    def test_precio_fijo_menor_a_costo_bloquea_guardado(self):
        insumo = Insumo.objects.create(
            nombre='Madera', unidad_medida='pza', costo_unitario=Decimal('500.00'),
        )
        sub = SubProducto.objects.create(nombre='Estructura')
        RecetaSubProducto.objects.create(subproducto=sub, insumo=insumo, cantidad=Decimal('1'))
        p = Producto.objects.create(nombre='Mesa cara', margen_ganancia=Decimal('0.30'))
        ComponenteProducto.objects.create(producto=p, subproducto=sub, cantidad=Decimal('1'))
        p.precio_venta_fijo = Decimal('100.00')  # costo real = 500
        with self.assertRaises(ValidationError):
            p.full_clean()

    def test_redondeo_half_up(self):
        p = Producto.objects.create(nombre='Redondeo', precio_venta_fijo=Decimal('599.995'))
        self.assertEqual(p.sugerencia_precio(), Decimal('600.00'))

    def test_precio_fijo_en_cero_no_sobreescribe(self):
        """Guardrail: precio_venta_fijo=0.00 (placeholder pendiente) no debe
        exponerse como precio real; cae al cálculo por costo (también 0 sin receta)."""
        p = Producto.objects.create(
            nombre='Pendiente de precio', margen_ganancia=Decimal('0.30'),
            precio_venta_fijo=Decimal('0.00'),
        )
        self.assertEqual(p.sugerencia_precio(), Decimal('0.00'))
