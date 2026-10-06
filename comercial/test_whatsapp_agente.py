from django.test import TestCase

from comercial.views_portal import WA_AGENTE_URL


class WhatsappAgentePaginaTest(TestCase):
    def test_pagina_real_con_boton_al_agente(self):
        for ruta in ('/whatsapp/', '/contacto/'):
            response = self.client.get(ruta)
            # 200 y no redirect: Meta rechaza el anuncio si el destino termina en wa.me.
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, f'href="{WA_AGENTE_URL}"')
