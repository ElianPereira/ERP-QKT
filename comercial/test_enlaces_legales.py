"""
Los documentos legales deben ser alcanzables desde las superficies públicas.

Requisito de la validación técnica de Openpay: el aviso de privacidad y los
términos y condiciones tienen que estar enlazados donde el cliente captura
datos o paga, no solo existir en una URL suelta.
"""

from django.test import TestCase
from django.urls import reverse


class EnlacesLegalesEnCotizadorTest(TestCase):
    def test_cotizador_publico_enlaza_los_documentos_legales(self):
        response = self.client.get(reverse('cotizador_publico'))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        for nombre in ('legal:aviso_privacidad', 'legal:terminos',
                       'legal:politica_cancelacion'):
            self.assertIn(reverse(nombre), html, f'Falta el enlace a {nombre}')
