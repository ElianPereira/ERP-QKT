"""
Tests del backlog de seguridad (Issue #190), orden 36 (SEC-LOG-001):
logger `django.security` declarado explícitamente, y
`AuthorizationAuditMiddleware` registra cada 403 de autorización con
usuario y ruta. También orden 45 (SEC-LOG-002): `CorrelationIdMiddleware`
añade un ID por petición, presente en la cabecera de la respuesta y en
cualquier log emitido durante el procesamiento del request.

Ejecutar: python manage.py test core_erp.test_middleware --verbosity=2
"""
import logging

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core_erp.middleware import CorrelationIdFilter
from core_erp.test_utils import login_superuser_con_totp

User = get_user_model()


class _HandlerDePrueba(logging.Handler):
    """Handler mínimo que solo junta los LogRecord recibidos, para poder
    inspeccionar los atributos que le inyectó un filtro (assertLogs no sirve
    para esto: instala su propio handler sin los filtros de producción)."""

    def __init__(self):
        super().__init__()
        self.registros = []

    def emit(self, record):
        self.registros.append(record)


class AuthorizationAuditMiddlewareTest(TestCase):
    def test_403_de_autorizacion_queda_registrado_con_usuario_y_ruta(self):
        staff = User.objects.create_user(
            'staff_sin_permiso', 'staff@quintakooxtanil.com', 'clave-de-prueba', is_staff=True,
        )
        self.client.force_login(staff)
        url = reverse('importar_historico')

        with self.assertLogs('django.security', level='WARNING') as logs:
            respuesta = self.client.get(url)

        self.assertEqual(respuesta.status_code, 403)
        salida = '\n'.join(logs.output)
        self.assertIn('staff_sin_permiso', salida)
        self.assertIn(url, salida)

    def test_una_respuesta_no_403_no_genera_registro(self):
        superusuario = User.objects.create_superuser(
            'jefa_middleware', 'jefa_middleware@quintakooxtanil.com', 'clave-de-prueba',
        )
        login_superuser_con_totp(self.client, superusuario)
        url = reverse('importar_historico')

        with self.assertNoLogs('django.security', level='WARNING'):
            respuesta = self.client.get(url)

        self.assertEqual(respuesta.status_code, 200)


class CorrelationIdMiddlewareTest(TestCase):
    def test_la_respuesta_incluye_un_correlation_id(self):
        respuesta = self.client.get('/admin/login/')
        self.assertIn('X-Correlation-ID', respuesta.headers)
        self.assertTrue(respuesta.headers['X-Correlation-ID'])

    def test_cada_peticion_tiene_un_correlation_id_distinto(self):
        primera = self.client.get('/admin/login/')
        segunda = self.client.get('/admin/login/')
        self.assertNotEqual(
            primera.headers['X-Correlation-ID'], segunda.headers['X-Correlation-ID'],
        )

    def test_el_log_emitido_durante_el_request_lleva_el_mismo_id_que_la_cabecera(self):
        staff = User.objects.create_user(
            'staff_correlation', 'staff_correlation@quintakooxtanil.com', 'clave-de-prueba', is_staff=True,
        )
        self.client.force_login(staff)
        url = reverse('importar_historico')

        handler = _HandlerDePrueba()
        handler.addFilter(CorrelationIdFilter())
        logger_seguridad = logging.getLogger('django.security')
        logger_seguridad.addHandler(handler)
        try:
            respuesta = self.client.get(url)
        finally:
            logger_seguridad.removeHandler(handler)

        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(len(handler.registros), 1)
        self.assertEqual(handler.registros[0].correlation_id, respuesta.headers['X-Correlation-ID'])


@override_settings(ALLOWED_HOSTS=['erp.quintakooxtanil.com'])
class DisallowedHostQuedaRegistradoTest(TestCase):
    """Criterio de aceptación del backlog: una petición con Host inválido
    produce una línea identificable en el logger django.security — ya
    viene de fábrica en Django (django.security.DisallowedHost), esto solo
    confirma que declarar el logger explícitamente no lo silencia."""

    def test_host_no_permitido_queda_en_el_logger_de_seguridad(self):
        with self.assertLogs('django.security', level='ERROR') as logs:
            respuesta = self.client.get('/', HTTP_HOST='host-no-permitido.example.com')

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('host-no-permitido.example.com', '\n'.join(logs.output))
