"""
Tests del backlog de seguridad (Issue #190), orden 36 (SEC-LOG-001):
logger `django.security` declarado explícitamente, y
`AuthorizationAuditMiddleware` registra cada 403 de autorización con
usuario y ruta.

Ejecutar: python manage.py test core_erp.test_middleware --verbosity=2
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

User = get_user_model()


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
        self.client.force_login(superusuario)
        url = reverse('importar_historico')

        with self.assertNoLogs('django.security', level='WARNING'):
            respuesta = self.client.get(url)

        self.assertEqual(respuesta.status_code, 200)


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
