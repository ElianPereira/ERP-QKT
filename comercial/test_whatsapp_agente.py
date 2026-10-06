from django.test import TestCase

from comercial.views_portal import WA_AGENTE_URL


class WhatsappAgenteRedirectTest(TestCase):
    def test_redirige_al_chat_del_agente(self):
        for ruta in ('/whatsapp/', '/whatsapp'):
            response = self.client.get(ruta, follow=False)
            if response.status_code == 301:  # APPEND_SLASH
                response = self.client.get(response['Location'])
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response['Location'], WA_AGENTE_URL)
