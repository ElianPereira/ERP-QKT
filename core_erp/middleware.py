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

  4. CSP en modo Report-Only para el admin ("/admin/…"), opt-in vía
     ADMIN_CSP_REPORT_ONLY=True (orden 37, SEC-CFG-002). Mismo motivo que el
     portal: Jazzmin/AdminLTE, FullCalendar y Chart.js (jsDelivr) cargan
     recursos que una CSP bloqueante rompería sin antes ver qué falta en la
     lista blanca — nunca se activa en modo bloqueante sin haber recorrido el
     admin completo primero y ajustado la política a las violaciones reales.

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
from django.shortcuts import redirect
from django_otp import devices_for_user

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

# --- CSP objetivo del admin, SOLO Report-Only (no bloquea). Cubre lo que ya
# se sabe que carga el admin: FullCalendar y Chart.js vía jsDelivn (calendario
# unificado, dashboards de comercial/airbnb), Google Fonts (admin_fix.css) y
# las imágenes del bucket público (miniaturas de ImagenLanding/Producto). Se
# revisa y amplía con las violaciones reales antes de plantear una versión
# bloqueante — ver docstring del módulo. ---
ADMIN_CSP_REPORT_ONLY_POLICY = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'self'; "
    "form-action 'self'; "
    "img-src 'self' data: https://media.quintakooxtanil.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    # cdn.jsdelivr.net: FullCalendar/Chart.js. static.cloudflareinsights.com:
    # el beacon de Cloudflare Web Analytics, inyectado por el propio proxy de
    # Cloudflare delante de erp.quintakooxtanil.com — no es un script que
    # sirva el código del ERP, no se puede quitar desde acá sin desactivarlo
    # en el dashboard de Cloudflare. Detectado en la primera pasada real de
    # Report-Only (orden 37).
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://static.cloudflareinsights.com; "
    "connect-src 'self' https://cloudflareinsights.com"
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


_TOTP_RUTAS_EXENTAS = ('/admin/2fa/activar/', '/admin/2fa/verificar/', '/admin/logout/')


class SuperuserTOTPGateMiddleware:
    """SEC-AUTHN-002 (orden 42): ningún superusuario completa el login sin
    verificar un segundo factor (TOTP). `django_otp.middleware.OTPMiddleware`
    (justo antes en MIDDLEWARE) resuelve `request.user.is_verified()` — True
    solo si la sesión actual ya pasó por `django_otp.login()`. Para
    cualquier superusuario autenticado pero no verificado que pida una URL
    de `/admin/`, esta clase redirige a la pantalla de activación (sin
    dispositivo confirmado aún) o de verificación (ya tiene uno, falta el
    código de esta sesión) antes de dejarlo llegar a cualquier otra vista.

    Deliberadamente acotado a superusuarios: el staff de Ventas/Contabilidad/
    Nómina (is_staff sin is_superuser) no lleva TOTP, tal como pide la
    orden — es Dirección quien tiene acceso a todo el ERP sin restricción de
    grupo, así que es la cuenta cuyo robo tiene el impacto más alto.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, 'user', None)
        necesita_segundo_factor = (
            usuario is not None
            and usuario.is_authenticated
            and usuario.is_superuser
            and request.path.startswith('/admin/')
            and request.path not in _TOTP_RUTAS_EXENTAS
            and not usuario.is_verified()
        )
        if necesita_segundo_factor:
            destino = 'totp_verificar' if any(devices_for_user(usuario)) else 'totp_activar'
            return redirect(destino)
        return self.get_response(request)


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
