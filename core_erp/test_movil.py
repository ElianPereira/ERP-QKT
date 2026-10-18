"""
Barrido móvil del admin (Issue #417): viewport iPhone 13 (390 px, táctil) sobre
todas las listas con registros. Falla si una tabla queda recortada sin scroll,
si la última columna es inalcanzable o si la página desborda en horizontal.

Se salta solo si no hay Playwright o Chromium (CI no los instala).
Ejecutar: python manage.py test core_erp.test_movil
"""
import glob
import os
from datetime import date, timedelta

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import NoReverseMatch, reverse

from comercial.models import Cliente, Cotizacion, ItemCotizacion
from core_erp.test_utils import login_superuser_con_totp

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

ANCHO_IPHONE = 390

# Elementos recortados a propósito (accesibilidad) o con elipsis normal.
JS_BARRIDO = """
() => {
  const ancho = window.innerWidth;
  const res = {desborde_pagina: document.documentElement.scrollWidth - ancho, tablas: []};
  const tabla = document.querySelector('#result_list');
  if (tabla) {
    let scroller = null, recortada = false;
    for (let el = tabla; el && el !== document.documentElement; el = el.parentElement) {
      const cs = getComputedStyle(el);
      const ox = cs.overflowX;
      if ((ox === 'auto' || ox === 'scroll') && el.scrollWidth > el.clientWidth + 1) { scroller = el; break; }
      if ((ox === 'hidden' || ox === 'clip') && el.scrollWidth > el.clientWidth + 1) { recortada = true; break; }
    }
    const filas = tabla.querySelectorAll('tbody tr');
    const ultima = filas.length ? filas[0].lastElementChild : null;
    let alcanza = null;
    if (scroller && ultima) {
      scroller.scrollLeft = scroller.scrollWidth;
      const r = ultima.getBoundingClientRect();
      alcanza = r.right <= ancho + 1 && r.left >= 0;
      scroller.scrollLeft = 0;
    }
    res.tablas.push({
      recortada: recortada,
      sin_scroll: !scroller && tabla.getBoundingClientRect().right > ancho + 1,
      alcanza_ultima: alcanza,
    });
  }
  return res;
}
"""


def _ruta_chromium():
    ruta = os.environ.get('PLAYWRIGHT_CHROMIUM_PATH')
    if ruta:
        return ruta
    for patron in ('/opt/pw-browsers/chromium-*/chrome-linux/chrome', '/opt/pw-browsers/chromium'):
        encontrados = sorted(glob.glob(patron))
        if encontrados:
            return encontrados[-1]
    return None


class MovilBase(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            from unittest import SkipTest
            raise SkipTest('Playwright no está instalado')
        # Playwright síncrono deja un event loop activo en el hilo y Django
        # lo toma por contexto async al limpiar la BD entre tests.
        os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
        super().setUpClass()
        cls._pw = sync_playwright().start()
        try:
            cls._browser = cls._pw.chromium.launch(executable_path=_ruta_chromium())
        except Exception as exc:
            cls._pw.stop()
            super().tearDownClass()
            from unittest import SkipTest
            raise SkipTest(f'Sin Chromium disponible: {exc}')

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()
        super().tearDownClass()

    def setUp(self):
        usuario = get_user_model().objects.create_superuser('movil', 'movil@example.com', 'x' * 20)
        cliente = Cliente.objects.create(nombre='Cliente Móvil', tipo_persona='FISICA', telefono='9991234567')
        for i in range(3):
            cot = Cotizacion.objects.create(
                cliente=cliente, nombre_evento=f'Evento móvil {i}',
                fecha_evento=date.today() + timedelta(days=60 + i), incluye_refrescos=False,
            )
            ItemCotizacion.objects.create(
                cotizacion=cot, descripcion='Servicio', cantidad=1, precio_unitario=10000,
            )
        self.cotizacion = cot
        django_client = Client()
        login_superuser_con_totp(django_client, usuario)
        self.ctx = self._browser.new_context(**self._pw.devices['iPhone 13'])
        self.ctx.add_cookies([{
            'name': 'sessionid', 'value': django_client.cookies['sessionid'].value,
            'url': self.live_server_url,
        }])
        self.page = self.ctx.new_page()

    def tearDown(self):
        self.ctx.close()

    def barrer(self, ruta):
        self.page.goto(self.live_server_url + ruta)
        self.page.wait_for_load_state('networkidle')
        return self.page.evaluate(JS_BARRIDO)


def urls_de_listas():
    urls = []
    for modelo in admin.site._registry:
        nombre = f'admin:{modelo._meta.app_label}_{modelo._meta.model_name}_changelist'
        try:
            urls.append(reverse(nombre))
        except NoReverseMatch:
            continue
    return sorted(urls)


class ListasMovilTest(MovilBase):
    def test_todas_las_listas_se_deslizan_hasta_la_ultima_columna(self):
        problemas = []
        for ruta in urls_de_listas():
            res = self.barrer(ruta)
            for t in res['tablas']:
                if t['recortada'] or t['sin_scroll'] or t['alcanza_ultima'] is False:
                    problemas.append(f'{ruta}: {t}')
        self.assertEqual(problemas, [], 'Listas recortadas en móvil:\n' + '\n'.join(problemas))

    def test_escritorio_no_agrega_scroll_ni_pierde_el_redondeo(self):
        self.page.set_viewport_size({'width': 1440, 'height': 900})
        self.page.goto(self.live_server_url + reverse('admin:comercial_cotizacion_changelist'))
        datos = self.page.evaluate("""() => {
          const c = document.querySelector('#result_list').closest('.table-responsive');
          const cs = getComputedStyle(c);
          return {sw: c.scrollWidth, cw: c.clientWidth, desborda: c.scrollWidth > c.clientWidth + 1, radio: cs.borderTopLeftRadius};
        }""")
        self.assertFalse(datos['desborda'], datos)
        self.assertNotEqual(datos['radio'], '0px')


class CapturasMovilTest(MovilBase):
    """Capturas de Cotizaciones (lista y formulario) para el PR; solo con
    MOVIL_CAPTURAS=<directorio> y el prefijo en MOVIL_PREFIJO."""

    def test_capturas_cotizaciones(self):
        destino = os.environ.get('MOVIL_CAPTURAS')
        if not destino:
            self.skipTest('MOVIL_CAPTURAS no definido')
        prefijo = os.environ.get('MOVIL_PREFIJO', 'captura')
        os.makedirs(destino, exist_ok=True)
        for nombre, ruta in (
            ('lista', reverse('admin:comercial_cotizacion_changelist')),
            ('formulario', reverse('admin:comercial_cotizacion_change', args=[self.cotizacion.pk])),
        ):
            self.barrer(ruta)
            self.page.screenshot(path=f'{destino}/{prefijo}_{nombre}.png')
            if nombre == 'lista':
                self.page.evaluate("document.querySelector('.table-responsive').scrollLeft = 9999")
                self.page.locator('#result_list').scroll_into_view_if_needed()
                self.page.screenshot(path=f'{destino}/{prefijo}_lista_derecha.png')
