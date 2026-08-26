"""
Orden 51 del backlog de seguridad (`SEC-TEST-001`, Issue #190): suite de
regresión de seguridad — autorización cruzada, XSS, CSRF, cabeceras,
expiración de sesión.

La mayor parte de las órdenes 1-18 ya quedó cubierta por tests dedicados,
escritos junto con cada corrección (no reinventados aquí para no duplicar
aserciones sin valor añadido). Este archivo consolida un índice de dónde
vive cada uno — para que "¿qué prueba SEC-AUTHZ-001c?" tenga una respuesta
de un solo lugar — y **añade** cobertura a los dos huecos reales que la
auditoría de esta orden encontró: `PublicSecurityHeadersMiddleware`
(CSP + Permissions-Policy) no tenía ningún test, y la configuración de
expiración de sesión por inactividad tampoco.

Índice de cobertura existente (órdenes 1-18):
  - SEC-XSS-001 / 001b (1, 2)  → airbnb/test_seguridad.py::CalendarioAdminXssTest
  - SEC-DATA-001 / 001b (5, 6) → airbnb/test_seguridad.py::FeedIcalTest
  - SEC-FILE-001b (8)          → core_erp/test_descargas.py
  - SEC-AUTHN-001a (9)         → core_erp/test_descargas.py (mensaje de error idéntico)
  - SEC-AUTHN-001b (10)        → comercial/test_seguridad_portal.py
  - SEC-AUTHN-001c (11)        → comercial/test_seguridad_portal.py
  - SEC-AUTHZ-001a-e (14-18)   → comercial/test_permisos_grupos.py
  - SEC-CSRF-001 (23)          → comercial/test_cotizador_seguridad.py
  - SEC-CFG-003 (41, cabeceras)→ core_erp/test_referrer_policy.py
  - SEC-LOG-001/002 (36, 45)   → core_erp/test_middleware.py

Ejecutar: python manage.py test core_erp.test_regresion_seguridad --verbosity=2
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core_erp.context_processors import session_idle

User = get_user_model()


class ExpiracionDeSesionTest(TestCase):
    """SEC-TEST-001 (orden 51): la sesión del admin/ERP debe caducar por
    inactividad, no permanecer viva indefinidamente. Un valor recuperado a
    su default (o eliminado) sería una regresión silenciosa: nada rompería
    a simple vista, la sesión simplemente dejaría de expirar."""

    def test_la_cookie_de_sesion_expira_por_inactividad_no_de_forma_indefinida(self):
        # Django trae 1209600s (2 semanas) como default de SESSION_COOKIE_AGE
        # si no se toca — el proyecto lo ata a SESSION_IDLE_TIMEOUT a propósito.
        self.assertEqual(settings.SESSION_COOKIE_AGE, settings.SESSION_IDLE_TIMEOUT)
        self.assertLessEqual(settings.SESSION_COOKIE_AGE, 1800)

    def test_el_reloj_de_inactividad_se_reinicia_en_cada_peticion(self):
        # Sin esto, SESSION_COOKIE_AGE se comporta como expiración ABSOLUTA
        # desde el login, no como timeout de INACTIVIDAD — el docstring de
        # settings.py promete lo segundo.
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)

    def test_la_sesion_no_sobrevive_al_cierre_del_navegador(self):
        self.assertTrue(settings.SESSION_EXPIRE_AT_BROWSER_CLOSE)

    def test_la_sesion_real_expira_al_valor_de_inactividad_configurado(self):
        # SESSION_EXPIRE_AT_BROWSER_CLOSE=True hace que la cookie viaje SIN
        # max-age propio (cookie de sesión de navegador) — el timeout de
        # inactividad real vive del lado del servidor, en la expiración de
        # la sesión guardada, no en un atributo de la cookie.
        usuario = User.objects.create_superuser(
            'sesion_test', 'sesion_test@quintakooxtanil.com', 'clave-de-prueba',
        )
        self.client.force_login(usuario)
        self.client.get('/admin/')

        self.assertEqual(self.client.session.get_expiry_age(), settings.SESSION_COOKIE_AGE)

    def test_el_context_processor_expone_el_mismo_valor_que_el_backend(self):
        # El auto-logout del navegador (JS) lee session_idle_minutes de la
        # plantilla — si se desincroniza de SESSION_IDLE_TIMEOUT, el aviso
        # al usuario y la expiración real dejan de coincidir.
        contexto = session_idle(None)
        minutos_esperados = settings.SESSION_IDLE_TIMEOUT // 60
        self.assertEqual(contexto['session_idle_minutes'], minutos_esperados)


class PublicSecurityHeadersMiddlewareTest(TestCase):
    """SEC-TEST-001 (orden 51): `PublicSecurityHeadersMiddleware`
    (core_erp/middleware.py) no tenía ningún test — una regresión que
    quitara la CSP de las páginas públicas o el Permissions-Policy general
    habría pasado inadvertida en CI."""

    @override_settings(PUBLIC_CSP_ENABLED=True, PUBLIC_CSP_REPORT_ONLY=False)
    def test_la_pagina_publica_lleva_csp_bloqueante(self):
        respuesta = self.client.get('/')

        self.assertIn('Content-Security-Policy', respuesta.headers)
        self.assertNotIn('Content-Security-Policy-Report-Only', respuesta.headers)
        self.assertIn("default-src 'self'", respuesta.headers['Content-Security-Policy'])
        self.assertIn("object-src 'none'", respuesta.headers['Content-Security-Policy'])

    @override_settings(PUBLIC_CSP_ENABLED=True, PUBLIC_CSP_REPORT_ONLY=True)
    def test_public_csp_report_only_cambia_la_cabecera_usada(self):
        respuesta = self.client.get('/')

        self.assertIn('Content-Security-Policy-Report-Only', respuesta.headers)
        self.assertNotIn('Content-Security-Policy', respuesta.headers)

    @override_settings(PUBLIC_CSP_ENABLED=False)
    def test_public_csp_enabled_en_false_no_manda_ninguna_csp(self):
        respuesta = self.client.get('/')

        self.assertNotIn('Content-Security-Policy', respuesta.headers)
        self.assertNotIn('Content-Security-Policy-Report-Only', respuesta.headers)

    def test_permissions_policy_va_en_toda_respuesta_publica_y_de_admin(self):
        respuesta_publica = self.client.get('/')
        self.assertIn('Permissions-Policy', respuesta_publica.headers)

        respuesta_admin = self.client.get('/admin/login/')
        self.assertIn('Permissions-Policy', respuesta_admin.headers)

    def test_admin_csp_report_only_desactivada_por_default_no_manda_nada(self):
        # ADMIN_CSP_REPORT_ONLY default es False (orden 37, SEC-CFG-002):
        # el admin no debe llevar ninguna CSP mientras no se active a propósito.
        respuesta = self.client.get('/admin/login/')

        self.assertNotIn('Content-Security-Policy', respuesta.headers)
        self.assertNotIn('Content-Security-Policy-Report-Only', respuesta.headers)

    @override_settings(ADMIN_CSP_REPORT_ONLY=True)
    def test_admin_csp_report_only_activada_manda_report_only_no_bloqueante(self):
        respuesta = self.client.get('/admin/login/')

        self.assertIn('Content-Security-Policy-Report-Only', respuesta.headers)
        self.assertNotIn('Content-Security-Policy', respuesta.headers)
        cabecera = respuesta.headers['Content-Security-Policy-Report-Only']
        self.assertIn("default-src 'self'", cabecera)
        self.assertIn('https://cdn.jsdelivr.net', cabecera)

        # No debe filtrarse a rutas fuera de /admin/.
        respuesta_publica = self.client.get('/')
        self.assertNotIn('Content-Security-Policy-Report-Only', respuesta_publica.headers)
