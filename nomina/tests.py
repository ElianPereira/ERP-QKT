from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse


class WebhookSyncJibbleRateLimitTest(TestCase):
    """SEC-RL-001c (backlog orden 21, parte 3 de 3): el webhook de sincronización
    con Jibble, ~120/min.

    Se cuenta antes de validar el Bearer token, así que peticiones sin
    credenciales (401) también agotan el cupo — es justo lo que se quiere
    contra la fuerza bruta del token.
    """

    def setUp(self):
        cache.clear()
        self.url = reverse('webhook_sync_jibble')

    def test_bloquea_tras_ciento_veinte_peticiones(self):
        for _ in range(120):
            respuesta = self.client.post(self.url, data='{}', content_type='application/json')
            self.assertNotEqual(respuesta.status_code, 429)
        respuesta = self.client.post(self.url, data='{}', content_type='application/json')
        self.assertEqual(respuesta.status_code, 429)


@override_settings(NOMINA_CRON_TOKEN='token-de-prueba')
class WebhookSyncJibbleErrorGenericoTest(TestCase):
    """SEC-INFO-001 (backlog orden 25): un error inesperado no debe filtrar
    el texto crudo de la excepción en el cuerpo de la respuesta pública."""

    def setUp(self):
        cache.clear()
        self.url = reverse('webhook_sync_jibble')

    def _post(self):
        return self.client.post(
            self.url, data='{}', content_type='application/json',
            HTTP_AUTHORIZATION='Bearer token-de-prueba',
        )

    def test_error_inesperado_no_filtra_el_detalle_pero_queda_en_el_log(self):
        mensaje_interno = 'conexión perdida a la tabla recibonomina_jibble_cache'
        with patch('nomina.services.JibbleService.esta_configurado', return_value=True), \
             patch('nomina.services.JibbleService.autenticar',
                   side_effect=RuntimeError(mensaje_interno)), \
             self.assertLogs('nomina.views', level='ERROR') as logs:
            respuesta = self._post()

        self.assertEqual(respuesta.status_code, 500)
        cuerpo = respuesta.json()
        self.assertNotIn(mensaje_interno, str(cuerpo))
        self.assertEqual(cuerpo['error'], 'Error inesperado al sincronizar con Jibble.')
        self.assertIn(mensaje_interno, '\n'.join(logs.output))
