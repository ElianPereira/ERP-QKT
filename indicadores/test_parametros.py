"""Fase 1 (Issue #402): parámetros, metas y acceso exclusivo de Dirección."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from core_erp.test_utils import login_superuser_con_totp
from indicadores.models import MetaMensual, ParametrosNegocio


class ParametrosTest(TestCase):

    def test_sin_captura_devuelve_los_defaults_sin_guardar(self):
        p = ParametrosNegocio.vigente()
        self.assertIsNone(p.pk)
        self.assertEqual(p.dias_de('EVENTO'), {5, 6, 7})
        self.assertEqual(p.dias_de('PASADIA'), set(range(1, 8)))
        self.assertEqual(p.max_eventos_semana, 2)
        self.assertEqual(p.pct_costo_de('EVENTO'), Decimal('35.00'))
        self.assertEqual(p.inicio_datos, date(2026, 7, 1))

    def test_uno_nuevo_desactiva_al_anterior_sin_borrarlo(self):
        viejo = ParametrosNegocio.objects.create(vigente_desde=date(2026, 7, 1))
        nuevo = ParametrosNegocio.objects.create(vigente_desde=date(2026, 10, 1), max_eventos_semana=3)
        viejo.refresh_from_db()
        self.assertFalse(viejo.activo)
        self.assertTrue(nuevo.activo)
        self.assertEqual(ParametrosNegocio.vigente().pk, nuevo.pk)
        self.assertEqual(ParametrosNegocio.objects.count(), 2)

    def test_dias_invalidos_se_rechazan(self):
        for valor in ('58', '5567', 'ab', '0'):
            with self.assertRaises(ValidationError, msg=valor):
                ParametrosNegocio(dias_evento=valor).clean()
        ParametrosNegocio(dias_evento='').clean()  # vacío = no se vende

    def test_una_meta_activa_por_mes(self):
        MetaMensual.objects.create(anio=2026, mes=11)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MetaMensual.objects.create(anio=2026, mes=11)
        MetaMensual.objects.filter(anio=2026, mes=11).update(activo=False)
        MetaMensual.objects.create(anio=2026, mes=11)
        self.assertEqual(MetaMensual.del_mes(2026, 11).activo, True)


class AdminSoloDireccionTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.direccion = User.objects.create_superuser('direccion', 'd@x.mx', 'clave-de-prueba-123')
        cls.staff = User.objects.create_user('staff', 's@x.mx', 'clave-de-prueba-123', is_staff=True)
        cls.staff.user_permissions.set(Permission.objects.filter(content_type__app_label='indicadores'))

    def test_superusuario_captura_y_queda_auditado(self):
        login_superuser_con_totp(self.client, self.direccion)
        r = self.client.post(reverse('admin:indicadores_parametrosnegocio_add'), {
            'vigente_desde': '2026-10-01', 'activo': 'on', 'inicio_datos': '2026-07-01',
            'meses_promedio': 3, 'dias_evento': '567', 'dias_pasadia': '1234567',
            'dias_hospedaje': '1234567', 'max_eventos_semana': 2, 'dias_preparacion_evento': 0,
            'pct_costo_variable_evento': '35', 'pct_costo_variable_pasadia': '15',
            'pct_costo_variable_hospedaje': '10', 'utilidad_mensual_objetivo': '0',
        })
        self.assertEqual(r.status_code, 302, getattr(r, 'context_data', None))
        p = ParametrosNegocio.objects.get()
        self.assertEqual(p.created_by, self.direccion)
        self.assertEqual(p.updated_by, self.direccion)

    def test_staff_no_superusuario_recibe_403_aunque_tenga_permisos(self):
        self.client.force_login(self.staff)
        for nombre in ('parametrosnegocio', 'metamensual'):
            self.assertEqual(self.client.get(reverse(f'admin:indicadores_{nombre}_changelist')).status_code, 403)
            self.assertEqual(self.client.get(reverse(f'admin:indicadores_{nombre}_add')).status_code, 403)

    def test_nadie_borra(self):
        login_superuser_con_totp(self.client, self.direccion)
        p = ParametrosNegocio.objects.create()
        r = self.client.post(reverse('admin:indicadores_parametrosnegocio_delete', args=[p.pk]), {'post': 'yes'})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(ParametrosNegocio.objects.filter(pk=p.pk).exists())
