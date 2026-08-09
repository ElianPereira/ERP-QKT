"""
La pantalla de gracias recibe el destino por query string (`?portal=`) y lo
vuelca en un `href` y en un `window.location`. Sin validarlo, un
`javascript:...` da XSS y cualquier dominio ajeno la convierte en redirección
abierta sobre un dominio de confianza, justo después de que el cliente envía
sus datos.

Solo se acepta si cuelga de PORTAL_URL. Estas pruebas fijan ese contrato para
que nadie lo relaje sin darse cuenta.
"""

from django.test import TestCase, override_settings
from django.urls import reverse

PORTAL = 'https://portal.example.com'


@override_settings(PORTAL_URL=PORTAL)
class PortalUrlDeGraciasTest(TestCase):
    def _portal_url(self, **params):
        response = self.client.get(reverse('cotizador_gracias'), params)
        self.assertEqual(response.status_code, 200)
        return response

    def test_acepta_el_enlace_legitimo_del_portal(self):
        """El que emite `cotizador_enviar`: {PORTAL_URL}/mi-evento/{token}/."""
        destino = f'{PORTAL}/mi-evento/abc123/'
        response = self._portal_url(portal=destino)

        self.assertEqual(response.context['portal_url'], destino)

    def test_descarta_el_esquema_javascript(self):
        response = self._portal_url(portal='javascript:alert(1)')

        self.assertEqual(response.context['portal_url'], PORTAL)
        self.assertNotContains(response, 'javascript:')

    def test_descarta_un_dominio_ajeno(self):
        response = self._portal_url(portal='https://atacante.mx/phishing/')

        self.assertEqual(response.context['portal_url'], PORTAL)
        self.assertNotContains(response, 'atacante.mx')

    def test_descarta_un_dominio_que_solo_empieza_igual(self):
        """
        `https://portal.example.com.atacante.mx/` comparte prefijo con
        PORTAL_URL. Lo que lo frena es la barra del final del prefijo; sin
        ella este caso pasaría el filtro.
        """
        response = self._portal_url(portal=f'{PORTAL}.atacante.mx/phishing/')

        self.assertEqual(response.context['portal_url'], PORTAL)
        self.assertNotContains(response, 'atacante.mx')

    def test_sin_parametro_cae_al_portal_generico(self):
        response = self._portal_url()

        self.assertEqual(response.context['portal_url'], PORTAL)

    def test_no_queda_rastro_del_subdominio_retirado(self):
        """`clientes.quintakooxtanil.com` ya no existe en DNS."""
        response = self._portal_url()

        self.assertNotContains(response, 'clientes.quintakooxtanil.com')

    def test_la_plantilla_no_autoredirige_al_portal_generico(self):
        """
        La comparación del JS es contra `portal_base`: sin una cotización
        concreta que enseñar, no debe mandar a nadie a ningún lado.
        """
        response = self._portal_url()

        self.assertEqual(response.context['portal_base'], PORTAL)
        self.assertEqual(
            response.context['portal_url'], response.context['portal_base'],
        )
