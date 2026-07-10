"""
Test del webhook de ManyChat — DESHABILITADO.

Elian ya no usa ManyChat, pero una automatización externa seguía llamando
este endpoint y creando cotizaciones con productos no solicitados (Paquete
Esencial + mobiliario/taquiza automáticos). Se deshabilitó el webhook en
comercial/views.py (responde 410 antes de ejecutar cualquier lógica).

Estos tests reemplazan a los que antes verificaban la lógica de fechas
del webhook (ya no aplica, esa lógica es inalcanzable) — ahora verifican
que el webhook esté realmente apagado: no crea nada, sin importar el
payload que reciba.
"""
import json

from django.test import TestCase, Client

from comercial.models import Cotizacion


class WebhookManyChatDeshabilitadoTest(TestCase):
    def _post_webhook(self, **payload_extra):
        payload = {
            'telefono_cliente': '9999999999',
            'nombre_cliente': 'PRUEBA WEBHOOK',
            'tipo_servicio': 'Evento',
            'tipo_evento': 'Cumpleaños',
            'fecha_tentativa': '01/12/2026',
            'num_personas': '120',
            'hora_inicio': '18:00',
            'hora_fin': '23:00',
        }
        payload.update(payload_extra)
        return Client().post(
            '/api/webhook-manychat/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_WEBHOOK_TOKEN='cualquier-cosa',
        )

    def test_webhook_responde_410_deshabilitado(self):
        resp = self._post_webhook()
        self.assertEqual(resp.status_code, 410)
        data = resp.json()
        self.assertEqual(data['status'], 'error')

    def test_webhook_no_crea_ninguna_cotizacion(self):
        """El fin de deshabilitarlo: que ya no cree cotizaciones con
        productos automáticos no solicitados."""
        antes = Cotizacion.objects.count()
        self._post_webhook()
        self.assertEqual(Cotizacion.objects.count(), antes)

    def test_webhook_deshabilitado_incluso_con_token_valido(self):
        """Aunque alguien conserve el token real, el webhook sigue sin
        procesar nada — se corta antes de verificar el token."""
        with self.settings(MANYCHAT_WEBHOOK_TOKEN='test-token'):
            resp = self._post_webhook()
            self.assertEqual(resp.status_code, 410)
