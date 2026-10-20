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

# Controles de formulario que se salen de su panel o del viewport, o que
# quedan demasiado angostos para leerse (< 120 px).
JS_CULPABLES = """
() => {
  const ancho = window.innerWidth, malos = [];
  const visibles = document.querySelectorAll(
    'form textarea, form input:not([type=hidden]):not([type=checkbox]):not([type=radio]), form select, form .select2-container, .selector');
  for (const el of visibles) {
    if (el.closest('.sr-only, .qkt-panel__cuerpo--tabla, .select2-hidden-accessible, .empty-form, .inline-related.empty-form')) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0) continue;
    const panel = el.closest('.card-body, .tab-pane');
    const limite = panel ? panel.getBoundingClientRect().right : ancho;
    const etiqueta = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + ' w=' + Math.round(r.width);
    if (r.right > Math.min(limite, ancho) + 1) malos.push('desborda ' + etiqueta);
    else if (el.classList.contains('select2-container') && r.width < 120) malos.push('angosto ' + etiqueta);
  }
  return malos.slice(0, 6);
}
"""

# Elementos que sobresalen del viewport fuera de un scroller (auto/scroll).
JS_SOBRESALEN = """
() => {
  const ancho = window.innerWidth, malos = [];
  for (const el of document.body.querySelectorAll('main *, .content-wrapper *')) {
    if (el.closest('.sr-only, .select2-container--open, .select2-hidden-accessible')) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.right <= ancho + 1) continue;
    let ok = false;
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const ox = getComputedStyle(p).overflowX;
      if (ox === 'auto' || ox === 'scroll') { ok = true; break; }
    }
    if (!ok && getComputedStyle(el).position !== 'fixed') {
      malos.push(el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + '.' + String(el.className).split(' ')[0] + ' r=' + Math.round(r.right));
    }
  }
  return malos.slice(0, 5);
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


class FlujoCotizacionMovilTest(MovilBase):
    """Desde el celular: lista de Cotizaciones, íconos de acción y menú «⋯»
    con el dedo."""

    def setUp(self):
        super().setUp()
        self.page.goto(self.live_server_url + reverse('admin:comercial_cotizacion_changelist'))
        self.page.wait_for_load_state('networkidle')

    def test_iconos_de_accion_tienen_area_tactil(self):
        chicos = self.page.evaluate("""() => [...document.querySelectorAll('#result_list .qkt-btn--icono')]
            .map(e => e.getBoundingClientRect()).filter(r => r.width < 43.5 || r.height < 43.5).length""")
        self.assertEqual(chicos, 0)

    def test_menu_mas_acciones_abre_dentro_de_pantalla_y_cierra_al_tocar_fuera(self):
        resumen = self.page.locator('details.qkt-menu > summary').first
        resumen.tap()
        panel = self.page.locator('details.qkt-menu[open] .qkt-menu__panel').first
        panel.wait_for(state='visible')
        caja = panel.bounding_box()
        alto = self.page.evaluate('window.innerHeight')
        self.assertGreaterEqual(caja['x'], 0)
        self.assertLessEqual(caja['x'] + caja['width'], ANCHO_IPHONE + 1)
        self.assertGreaterEqual(caja['y'], 0)
        self.assertLessEqual(caja['y'] + caja['height'], alto + 1)
        self.page.touchscreen.tap(385, 130)
        self.assertEqual(self.page.locator('details.qkt-menu[open]').count(), 0)

    def test_tocar_el_folio_abre_la_cotizacion(self):
        self.page.locator('#result_list tbody th a').first.tap()
        self.page.wait_for_url('**/change/')
        self.assertIn('/change/', self.page.url)


class MenuLateralMovilTest(MovilBase):
    def test_menu_lateral_abre_cabe_y_enlaza(self):
        self.barrer('/admin/')
        self.page.locator('a[data-lte-toggle="sidebar"]').first.tap()
        lateral = self.page.locator('aside.main-sidebar, aside.app-sidebar').first
        self.page.wait_for_timeout(500)
        caja = lateral.bounding_box()
        self.assertGreaterEqual(caja['x'], -1)
        self.assertLessEqual(caja['x'] + caja['width'], ANCHO_IPHONE + 1)
        enlaces = self.page.locator('aside .nav-link:visible')
        self.assertGreater(enlaces.count(), 0)
        destino = self.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        self.assertTrue(destino)
        if os.environ.get('MOVIL_CAPTURAS'):
            self.page.screenshot(path=os.environ['MOVIL_CAPTURAS'] + '/menu_lateral.png')


class PaginasMovilTest(MovilBase):
    """Ninguna pantalla desborda la página en horizontal (formularios,
    altas y páginas propias)."""

    def _rutas(self):
        rutas = []
        for modelo, model_admin in admin.site._registry.items():
            if not model_admin.has_add_permission(self._peticion()):
                continue
            nombre = f'admin:{modelo._meta.app_label}_{modelo._meta.model_name}_add'
            try:
                rutas.append(reverse(nombre))
            except NoReverseMatch:
                continue
        rutas.append(reverse('admin:comercial_cotizacion_change', args=[self.cotizacion.pk]))
        return sorted(rutas)

    def _peticion(self):
        from django.test import RequestFactory
        peticion = RequestFactory().get('/')
        peticion.user = get_user_model().objects.get(username='movil')
        return peticion

    def test_ninguna_pantalla_desborda(self):
        problemas = []
        for ruta in self._rutas():
            res = self.barrer(ruta)
            culpables = self.page.evaluate(JS_CULPABLES)
            if res['desborde_pagina'] > 0 or culpables:
                problemas.append(f"{ruta}: +{res['desborde_pagina']}px {culpables}")
        self.assertEqual(problemas, [], 'Páginas que desbordan en móvil:\n' + '\n'.join(problemas))

    def test_pestañas_del_formulario_ocupan_un_renglon(self):
        self.barrer(reverse('admin:comercial_cotizacion_change', args=[self.cotizacion.pk]))
        alto = self.page.evaluate("document.querySelector('.nav-tabs').getBoundingClientRect().height")
        self.assertLess(alto, 60)

    def test_directorio_de_responsables_no_desborda(self):
        from comunicacion.models import Responsable
        resp = Responsable.objects.create(rol='DIRECCION', nombre='Dirección móvil', telefono_wa='525555550011',
                                          telefono_pendiente='525555550012')
        problemas = []
        for ruta in (reverse('admin:comunicacion_responsable_changelist'),
                     reverse('admin:comunicacion_responsable_change', args=[resp.pk])):
            res = self.barrer(ruta)
            # Los filtros de las listas (select2 en dos columnas) ya los barre
            # ListasMovilTest; aquí solo importa el formulario.
            culpables = self.page.evaluate(JS_CULPABLES) if '/change/' in ruta else []
            if res['desborde_pagina'] > 0 or culpables:
                problemas.append(f"{ruta}: +{res['desborde_pagina']}px {culpables}")
        self.assertEqual(problemas, [], 'Responsables desborda en móvil:\n' + '\n'.join(problemas))

    def test_paginas_propias_no_desbordan(self):
        rutas = [
            '/admin/', reverse('calendario_unificado'),
            reverse('indicadores:tablero'), reverse('indicadores:plan'),
            reverse('cumplimiento:tablero'), reverse('reportes:selector'),
        ]
        problemas = []
        for ruta in rutas:
            res = self.barrer(ruta)
            sobresalen = self.page.evaluate(JS_SOBRESALEN)
            if res['desborde_pagina'] > 0 or sobresalen:
                problemas.append(f"{ruta}: +{res['desborde_pagina']}px {sobresalen}")
        self.assertEqual(problemas, [], 'Páginas propias que desbordan:\n' + '\n'.join(problemas))


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
            self.page.screenshot(path=f'{destino}/{prefijo}_{nombre}.png', full_page=(nombre == 'formulario'))
            if nombre == 'lista':
                self.page.evaluate("document.querySelector('.table-responsive').scrollLeft = 9999")
                self.page.locator('#result_list').scroll_into_view_if_needed()
                self.page.screenshot(path=f'{destino}/{prefijo}_lista_derecha.png')
