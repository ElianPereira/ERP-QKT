from django.core.cache import cache
from django.test import TestCase
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
