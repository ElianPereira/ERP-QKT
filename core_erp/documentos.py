"""
Sistema de documentos QKT (Issue #373)
=======================================
Fuente única para generar los PDF del ERP. Toda plantilla de documento
extiende `documentos/_base.html` y se renderiza con `render_pdf()`, que le
inyecta lo común (logo, fuentes incrustadas, quién y cuándo lo generó) para
que ninguna vista vuelva a armar rutas `file://` por su cuenta.

Los recursos se resuelven a disco y no por HTTP: WeasyPrint no debe depender
de la red (ni de Google Fonts) para armar un documento.
"""
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone

EMPRESA = "Quinta Ko'ox Tanil"
UBICACION = 'Umán, Yucatán'
# Contacto que ve el cliente: el 9191 (agente) es el único número público
# (Memoria 2026-10-06), igual que en contratos y documentos legales.
DOMICILIO = 'Ctra. Tanil - Ticimul km 1.920, Umán, Yucatán'
WHATSAPP = '999 169 9191'
EMAIL = 'quintakooxtanil@gmail.com'


def ruta_estatica(ruta: str) -> str:
    """URL `file://` de un archivo de `static/` para WeasyPrint."""
    return (Path(settings.BASE_DIR) / 'static' / ruta).resolve().as_uri()


def contexto_documento(request=None) -> dict:
    """Lo que toda plantilla de documento puede usar sin pedirlo."""
    usuario = getattr(request, 'user', None)
    generado_por = ''
    if usuario is not None and usuario.is_authenticated:
        generado_por = usuario.get_full_name() or usuario.get_username()
    return {
        'doc_empresa': EMPRESA,
        'doc_ubicacion': UBICACION,
        'doc_domicilio': DOMICILIO,
        'doc_whatsapp': WHATSAPP,
        'doc_email': EMAIL,
        'doc_logo': ruta_estatica('img/logo.png'),
        'doc_fuentes': ruta_estatica('fonts'),
        'doc_generado_en': timezone.localtime(),
        'doc_generado_por': generado_por,
    }


def render_pdf(plantilla: str, contexto: dict, *, request=None) -> bytes:
    """Renderiza una plantilla de documento a PDF y devuelve los bytes."""
    from weasyprint import HTML

    html = render_to_string(plantilla, {**contexto_documento(request), **contexto})
    return HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()


def respuesta_pdf(plantilla: str, contexto: dict, nombre: str, *, request=None,
                  descargar: bool = False) -> HttpResponse:
    """`HttpResponse` con el PDF; en línea por defecto (se abre en el navegador)."""
    pdf = render_pdf(plantilla, contexto, request=request)
    respuesta = HttpResponse(pdf, content_type='application/pdf')
    disposicion = 'attachment' if descargar else 'inline'
    respuesta['Content-Disposition'] = f'{disposicion}; filename="{nombre}"'
    return respuesta


def _slug(texto) -> str:
    texto = unicodedata.normalize('NFKD', str(texto)).encode('ascii', 'ignore').decode()
    return re.sub(r'[^A-Za-z0-9]+', '-', texto).strip('-')


def nombre_archivo(tipo: str, *partes, extension: str = 'pdf') -> str:
    """`QKT_<Tipo>_<parte>_<parte>.pdf`; las fechas van como AAAAMMDD."""
    piezas = ['QKT', _slug(tipo)]
    for parte in partes:
        if parte in (None, ''):
            continue
        if hasattr(parte, 'strftime'):
            parte = parte.strftime('%Y%m%d')
        piezas.append(_slug(parte))
    return '_'.join(piezas) + f'.{extension}'


# ==========================================
# FORMATO DE CIFRAS (lo usan los filtros de plantilla)
# ==========================================

def _a_decimal(valor):
    if valor in (None, ''):
        return None
    try:
        return Decimal(str(valor)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return None


def cifra(valor) -> str:
    """`1234.5` → `1,234.50`; vacío si no es número."""
    numero = _a_decimal(valor)
    if numero is None:
        return ''
    return f'{numero:,.2f}'


def moneda(valor) -> str:
    """`-1234.5` → `-$1,234.50`; vacío si no es número."""
    numero = _a_decimal(valor)
    if numero is None:
        return ''
    signo = '-' if numero < 0 else ''
    return f'{signo}${abs(numero):,.2f}'
