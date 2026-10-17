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
            'meses_promedio': 3, 'dias_evento': ['5', '6', '7'], 'dias_pasadia': list('1234567'),
            'dias_hospedaje': list('1234567'), 'max_eventos_semana': 2, 'dias_preparacion_evento': 0,
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

    def test_dias_se_eligen_con_casillas_y_se_guardan_como_cadena(self):
        login_superuser_con_totp(self.client, self.direccion)
        p = ParametrosNegocio.objects.create(dias_evento='67')
        url = reverse('admin:indicadores_parametrosnegocio_change', args=[p.pk])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'qkt-dias')
        self.assertContains(r, 'Qué sale de esto')
        datos = {
            'vigente_desde': '2026-10-01', 'activo': 'on', 'inicio_datos': '2026-07-01', 'meses_promedio': 3,
            'dias_evento': ['6', '5', '7'], 'dias_pasadia': ['1', '2'], 'dias_hospedaje': [],
            'max_eventos_semana': 2, 'dias_preparacion_evento': 0, 'pct_costo_variable_evento': '35',
            'pct_costo_variable_pasadia': '15', 'pct_costo_variable_hospedaje': '10',
            'utilidad_mensual_objetivo': '0',
        }
        self.assertEqual(self.client.post(url, datos).status_code, 302)
        p.refresh_from_db()
        self.assertEqual((p.dias_evento, p.dias_pasadia, p.dias_hospedaje), ('567', '12', ''))

    def test_costo_directo_no_puede_igualar_el_precio_sin_iva(self):
        p = ParametrosNegocio(precio_venta_pasadia=Decimal('1500.00'), costo_directo_pasadia=Decimal('1293.10'),
                              demanda_max_pasadia_mes=6)
        with self.assertRaises(ValidationError) as ctx:
            p.full_clean()
        self.assertIn('costo_directo_pasadia', ctx.exception.message_dict)
        p.costo_directo_pasadia = Decimal('200.00')
        p.full_clean()

    def test_un_precio_sin_tope_o_con_tope_cero_se_rechaza(self):
        for tope in (None, 0):
            p = ParametrosNegocio(precio_venta_hospedaje=Decimal('1000.00'), demanda_max_hospedaje_mes=tope)
            with self.assertRaises(ValidationError) as ctx:
                p.full_clean()
            self.assertIn('demanda_max_hospedaje_mes', ctx.exception.message_dict)
        ParametrosNegocio(precio_venta_hospedaje=Decimal('1000.00'), demanda_max_hospedaje_mes=1).full_clean()
        ParametrosNegocio(demanda_max_evento_mes=None).full_clean()      # sin precio no hay nada que topar

    def test_el_alta_rechaza_un_precio_sin_tope_y_explica_por_que(self):
        login_superuser_con_totp(self.client, self.direccion)
        r = self.client.post(reverse('admin:indicadores_parametrosnegocio_add'), self.datos(
            precio_venta_evento='12000.00'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Obligatorio y mayor que 0')
        self.assertFalse(ParametrosNegocio.objects.exists())
        r = self.client.post(reverse('admin:indicadores_parametrosnegocio_add'), self.datos(
            precio_venta_evento='12000.00', demanda_max_evento_mes='4'))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ParametrosNegocio.objects.get().demanda_max_evento_mes, 4)

    def datos(self, **extra):
        datos = {
            'vigente_desde': '2026-10-01', 'activo': 'on', 'inicio_datos': '2026-07-01', 'meses_promedio': 3,
            'dias_evento': ['5', '6', '7'], 'dias_pasadia': list('1234567'), 'dias_hospedaje': list('1234567'),
            'max_eventos_semana': 2, 'dias_preparacion_evento': 0, 'pct_costo_variable_evento': '35',
            'pct_costo_variable_pasadia': '15', 'pct_costo_variable_hospedaje': '10',
            'utilidad_mensual_objetivo': '0',
        }
        datos.update(extra)
        return datos

    def test_parametros_muestra_una_tabla_con_una_fila_por_servicio(self):
        login_superuser_con_totp(self.client, self.direccion)
        r = self.client.get(reverse('admin:indicadores_parametrosnegocio_add'))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        for encabezado in ('Servicio', 'Precio con IVA', 'Costo directo sin IVA', 'Tope al mes', 'Días de venta'):
            self.assertIn(encabezado, html)
        for campo in ('precio_venta_evento', 'costo_directo_pasadia', 'demanda_max_hospedaje_mes'):
            self.assertEqual(html.count(f'name="{campo}"'), 1, campo)           # cada campo, una sola vez
        self.assertEqual(html.count('name="dias_hospedaje"'), 7)                # una casilla por día
        self.assertIn('Habitación-noche', html)
        self.assertIn('Avanzado', html)
        self.assertLess(html.index('Precio con IVA'), html.index('Costos fijos y utilidad'))
        self.assertLess(html.index('Costos fijos y utilidad'), html.index('Avanzado'))
        self.assertLess(html.index('Avanzado'), html.index('Auditoría'))

    def test_formularios_de_alta_cargan_con_las_secciones_nuevas(self):
        login_superuser_con_totp(self.client, self.direccion)
        r = self.client.get(reverse('admin:indicadores_parametrosnegocio_add'))
        self.assertContains(r, 'Captura precio, costo directo y tope')
        r = self.client.get(reverse('admin:indicadores_metamensual_add'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Llenar desde el plan')
        self.assertEqual(r.context['adminform'].form.initial['anio'], date.today().year)

    def test_el_resumen_muestra_el_plan_con_lo_guardado(self):
        login_superuser_con_totp(self.client, self.direccion)
        p = ParametrosNegocio.objects.create(
            precio_venta_pasadia=Decimal('2000.00'), costo_directo_pasadia=Decimal('300.00'),
            demanda_max_pasadia_mes=6, precio_venta_hospedaje=Decimal('1000.00'),
            costo_directo_hospedaje=Decimal('120.00'), demanda_max_hospedaje_mes=20,
            costo_fijo_mensual_override=Decimal('3000.00'))
        r = self.client.get(reverse('admin:indicadores_parametrosnegocio_change', args=[p.pk]))
        html = r.content.decode()
        self.assertIn('Vende 6 pasadías y 20 noches-habitación', html)
        self.assertIn('Utilidad neta', html)
        self.assertNotIn('Techo teórico', html)
        p.demanda_max_hospedaje_mes = None
        p.save()
        r = self.client.get(reverse('admin:indicadores_parametrosnegocio_change', args=[p.pk]))
        self.assertContains(r, 'Techo teórico')


class LlenarDesdeElPlanTest(TestCase):
    """«Llenar desde el plan» prellena Metas con GET y no crea nada."""

    @classmethod
    def setUpTestData(cls):
        from indicadores.test_services import EjemploFijoTest

        cls.direccion = User.objects.create_superuser('direccion', 'd@x.mx', 'clave-de-prueba-123')
        EjemploFijoTest().parametros().save()

    def setUp(self):
        login_superuser_con_totp(self.client, self.direccion)

    def test_en_el_alta_prellena_sin_guardar(self):
        r = self.client.get(reverse('admin:indicadores_metamensual_add'), {'desde_plan': 1, 'anio': 2026, 'mes': 4})
        initial = r.context['adminform'].form.initial
        self.assertEqual((initial['anio'], initial['mes']), (2026, 4))
        self.assertEqual((initial['meta_eventos'], initial['meta_pasadias'], initial['meta_noches']), (4, 6, 20))
        self.assertEqual(initial['meta_venta_neta'], Decimal('68965.56'))
        self.assertContains(r, 'value="68965.56"')
        self.assertContains(r, 'Valores del plan de 04/2026')
        self.assertFalse(MetaMensual.objects.exists())

    def test_en_la_edicion_prellena_sin_guardar(self):
        meta = MetaMensual.objects.create(anio=2026, mes=4, meta_eventos=1)
        r = self.client.get(reverse('admin:indicadores_metamensual_change', args=[meta.pk]),
                            {'desde_plan': 1, 'anio': 2026, 'mes': 4})
        self.assertContains(r, 'value="68965.56"')
        self.assertEqual(r.context['adminform'].form.initial['meta_eventos'], 4)
        meta.refresh_from_db()
        self.assertEqual(meta.meta_eventos, 1)           # nada se guardó
        self.assertEqual(MetaMensual.objects.count(), 1)
        sin_boton = self.client.get(reverse('admin:indicadores_metamensual_change', args=[meta.pk]))
        self.assertEqual(sin_boton.context['adminform'].form.initial['meta_eventos'], 1)
        self.assertContains(sin_boton, 'Llenar desde el plan')

    def test_sin_plan_avisa_y_deja_los_campos(self):
        r = self.client.get(reverse('admin:indicadores_metamensual_add'), {'desde_plan': 1, 'anio': 2026, 'mes': 'x'})
        self.assertEqual(r.status_code, 200)
        ParametrosNegocio.objects.all().update(demanda_max_evento_mes=None)
        r = self.client.get(reverse('admin:indicadores_metamensual_add'), {'desde_plan': 1, 'anio': 2026, 'mes': 4})
        self.assertContains(r, 'No hay un plan para 04/2026')
        self.assertNotIn('meta_eventos', r.context['adminform'].form.initial)
