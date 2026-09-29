"""Borrado de pólizas por Dirección, con confirmación, motivo y bitácora."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from core_erp.test_utils import login_superuser_con_totp

from .models import CuentaContable, MovimientoContable, Poliza, PolizaEliminada, UnidadNegocio
from .services_polizas import eliminar_polizas

URL = reverse('admin:contabilidad_poliza_changelist')


class EliminarPolizasTest(TestCase):
    def setUp(self):
        self.direccion = User.objects.create_superuser('direccion', password='x')
        self.staff = User.objects.create_user('contador', password='x', is_staff=True)
        self.poliza = Poliza.objects.create(
            tipo='E', folio=3, fecha=date(2026, 6, 15), concepto='Pago nómina',
            unidad_negocio=UnidadNegocio.objects.get(clave='QUINTA'),
            estado='APLICADA', origen='NOMINA', created_by=self.direccion,
        )
        gasto = CuentaContable.objects.filter(tipo='GASTO', permite_movimientos=True).first()
        banco = CuentaContable.objects.get(codigo_sat='102.02.01')
        MovimientoContable.objects.create(poliza=self.poliza, cuenta=gasto, debe=Decimal('1369.18'))
        MovimientoContable.objects.create(poliza=self.poliza, cuenta=banco, haber=Decimal('1369.18'))

    def _post(self, **extra):
        return self.client.post(URL, {
            'action': 'eliminar_polizas', '_selected_action': [self.poliza.pk], **extra,
        })

    def test_sin_confirmar_no_borra_y_pide_motivo(self):
        login_superuser_con_totp(self.client, self.direccion)
        respuesta = self._post()
        self.assertContains(respuesta, 'name="motivo"')
        self.assertTrue(Poliza.objects.filter(pk=self.poliza.pk).exists())

    def test_direccion_borra_poliza_y_movimientos_con_bitacora(self):
        login_superuser_con_totp(self.client, self.direccion)
        respuesta = self._post(confirmar='si', motivo='Residuo del signal de nómina retirado')
        self.assertEqual(respuesta.status_code, 302)
        self.assertFalse(Poliza.objects.filter(pk=self.poliza.pk).exists())
        self.assertFalse(MovimientoContable.objects.filter(poliza_id=self.poliza.pk).exists())

        registro = PolizaEliminada.objects.get()
        self.assertEqual(registro.eliminada_por, self.direccion)
        self.assertEqual(registro.motivo, 'Residuo del signal de nómina retirado')
        self.assertEqual(registro.total, Decimal('1369.18'))
        self.assertEqual((registro.tipo, registro.folio, registro.estado), ('E', 3, 'APLICADA'))
        self.assertEqual(len(registro.detalle['movimientos']), 2)
        self.assertEqual(registro.detalle['movimientos'][0]['debe'], '1369.18')

    def test_sin_motivo_no_borra(self):
        login_superuser_con_totp(self.client, self.direccion)
        self._post(confirmar='si', motivo='  ')
        self.assertTrue(Poliza.objects.filter(pk=self.poliza.pk).exists())
        self.assertFalse(PolizaEliminada.objects.exists())

    def test_staff_no_ve_la_accion_ni_puede_borrar(self):
        self.staff.user_permissions.add(*self.staff.user_permissions.model.objects.filter(
            content_type__app_label='contabilidad', codename__in=['view_poliza', 'change_poliza', 'delete_poliza'],
        ))
        self.client.force_login(self.staff)
        self.assertNotContains(self.client.get(URL), 'eliminar_polizas')
        self._post(confirmar='si', motivo='x')
        self.assertTrue(Poliza.objects.filter(pk=self.poliza.pk).exists())
        with self.assertRaises(PermissionDenied):
            eliminar_polizas(Poliza.objects.all(), self.staff, 'x')

    def test_borrado_estandar_de_django_cerrado(self):
        login_superuser_con_totp(self.client, self.direccion)
        self.assertNotContains(self.client.get(URL), 'value="delete_selected"')
        url = reverse('admin:contabilidad_poliza_delete', args=[self.poliza.pk])
        self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 403)
        self.assertTrue(Poliza.objects.filter(pk=self.poliza.pk).exists())

    def test_bitacora_es_de_solo_lectura(self):
        login_superuser_con_totp(self.client, self.direccion)
        eliminar_polizas(Poliza.objects.all(), self.direccion, 'prueba')
        registro = PolizaEliminada.objects.get()
        self.assertEqual(self.client.get(reverse('admin:contabilidad_polizaeliminada_changelist')).status_code, 200)
        url = reverse('admin:contabilidad_polizaeliminada_delete', args=[registro.pk])
        self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 403)
