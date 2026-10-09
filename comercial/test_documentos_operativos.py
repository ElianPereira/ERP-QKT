"""Lista de compras y ficha de producto con el sistema de documentos (Issue #373, fase 3)."""
import io
from datetime import timedelta
from decimal import Decimal

import pdfplumber
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, Producto
from core_erp.test_utils import login_superuser_con_totp


def _texto(respuesta):
    with pdfplumber.open(io.BytesIO(respuesta.content)) as pdf:
        return '\n'.join(p.extract_text() or '' for p in pdf.pages)


class DocumentosOperativosTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.usuario = User.objects.create_superuser('direccion', 'd@x.mx', 'clave-de-prueba-123')
        cliente = Cliente.objects.create(nombre='Ana Pech Canul', telefono='5555550001')
        cls.cot = Cotizacion.objects.create(
            cliente=cliente, nombre_evento='Boda Pech', tipo_servicio='EVENTO', num_personas=80,
            fecha_evento=timezone.localdate() + timedelta(days=30), incluye_refrescos=True,
        )

    def setUp(self):
        login_superuser_con_totp(self.client, self.usuario)

    def test_lista_de_compras(self):
        r = self.client.get(reverse('cotizacion_lista_compras', args=[self.cot.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertIn(f'QKT_ListaCompras_COT-{self.cot.pk:03d}.pdf', r['Content-Disposition'])
        texto = _texto(r)
        self.assertIn('Lista de compras de barra', texto)
        self.assertIn('Boda Pech', texto)
        self.assertIn('Hielo', texto)
        # Sin insumos capturados, cada artículo queda marcado como pendiente de proveedor.
        self.assertIn('Sin proveedor asignado', texto)
        self.assertIn('sin proveedor: configúr', texto)

    def test_ficha_de_producto_con_precio_iva_incluido(self):
        producto = Producto.objects.create(
            nombre='Paquete Fiesta', precio_venta_fijo=Decimal('1000.00'),
            descripcion_corta='Mobiliario, mantelería y meseros.',
            cantidad_por_persona=True, factor_personas=10,
        )
        r = self.client.get(reverse('producto_ficha_pdf', args=[producto.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertIn('QKT_Ficha_Paquete-Fiesta.pdf', r['Content-Disposition'])
        texto = _texto(r)
        self.assertIn('Paquete Fiesta', texto)
        self.assertIn('$1,160.00', texto)
        self.assertIn('POR CADA 10 PERSONAS', texto)
        self.assertIn('Mobiliario, mantelería y meseros.', texto)
        self.assertIn('999 169 9191', texto)
        self.assertNotIn('999 999 9999', texto)
