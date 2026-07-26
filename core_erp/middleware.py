"""
Cabeceras de seguridad para las páginas PÚBLICAS del sitio (landing y cotizador).

Por qué un middleware con allowlist y no una CSP global:
  - El admin (Jazzmin/AdminLTE) usa scripts/estilos propios y una CSP estricta
    lo rompería → se excluye.
  - El portal de pago (/mi-evento/) carga Openpay.js, su antifraude y, en cargos
    con tarjeta, 3-D Secure que redirige/enmarca páginas del banco (dominios
    arbitrarios). Una CSP ahí rompería pagos reales → se excluye.

Se aplica solo a la landing ("/"), al cotizador ("/cotizar…") y a sus APIs
("/api/…"). Configurable por entorno:
  - PUBLIC_CSP_ENABLED (default True): apaga la CSP sin tocar código si algo
    se rompiera en producción.
  - PUBLIC_CSP_REPORT_ONLY (default False): la envía como Report-Only para
    probar sin bloquear.
"""
from django.conf import settings

# CSP = unión de lo que cargan landing y cotizador:
#   fonts.googleapis/gstatic (Google Fonts), res.cloudinary.com (imágenes de la
#   landing), cdn.jsdelivr.net (flatpickr), www.google.com (mapa embebido).
# 'unsafe-inline' es necesario por los <style> y <script> inline de esas
# plantillas; el resto queda restringido a 'self' + orígenes conocidos.
PUBLIC_CSP = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'self'; "
    "form-action 'self'; "
    "img-src 'self' data: https://res.cloudinary.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "frame-src https://www.google.com; "
    "connect-src 'self'; "
    "upgrade-insecure-requests"
)

PERMISSIONS_POLICY = "geolocation=(), microphone=(), camera=(), payment=()"


def _es_pagina_publica(path):
    return path == '/' or path.startswith('/cotizar') or path.startswith('/api/')


class PublicSecurityHeadersMiddleware:
    """Añade CSP + Permissions-Policy a las páginas públicas (no admin, no portal)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if not getattr(settings, 'PUBLIC_CSP_ENABLED', True):
            return response
        if not _es_pagina_publica(request.path):
            return response

        cabecera = (
            'Content-Security-Policy-Report-Only'
            if getattr(settings, 'PUBLIC_CSP_REPORT_ONLY', False)
            else 'Content-Security-Policy'
        )
        # No pisar una CSP que una vista haya fijado explícitamente.
        if 'Content-Security-Policy' not in response and 'Content-Security-Policy-Report-Only' not in response:
            response[cabecera] = PUBLIC_CSP
        response.setdefault('Permissions-Policy', PERMISSIONS_POLICY)
        return response
