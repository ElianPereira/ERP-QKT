"""
Tests de ProductoAdminForm: captura de "Precio de venta fijo" con IVA incluido.
=================================================================================
`Producto.precio_venta_fijo` (el campo del modelo, usado por `sugerencia_precio()`,
`ItemCotizacion.precio_unitario` y `Cotizacion.calcular_totales()`) sigue guardando
la base SIN IVA — lo que cambia es que quien captura en el admin escribe el precio
final CON IVA y el form hace la conversión.

Ejecutar: python manage.py test comercial.test_producto_admin_precio --verbosity=2
"""
from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.test import Client, TestCase
from django.urls import reverse

from comercial.admin import ProductoAdminForm
from comercial.models import Producto
from core_erp import impuestos


class ProductoAdminFormTest(TestCase):

    def test_convierte_precio_con_iva_a_base_sin_iva(self):
        form = ProductoAdminForm(data={
            'nombre': 'Paquete Esencial QKT',
            'descripcion': '',
            'margen_ganancia': '0.30',
            'precio_venta_fijo': '3999.99',
            'capacidad_base_hospedaje': '4',
            'orden_cotizador': '0',
            'factor_personas': '1',
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['precio_venta_fijo'], impuestos.sin_iva_preciso(Decimal('3999.99')))
        obj = form.save()
        self.assertEqual(obj.precio_venta_fijo, Decimal('3448.2672'))
        # Round-trip: lo que el cliente paga coincide exacto con lo capturado
        # (4 decimales de precisión, no 2 — ver Memoria "me lo redondea a .99").
        self.assertEqual(impuestos.con_iva(obj.precio_venta_fijo), Decimal('3999.99'))

    def test_vacio_no_fija_precio(self):
        form = ProductoAdminForm(data={
            'nombre': 'Extra sin precio fijo',
            'descripcion': '',
            'margen_ganancia': '0.30',
            'precio_venta_fijo': '',
            'capacidad_base_hospedaje': '4',
            'orden_cotizador': '0',
            'factor_personas': '1',
        })
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save()
        self.assertIsNone(obj.precio_venta_fijo)

    def test_editar_producto_existente_muestra_precio_con_iva_como_inicial(self):
        producto = Producto.objects.create(
            nombre='Paquete Pasadía QKT', precio_venta_fijo=Decimal('1724.14'),
        )
        form = ProductoAdminForm(instance=producto)
        self.assertEqual(form.initial['precio_venta_fijo'], Decimal('2000.00'))

    def test_cero_es_rechazado_para_no_confundir_con_placeholder(self):
        form = ProductoAdminForm(data={
            'nombre': 'Con cero',
            'descripcion': '',
            'margen_ganancia': '0.30',
            'precio_venta_fijo': '0',
            'capacidad_base_hospedaje': '4',
            'orden_cotizador': '0',
            'factor_personas': '1',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('precio_venta_fijo', form.errors)


class ProductoAdminVistaTest(TestCase):
    """Round-trip real contra la vista de admin (no solo el form aislado)."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            username='ventas_precio', password='x', is_staff=True,
        )
        self.staff.user_permissions.add(
            *Permission.objects.filter(content_type__app_label='comercial')
        )
        self.client.force_login(self.staff)

    def test_alta_por_admin_guarda_base_sin_iva(self):
        url = reverse('admin:comercial_producto_add')
        response = self.client.post(url, data={
            'nombre': 'Paquete Premium QKT',
            'descripcion': '',
            'margen_ganancia': '0.30',
            'precio_venta_fijo': '3000.00',
            'capacidad_base_hospedaje': '4',
            'orden_cotizador': '0',
            'factor_personas': '1',
            'componentes-TOTAL_FORMS': '0', 'componentes-INITIAL_FORMS': '0',
            'componentes-MIN_NUM_FORMS': '0', 'componentes-MAX_NUM_FORMS': '1000',
            'productos_incluidos-TOTAL_FORMS': '0', 'productos_incluidos-INITIAL_FORMS': '0',
            'productos_incluidos-MIN_NUM_FORMS': '0', 'productos_incluidos-MAX_NUM_FORMS': '1000',
            '-1-TOTAL_FORMS': '0', '-1-INITIAL_FORMS': '0',
            '-1-MIN_NUM_FORMS': '0', '-1-MAX_NUM_FORMS': '1000',
        })
        self.assertEqual(response.status_code, 302, response.context['adminform'].form.errors if response.status_code == 200 else '')
        producto = Producto.objects.get(nombre='Paquete Premium QKT')
        self.assertEqual(producto.precio_venta_fijo, Decimal('2586.2069'))
        self.assertEqual(impuestos.con_iva(producto.precio_venta_fijo), Decimal('3000.00'))
