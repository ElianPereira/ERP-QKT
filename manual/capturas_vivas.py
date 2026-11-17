"""
Corredor de capturas del manual (Issue #507). NO es un módulo de tests (el
nombre no empieza con `test_`): lo carga `generar_capturas_manual`, que lo
ejecuta con el corredor de Django para tener una base temporal y un servidor
real. Nunca corre en CI ni en producción.
"""
import glob
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings

from core_erp.test_utils import login_superuser_con_totp

from . import demo
from .capturas import CAPTURAS

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

ANCHO_MAXIMO = 1000       # píxeles de la imagen final; mantiene liviano el PDF completo
CALIDAD_WEBP = 80
OPCIONES = {'destino': None, 'solo': None}   # las fija el comando antes de correr


def ruta_chromium():
    for patron in ('/opt/pw-browsers/chromium-*/chrome-linux*/chrome', '/opt/pw-browsers/chromium'):
        encontrados = sorted(glob.glob(patron))
        if encontrados:
            return encontrados[-1]
    return None


def a_webp(png: bytes) -> bytes:
    from PIL import Image
    imagen = Image.open(io.BytesIO(png)).convert('RGB')
    if imagen.width > ANCHO_MAXIMO:
        alto = round(imagen.height * ANCHO_MAXIMO / imagen.width)
        imagen = imagen.resize((ANCHO_MAXIMO, alto), Image.LANCZOS)
    salida = io.BytesIO()
    imagen.save(salida, 'WEBP', quality=CALIDAD_WEBP, method=6)
    return salida.getvalue()


JS_RESALTAR = """
(selector) => {
  const el = document.querySelector(selector);
  if (!el) return false;
  el.scrollIntoView({block: 'center'});
  el.style.outline = '3px solid #F5C518';
  el.style.outlineOffset = '3px';
  return true;
}
"""


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}, STATIC_VERSION='')
class CapturasManual(StaticLiveServerTestCase):
    def _cookie(self, rol):
        cliente = Client()
        usuario = User.objects.get(username=demo.USUARIOS[rol][0])
        if usuario.is_superuser:
            login_superuser_con_totp(cliente, usuario)
        else:
            cliente.force_login(usuario)
        return {'name': 'sessionid', 'value': cliente.cookies['sessionid'].value, 'url': self.live_server_url}

    def _fotografiar(self, trabajos, destino):
        """Todo Playwright va en su propio hilo: su bucle de eventos no debe tocar el de Django."""
        faltas = []
        with sync_playwright() as pw:
            navegador = pw.chromium.launch(executable_path=ruta_chromium())
            for captura, cookie in trabajos:
                contexto = navegador.new_context(viewport={'width': captura.ancho, 'height': captura.alto},
                                                 locale='es-MX')
                try:
                    if cookie:
                        contexto.add_cookies([cookie])
                    pagina = contexto.new_page()
                    pagina.goto(self.live_server_url + captura.url)
                    pagina.wait_for_load_state('networkidle')
                    if captura.preparar:
                        captura.preparar(pagina)
                    if captura.resaltar:
                        selectores = [x.strip() for x in captura.resaltar.split(',')]
                        if not any(pagina.evaluate(JS_RESALTAR, x) for x in selectores):
                            faltas.append(f'{captura.nombre}: no se encontró «{captura.resaltar}»')
                    (destino / f'{captura.nombre}.webp').write_bytes(a_webp(pagina.screenshot()))
                finally:
                    contexto.close()
            navegador.close()
        return faltas

    def test_generar_capturas(self):
        if sync_playwright is None:
            raise RuntimeError('Falta Playwright: pip install playwright (el navegador se busca en /opt/pw-browsers).')
        demo.sembrar_usuarios()
        demo.sembrar_datos()
        destino = Path(OPCIONES['destino'])
        destino.mkdir(parents=True, exist_ok=True)
        trabajos = [(c, None if c.rol == 'ANONIMO' else self._cookie(c.rol)) for c in CAPTURAS
                    if not OPCIONES['solo'] or c.nombre in OPCIONES['solo']]
        with ThreadPoolExecutor(max_workers=1) as hilo:
            faltas = hilo.submit(self._fotografiar, trabajos, destino).result()
        self.assertEqual(faltas, [], 'Selectores sin resolver:\n' + '\n'.join(faltas))
