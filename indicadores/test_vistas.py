"""Tablero de Indicadores (Issue #402, fase 3): acceso, render y reglas del sistema de diseño."""
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core_erp.test_utils import login_superuser_con_totp
from indicadores.services.tablero import overrides_desde
from indicadores.test_services import MesArmado

EMOJI = re.compile('[\U0001F300-\U0001FAFF☀-➿]')


D = Decimal


class AccesoTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.direccion = User.objects.create_superuser('direccion', 'd@x.mx', 'clave-de-prueba-123')
        cls.staff = User.objects.create_user('staff', 's@x.mx', 'clave-de-prueba-123', is_staff=True)

    def test_anonimo_va_al_login_y_staff_no_superusuario_recibe_403(self):
        url = reverse('indicadores:tablero')
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_tablero_renderiza_con_base_vacia(self):
        login_superuser_con_totp(self.client, self.direccion)
        r = self.client.get(reverse('indicadores:tablero'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Indicadores')
        self.assertContains(r, 'Revisa')           # avisos en vez de errores
        r = self.client.get(reverse('indicadores:tablero'), {'mes': 'basura'})
        self.assertEqual(r.status_code, 200)       # un mes inválido cae al mes en curso

    def test_el_menu_no_se_muestra_al_staff(self):
        self.client.force_login(self.staff)
        r = self.client.get(reverse('admin:index'))
        self.assertNotContains(r, 'indicadores/parametrosnegocio')


class TableroConDatosTest(MesArmado):

    def setUp(self):
        self.precios_de_febrero(guardar=True)
        login_superuser_con_totp(self.client, User.objects.create_superuser('dir', 'd@x.mx', 'clave-de-prueba-123'))

    def test_muestra_las_cifras_calculadas_a_mano(self):
        r = self.client.get(reverse('indicadores:tablero'), {'mes': '2026-02'})
        self.assertEqual(r.status_code, 200)
        for esperado in ('$75,000.00', '$50,000.00', '38.6 %', '$32,500.00', '$7,150.00', '$101,400.00'):
            self.assertContains(r, esperado)
        self.assertContains(r, 'Techo teórico (sin topes)')      # ningún servicio tiene tope
        self.assertContains(r, 'Escenarios')
        self.assertContains(r, 'Detalle por servicio')
        self.assertContains(r, 'las dos habitaciones se rentan la misma noche')

    def test_con_topes_el_panel_es_un_plan_con_utilidad_neta(self):
        self.p.demanda_max_evento_mes, self.p.demanda_max_pasadia_mes, self.p.demanda_max_hospedaje_mes = 4, 10, 20
        self.p.save()
        r = self.client.get(reverse('indicadores:tablero'), {'mes': '2026-02'})
        self.assertContains(r, 'Plan óptimo del mes')
        self.assertNotContains(r, 'Techo teórico (sin topes)')
        self.assertContains(r, 'Vende 4 eventos')
        self.assertContains(r, 'Utilidad del plan')
        self.assertEqual(r.context['d']['mezcla']['modo'], 'plan')

    def test_simulacion_por_get(self):
        r = self.client.get(reverse('indicadores:tablero'), {'mes': '2026-02', 'demanda_EVENTO': '2'})
        self.assertContains(r, 'Simulación activa')
        self.assertContains(r, '$65,000.00')       # contribución con solo 2 eventos


class OverridesTest(TestCase):

    def test_ignora_lo_invalido(self):
        o = overrides_desde({'ticket_EVENTO': '12,500.50', 'costo_pct_PASADIA': '120', 'demanda_HOSPEDAJE': '3',
                             'ticket_PASADIA': 'abc', 'costo_pct_EVENTO': '-1', 'demanda_EVENTO': '2.0'})
        self.assertEqual(sorted(o), ['demanda_EVENTO', 'demanda_HOSPEDAJE', 'ticket_EVENTO'])
        self.assertEqual(str(o['ticket_EVENTO']), '12500.50')


class SistemaDeDisenoTest(TestCase):

    def test_plantillas_sin_style_ni_emojis_ni_confirm_nativo(self):
        raiz = Path(settings.BASE_DIR) / 'indicadores' / 'templates'
        for ruta in raiz.rglob('*.html'):
            texto = ruta.read_text()
            if 'documentos/_base.html' in texto:
                continue                                  # los PDF tienen su propia guardia
            self.assertNotRegex(texto, r'\sstyle="', msg=ruta.name)
            self.assertIsNone(EMOJI.search(texto), msg=ruta.name)
            self.assertNotIn('confirm(', texto, msg=ruta.name)
            for version in re.findall(r'Chart\.js/([\w.]+)/', texto):
                self.assertNotEqual(version, 'latest')




class EnlacesTest(TestCase):

    def test_el_centro_de_reportes_enlaza_solo_a_direccion(self):
        direccion = User.objects.create_superuser('direccion', 'd@x.mx', 'clave-de-prueba-123')
        staff = User.objects.create_user('staff', 's@x.mx', 'clave-de-prueba-123', is_staff=True)
        url = reverse('indicadores:tablero')
        login_superuser_con_totp(self.client, direccion)
        self.assertContains(self.client.get(reverse('reportes:selector')), url)
        self.client.logout()
        self.client.force_login(staff)
        self.assertNotContains(self.client.get(reverse('reportes:selector')), url)


class ExportacionTest(MesArmado):

    def setUp(self):
        self.precios_de_febrero(guardar=True)
        login_superuser_con_totp(self.client, User.objects.create_superuser('dir', 'd@x.mx', 'clave-de-prueba-123'))

    def test_pdf(self):
        import io

        import pdfplumber
        r = self.client.get(reverse('indicadores:exportar'), {'mes': '2026-02'})
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertIn('QKT_Indicadores_2026-02.pdf', r['Content-Disposition'])
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            texto = '\n'.join(p.extract_text() or '' for p in pdf.pages)
        for esperado in ('Indicadores del mes', '$50,000.00', '$101,400.00', '38.6 %', 'Escenarios',
                         'Detalle por servicio', 'Techo teórico (sin topes)'):
            self.assertIn(esperado, texto)

    def test_excel(self):
        import io

        import openpyxl
        r = self.client.get(reverse('indicadores:exportar'), {'mes': '2026-02', 'formato': 'excel'})
        self.assertIn('QKT_Indicadores_2026-02.xlsx', r['Content-Disposition'])
        libro = openpyxl.load_workbook(io.BytesIO(r.content))
        self.assertIn('Mezcla óptima', libro.sheetnames)
        escenarios = {f[0].value: f[9].value for f in libro['Escenarios'].iter_rows(min_row=2)}
        self.assertEqual(escenarios['Techo teórico'], 68900)
        self.assertEqual(list(escenarios)[1], 'Mes anterior (real)')
        equilibrio = {f[0].value: f[1].value for f in libro['Equilibrio'].iter_rows(min_row=2)}
        self.assertEqual(equilibrio['Venta neta necesaria'], 50000)

    def test_staff_no_exporta(self):
        self.client.logout()
        self.client.force_login(User.objects.create_user('s', 's@x.mx', 'clave-de-prueba-123', is_staff=True))
        self.assertEqual(self.client.get(reverse('indicadores:exportar')).status_code, 403)


class ProponerMetaTest(MesArmado):

    def setUp(self):
        self.precios_de_febrero(guardar=True)

    def test_propone_y_actualiza_sin_duplicar(self):
        from indicadores.models import MetaMensual
        from indicadores.services.metas import proponer_meta

        usuario = User.objects.create_superuser('dir', 'd@x.mx', 'clave-de-prueba-123')
        meta = proponer_meta(2026, 2, usuario, hoy=date(2026, 3, 5), parametros=self.p)
        self.assertEqual((meta.meta_venta_neta, meta.meta_ocupacion_pct), (D('50000.00'), D('38.6')))
        self.assertEqual((meta.meta_eventos, meta.meta_pasadias, meta.meta_noches), (3, 5, 12))   # 9 ventas repartidas 4:6:2 (half-up); 2 reservaciones × 6 habitaciones-noche
        self.assertEqual(meta.presupuesto_marketing, D('4431.75'))
        self.assertEqual(meta.created_by, usuario)
        meta_otra = proponer_meta(2026, 2, usuario, hoy=date(2026, 3, 5), parametros=self.p)
        self.assertEqual(meta.pk, meta_otra.pk)
        self.assertEqual(MetaMensual.objects.filter(anio=2026, mes=2, activo=True).count(), 1)

    def test_con_topes_la_meta_sale_del_plan(self):
        from indicadores.services.metas import proponer_meta

        self.p.demanda_max_evento_mes, self.p.demanda_max_pasadia_mes, self.p.demanda_max_hospedaje_mes = 4, 10, 20
        self.p.save()
        meta = proponer_meta(2026, 2, hoy=date(2026, 3, 5), parametros=self.p)
        self.assertEqual((meta.meta_eventos, meta.meta_pasadias, meta.meta_noches), (4, 10, 20))
        self.assertIn('Propuesta desde plan', meta.notas)

    def test_accion_del_admin(self):
        from django.urls import reverse as r

        from indicadores.models import MetaMensual
        usuario = User.objects.create_superuser('dir', 'd@x.mx', 'clave-de-prueba-123')
        login_superuser_con_totp(self.client, usuario)
        meta = MetaMensual.objects.create(anio=2026, mes=2)
        resp = self.client.post(r('admin:indicadores_metamensual_changelist'), {
            'action': 'proponer_desde_equilibrio', '_selected_action': [meta.pk]})
        self.assertEqual(resp.status_code, 302)
        meta.refresh_from_db()
        self.assertGreater(meta.meta_venta_neta, 0)
