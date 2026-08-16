"""
Cabeceras de seguridad para el sitio.

Se aplican en tres niveles según la ruta, para maximizar protección sin romper
el admin (Jazzmin) ni los pagos (Openpay/3-D Secure):

  1. Permissions-Policy: en TODAS las respuestas (es seguro; solo desactiva
     cámara/micrófono/geolocalización, que ninguna página usa). Cubre también
     admin y portal.

  2. Content-Security-Policy (bloqueante): solo en páginas públicas — landing
     ("/"), cotizador ("/cotizar…") y sus APIs ("/api/…").

  3. CSP en modo Report-Only (NO bloquea, solo reporta) para el portal de pago
     ("/mi-evento/…"): opt-in vía PORTAL_CSP_REPORT_ONLY=True. Sirve para
     observar qué recursos carga Openpay/3-D Secure y poder diseñar después una
     CSP a medida sin arriesgar cobros reales.

El admin ("/admin/…") queda fuera de cualquier CSP porque Jazzmin/AdminLTE usan
recursos propios que una CSP estricta rompería.

Toggles de entorno:
  - PUBLIC_CSP_ENABLED (default True): apaga la CSP pública sin tocar código.
  - PUBLIC_CSP_REPORT_ONLY (default False): la CSP pública en modo prueba.
  - PORTAL_CSP_REPORT_ONLY (default False): activa la CSP Report-Only del portal.
"""
import logging

from django.conf import settings

logger_seguridad = logging.getLogger('django.security')

# --- Permissions-Policy: seguro en todo el sitio (no incluye 'payment' para no
# interferir con Openpay/3-D Secure en el portal de pago). ---
PERMISSIONS_POLICY = "geolocation=(), microphone=(), camera=()"

# --- CSP bloqueante de páginas públicas (unión de lo que cargan landing y
# cotizador): Google Fonts, Cloudinary (imágenes), jsDelivr (flatpickr) y el
# mapa de Google. 'unsafe-inline' es necesario por sus <style>/<script> inline. ---
PUBLIC_CSP = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'self'; "
    "form-action 'self'; "
    "img-src 'self' data: https://media.quintakooxtanil.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "frame-src https://www.google.com; "
    "connect-src 'self'; "
    "upgrade-insecure-requests"
)

# --- CSP objetivo del portal de pago, SOLO Report-Only (no bloquea). Refleja
# los orígenes conocidos de Openpay para que, al probar un cobro real, las
# violaciones en consola revelen qué más hay que permitir (antifraude, ACS del
# banco en 3-D Secure, etc.) antes de activar una CSP bloqueante. ---
PORTAL_CSP_REPORT_ONLY_POLICY = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'self'; "
    "img-src 'self' data: https://media.quintakooxtanil.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
    "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com data:; "
    "script-src 'self' 'unsafe-inline' https://openpay.s3.amazonaws.com; "
    "connect-src 'self' https://api.openpay.mx https://sandbox-api.openpay.mx; "
    "frame-src https://www.google.com"
)


def _es_pagina_publica(path):
    return path == '/' or path.startswith('/cotizar') or path.startswith('/api/')


def _tiene_csp(response):
    return (
        'Content-Security-Policy' in response
        or 'Content-Security-Policy-Report-Only' in response
    )


class PublicSecurityHeadersMiddleware:
    """CSP + Permissions-Policy escalonadas por ruta (ver docstring del módulo)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        path = request.path

        # 1) Permissions-Policy en todas las respuestas (seguro).
        response.setdefault('Permissions-Policy', PERMISSIONS_POLICY)

        # 2) CSP bloqueante en páginas públicas.
        if (
            getattr(settings, 'PUBLIC_CSP_ENABLED', True)
            and _es_pagina_publica(path)
            and not _tiene_csp(response)
        ):
            cabecera = (
                'Content-Security-Policy-Report-Only'
                if getattr(settings, 'PUBLIC_CSP_REPORT_ONLY', False)
                else 'Content-Security-Policy'
            )
            response[cabecera] = PUBLIC_CSP

        # 3) CSP Report-Only (no bloquea) en el portal de pago, opt-in.
        elif (
            getattr(settings, 'PORTAL_CSP_REPORT_ONLY', False)
            and path.startswith('/mi-evento/')
            and not _tiene_csp(response)
        ):
            response['Content-Security-Policy-Report-Only'] = PORTAL_CSP_REPORT_ONLY_POLICY

        return response


class AuthorizationAuditMiddleware:
    """Registra explícitamente cada 403 de autorización, con quién y qué ruta.

    Se apoya en la respuesta (no en un `process_exception`) para cubrir por
    igual el `raise PermissionDenied`/`@permission_required(raise_exception=
    True)` y cualquier vista que regrese un 403 manual sin lanzar excepción.
    Django ya loguea el 403 en `django.request` (solo la ruta y el status);
    esta línea aparte añade el usuario y queda en un logger propio
    (`django.security`) para poder filtrarla o alertar sobre ella sin
    mezclarla con el resto del tráfico.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code == 403:
            usuario = request.user if getattr(request, 'user', None) and request.user.is_authenticated else 'anónimo'
            logger_seguridad.warning(
                "403 de autorización: usuario=%s ruta=%s método=%s",
                usuario, request.path, request.method,
            )
        return response
