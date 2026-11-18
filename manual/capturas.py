"""
Lista de capturas del manual (Issue #507). Una entrada por imagen: nombre del
archivo (sin extensión), pantalla, rol con el que se entra, ancho y, si hace
falta, qué resaltar y qué hacer antes de fotografiar.

Se regeneran con `python manage.py generar_capturas_manual` (base temporal con
datos inventados de `manual/demo.py`). Quien cambie una pantalla que el manual
describe corre el comando en el mismo PR.
"""
from dataclasses import dataclass
from typing import Callable, Optional

ESCRITORIO = 1100
CELULAR = 390


@dataclass(frozen=True)
class Captura:
    nombre: str                       # archivo en static/manual/img/<nombre>.webp
    url: str                          # ruta del admin (o nombre de URL con reverse)
    rol: str                          # clave de manual.demo.USUARIOS, o 'ANONIMO'
    ancho: int = ESCRITORIO
    resaltar: Optional[str] = None    # selector CSS (o text=...) que se enmarca
    preparar: Optional[Callable] = None   # función(page) previa a la captura
    alto: int = 700


def _elegir_primer_estado(page):
    """Marca el primer renglón para que aparezca la barra de acciones."""
    page.locator('#result_list tbody input[type=checkbox]').first.check()


def _elegir(page, campo, valor=None):
    """Elige una opción en un select con select2 (el `<select>` real está oculto)."""
    page.evaluate(
        """([campo, valor]) => {
          const copias = [window.jQuery, window.$, window.django && django.jQuery].filter(Boolean);
          for (const $ of copias) {
            const sel = $('#' + campo);
            const opcion = valor || sel.find('option').filter((i, o) => o.value).first().val();
            if (opcion) sel.val(opcion).trigger('change').trigger('change.select2');
          }
        }""", [campo, valor])


def _llenar_pago(page):
    page.locator('a.nav-link:has-text("Datos")').first.click()
    page.wait_for_timeout(400)
    _elegir(page, 'id_cotizacion')
    _elegir(page, 'id_metodo', 'TRANSFERENCIA')
    page.fill('#id_monto', '9000.00')
    page.fill('#id_referencia', 'CLAVE-DE-RASTREO-EJEMPLO')


CAPTURAS = [
    Captura('entrar-acceso', '/admin/login/', 'ANONIMO', ancho=ESCRITORIO, alto=620,
            resaltar='button[type=submit]'),
    Captura('cobro-lista', '/admin/comercial/pago/', 'ENCARGADO', resaltar='a.addlink, a[href$="/pago/add/"]'),
    Captura('cobro-formulario', '/admin/comercial/pago/add/', 'ENCARGADO', alto=900,
            resaltar='#id_monto', preparar=_llenar_pago),
    Captura('estado-correos', '/admin/contabilidad/correoestadocuenta/', 'DIRECCION'),
    Captura('estado-lista', '/admin/contabilidad/estadocuentabancario/', 'CONTADOR',
            resaltar='select[name=action]', preparar=_elegir_primer_estado),
]
