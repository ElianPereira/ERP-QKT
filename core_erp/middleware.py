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

  4. CSP en modo Report-Only (NO bloquea) para el admin ("/admin/…"): opt-in
     vía ADMIN_CSP_REPORT_ONLY=True. Jazzmin/AdminLTE usan bastante estilo y
     script inline, así que una CSP bloqueante ahí sin antes observarla en uso
     real es alto riesgo de romper el panel de operación diaria — por eso
     nunca es bloqueante por defecto (orden 37 del backlog, SEC-CFG-002).

Toggles de entorno:
  - PUBLIC_CSP_ENABLED (default True): apaga la CSP pública sin tocar código.
  - PUBLIC_CSP_REPORT_ONLY (default False): la CSP pública en modo prueba.
  - PORTAL_CSP_REPORT_ONLY (default False): activa la CSP Report-Only del portal.
  - ADMIN_CSP_REPORT_ONLY (default False): activa la CSP Report-Only del admin.
"""
import logging
import uuid
from contextvars import ContextVar

from django.conf import settings

logger_seguridad = logging.getLogger('django.security')

# --- CORRELATION ID (SEC-LOG-002) ---
# ContextVar en vez de threading.local: gunicorn corre en modo sync (WSGI,
# no ASGI) así que ambos funcionarían igual aquí, pero ContextVar es la
# forma correcta si el proyecto adopta async más adelante — cada tarea
# async tiene su propia copia sin herencia entre requests concurrentes.
_correlation_id = ContextVar('correlation_id', default=None)


class CorrelationIdFilter(logging.Filter):
    """Inyecta el correlation ID del request actual en cada LogRecord, para
    que todas las líneas de log de una misma petición se puedan agrupar —
    incluidas las que emiten señales, servicios o código que no tiene
    acceso directo al `request` (solo al logger)."""

    def filter(self, record):
        record.correlation_id = _correlation_id.get() or '-'
        return True


class CorrelationIdMiddleware:
    """Genera un ID corto por request (nunca confía en uno que mande el
    cliente: aceptar `X-Correlation-ID` de fuera permitiría inyectar
    valores arbitrarios en el log, o que un cliente reutilice el mismo ID
    entre peticiones distintas para confundir la correlación) y lo deja
    disponible para el logging vía `CorrelationIdFilter`, y en la cabecera
    de la respuesta para poder cruzarlo con lo que ve el cliente."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        correlation_id = uuid.uuid4().hex[:12]
        token = _correlation_id.set(correlation_id)
        request.correlation_id = correlation_id
        try:
            response = self.get_response(request)
        finally:
            _correlation_id.reset(token)
        response['X-Correlation-ID'] = correlation_id
        return response

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

# --- CSP objetivo del admin, SOLO Report-Only. Los orígenes se sacaron
# auditando los templates reales (no adivinados): Jazzmin sirve todo su CSS/JS
# vendorizado (AdminLTE, Select2, FontAwesome) desde STATIC_URL ('self'),
# salvo Google Fonts (@import en admin_fix.css); los dashboards y calendarios
# del admin cargan FullCalendar y Chart.js desde jsDelivr; las miniaturas de
# imágenes (landing, producto) se sirven desde el dominio público de R2. Deja
# 'unsafe-inline' en script/style por lo mismo que PUBLIC_CSP y
# PORTAL_CSP_REPORT_ONLY_POLICY: Django admin y Jazzmin usan bastante inline
# y quitarlo sin refactorizar a nonces sería una CSP que rompe el panel, no
# defensa en profundidad real. ---
ADMIN_CSP_REPORT_ONLY_POLICY = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'self'; "
    "img-src 'self' data: https://media.quintakooxtanil.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "connect-src 'self'"
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

        # 4) CSP Report-Only (no bloquea) en el admin, opt-in.
        elif (
            getattr(settings, 'ADMIN_CSP_REPORT_ONLY', False)
            and path.startswith('/admin/')
            and not _tiene_csp(response)
        ):
            response['Content-Security-Policy-Report-Only'] = ADMIN_CSP_REPORT_ONLY_POLICY

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
