"""
Orden 13 del backlog de seguridad (`NV-07`): quién recibe una alerta cuando
el ERP genera un error 500 sin capturar, y por qué canal. Decisión del
propietario: solo él (`ADMIN_ALERT_EMAIL`, cae a `SERVER_EMAIL` si no está
configurada), por correo — reutilizando el backend de Brevo ya configurado
vía el handler estándar de Django (`AdminEmailHandler`), sin dependencias
nuevas.

Se prueba contra el logger real (`django.request`, el mismo que usa
`django.core.handlers.exception.response_for_exception()` para cada 500) en
vez de tumbar una vista de verdad: es la pieza que se tocó (el cableado en
`LOGGING`), y así la prueba no depende de mantener viva una URL rota a
propósito solo para el test.
"""
import logging
import sys

from django.conf import settings
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings

request_logger = logging.getLogger('django.request')


def _log_error_500(path):
    try:
        raise ValueError('error simulado para la prueba')
    except ValueError:
        exc_info = sys.exc_info()
    request_logger.error(
        'Internal Server Error: %s', path,
        exc_info=exc_info,
        extra={'status_code': 500, 'request': RequestFactory().get(path)},
    )


class AlertaAdminsAnteError500Test(TestCase):

    @override_settings(ADMINS=[('Prueba', 'admin-prueba@quintakooxtanil.com')])
    def test_un_error_500_envia_correo_a_admins(self):
        _log_error_500('/ruta-que-revento/')

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('admin-prueba@quintakooxtanil.com', mail.outbox[0].to)
        self.assertIn('Internal Server Error', mail.outbox[0].subject)
        self.assertIn('[ERP QKT]', mail.outbox[0].subject)

    @override_settings(ADMINS=[])
    def test_sin_admins_configurados_no_envia_ni_falla(self):
        # mail_admins() es un no-op silencioso con ADMINS vacío — confirma
        # que el handler no revienta el request si un día se deja sin valor.
        _log_error_500('/ruta-que-revento/')

        self.assertEqual(len(mail.outbox), 0)

    @override_settings(ADMINS=[('Prueba', 'admin-prueba@quintakooxtanil.com')])
    def test_un_error_por_debajo_de_500_no_envia_correo(self):
        request_logger.warning(
            'Not Found: %s', '/no-existe/',
            extra={'status_code': 404, 'request': RequestFactory().get('/no-existe/')},
        )

        self.assertEqual(len(mail.outbox), 0)

    def test_admins_tiene_al_menos_un_correo_valido_configurado(self):
        # No se compara contra un valor fijo: ADMIN_ALERT_EMAIL puede venir
        # del entorno real (Railway) o caer al default (SERVER_EMAIL) — lo
        # que importa es que la orden 13 nunca quede con ADMINS vacío.
        self.assertEqual(len(settings.ADMINS), 1)
        self.assertIn('@', settings.ADMINS[0][1])

    def test_el_logger_django_tiene_el_handler_mail_admins_enganchado(self):
        # Prueba estructural: si alguien reordena LOGGING y desengancha
        # mail_admins del logger 'django' (el padre de 'django.request', que
        # es donde Django registra cada 500), esta orden queda silenciosamente
        # revertida sin que ningún otro test lo note.
        handlers_de_django = settings.LOGGING['loggers']['django']['handlers']
        self.assertIn('mail_admins', handlers_de_django)
        self.assertEqual(
            settings.LOGGING['handlers']['mail_admins']['class'],
            'django.utils.log.AdminEmailHandler',
        )
