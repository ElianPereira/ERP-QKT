"""
Listas del admin tras el sistema de diseño (Issue #322, fase 2): todas
cargan, usan la barra nueva y sus filtros de fecha funcionan también sobre
DateTimeField.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, ItemCotizacion
from comunicacion.models import ComunicacionCliente
from core_erp.admin_filtros import con_titulo, filtro_periodo
from core_erp.test_utils import login_superuser_con_totp

APPS_PROPIAS = ('comercial', 'contabilidad', 'facturacion', 'nomina', 'reportes', 'operaciones', 'comunicacion')


class TodasLasListasCarganTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('dir', 'dir@example.com', 'x-segura-123')
        login_superuser_con_totp(self.client, self.admin)

    def test_cada_lista_responde_200_con_la_barra_nueva(self):
        for modelo, model_admin in admin.site._registry.items():
            if type(model_admin).__module__.split('.')[0] not in APPS_PROPIAS:
                continue
            url = reverse(f'admin:{modelo._meta.app_label}_{modelo._meta.model_name}_changelist')
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertIn('qkt-barra', response.content.decode())

    def test_filtro_de_periodo_en_datetime_compara_la_fecha_local(self):
        cliente = Cliente.objects.create(nombre='Cliente', tipo_persona='FISICA', telefono='9991234567')
        cot = Cotizacion.objects.create(cliente=cliente, nombre_evento='Evento',
                                        fecha_evento=timezone.localdate() + timedelta(days=10),
                                        incluye_refrescos=False)
        ItemCotizacion.objects.create(cotizacion=cot, descripcion='S', cantidad=1, precio_unitario=Decimal('10'))
        reciente = ComunicacionCliente.objects.create(
            cotizacion=cot, canal='EMAIL', tipo='OTRO', estado='ENVIADO', destinatario='a@example.com',
            clave_idempotencia='test-reciente')
        vieja = ComunicacionCliente.objects.create(
            cotizacion=cot, canal='EMAIL', tipo='OTRO', estado='ENVIADO', destinatario='b@example.com',
            clave_idempotencia='test-vieja')
        ComunicacionCliente.objects.filter(pk=vieja.pk).update(fecha_envio=timezone.now() - timedelta(days=90))

        filtro = filtro_periodo('fecha_envio', 'Fecha')
        spec = filtro(None, {'fecha_envio_periodo': ['7d']}, ComunicacionCliente, None)
        ids = set(spec.queryset(None, ComunicacionCliente.objects.all()).values_list('pk', flat=True))
        self.assertIn(reciente.pk, ids)
        self.assertNotIn(vieja.pk, ids)


class ConTituloTest(TestCase):
    def test_cambia_el_titulo_y_conserva_el_tipo_de_filtro(self):
        from django.contrib.admin.filters import BooleanFieldListFilter

        campo = Cotizacion._meta.get_field('identificacion_revisada')
        spec = con_titulo('INE revisada')(campo, None, {}, Cotizacion, None, 'identificacion_revisada')
        self.assertIsInstance(spec, BooleanFieldListFilter)
        self.assertEqual(spec.title, 'INE revisada')
