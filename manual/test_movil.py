"""Revisión a 390 px (iPhone 13) del Manual (regla de CLAUDE.md: toda pantalla nueva se revisa en celular).

Se salta sola sin Playwright/Chromium. `MOVIL_CAPTURAS=<carpeta>` guarda capturas.
"""
import os

from django.urls import reverse

from core_erp.test_movil import JS_SOBRESALEN, MovilBase

from . import services as sv


class ManualMovilTest(MovilBase):
    def _revisar(self, ruta, nombre):
        self.page.goto(self.live_server_url + ruta)
        self.page.wait_for_load_state('networkidle')
        desborde = self.page.evaluate('() => document.documentElement.scrollWidth - window.innerWidth')
        self.assertLessEqual(desborde, 1, f'{ruta} desborda {desborde}px en horizontal')
        self.assertEqual(self.page.evaluate(JS_SOBRESALEN), [], f'{ruta}: elementos fuera del viewport')
        carpeta = os.environ.get('MOVIL_CAPTURAS')
        if carpeta:
            os.makedirs(carpeta, exist_ok=True)
            self.page.screenshot(path=os.path.join(carpeta, f'manual-{nombre}.png'), full_page=True)

    def test_indice_y_buscador(self):
        self._revisar(reverse('manual:indice'), 'indice')
        self._revisar(reverse('manual:indice') + '?q=cobro', 'busqueda')

    def test_cada_seccion_cabe_en_el_celular(self):
        for s in sv.cargar_secciones():
            with self.subTest(seccion=s.slug):
                self._revisar(reverse('manual:seccion', args=[s.slug]), s.slug)
