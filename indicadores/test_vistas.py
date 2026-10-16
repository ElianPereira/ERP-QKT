"""Tablero de Indicadores (Issue #402, fase 3): acceso, render y reglas del sistema de diseño."""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core_erp.test_utils import login_superuser_con_totp
from indicadores.services.tablero import overrides_desde
from indicadores.test_services import MesArmado

EMOJI = re.compile('[\U0001F300-\U0001FAFF☀-➿]')


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
        login_superuser_con_totp(self.client, User.objects.create_superuser('dir', 'd@x.mx', 'clave-de-prueba-123'))

    def test_muestra_las_cifras_calculadas_a_mano(self):
        r = self.client.get(reverse('indicadores:tablero'), {'mes': '2026-02'})
        self.assertEqual(r.status_code, 200)
        for esperado in ('$75,000.00', '$50,000.00', '38.6 %', '$32,500.00', '$7,150.00', '$101,400.00'):
            self.assertContains(r, esperado)
        self.assertContains(r, 'Estimado')         # el costo de evento no tiene muestra

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
