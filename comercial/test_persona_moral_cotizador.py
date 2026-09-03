"""
El cotizador público nunca preguntó "¿física o moral?" — un cliente que
entraba con RFC de empresa (12 caracteres) quedaba marcado 'FISICA' (el
default del campo) para siempre, y `Cotizacion.calcular_totales()` nunca le
aplicaba la retención de ISR que le corresponde como persona moral.

Se detecta ahora a partir de la longitud del RFC (regla del SAT), sin
agregar ninguna pregunta nueva al formulario público.
"""
import json
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from comercial.models import Cliente, Cotizacion
from comercial.test_cotizador_lineas import _crear_catalogo, _payload
from comunicacion.tests.utils import RespuestaFalsa, limpiar_cache_emisor


class DeteccionPersonaMoralCotizadorTest(TestCase):

    def setUp(self):
        limpiar_cache_emisor()
        cache.clear()
        _crear_catalogo()

    def _enviar(self, **extra):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()), \
             patch('comunicacion.services.numero_emisor_wa', return_value='5215555550003'):
            return self.client.post(
                reverse('cotizador_enviar'),
                data=json.dumps(_payload(**extra)),
                content_type='application/json',
            )

    def test_rfc_de_doce_caracteres_marca_al_cliente_como_moral(self):
        respuesta = self._enviar(
            servicio='PASADIA', personas='10',
            requiere_factura=True, rfc='ABC010101AB1',
            razon_social='EMPRESA SA DE CV',
        )
        self.assertEqual(respuesta.status_code, 200)

        cotizacion = Cotizacion.objects.latest('id')
        self.assertEqual(cotizacion.cliente.tipo_persona, 'MORAL')
        # 616 no aplica a persona moral: sin que el formulario haya pedido
        # régimen, se le asigna 601 (General de Ley Personas Morales) en
        # vez de dejar el default de persona física.
        self.assertEqual(cotizacion.cliente.regimen_fiscal, '601')
        # La retención de ISR de persona moral debe reflejarse en la
        # cotización creada con este mismo envío, no solo en un cliente
        # editado después.
        self.assertGreater(cotizacion.retencion_isr, Decimal('0.00'))

    def test_rfc_de_trece_caracteres_se_queda_como_fisica(self):
        respuesta = self._enviar(
            servicio='PASADIA', personas='10',
            requiere_factura=True, rfc='XAXX010101000',
            razon_social='Persona Física',
        )
        self.assertEqual(respuesta.status_code, 200)

        cotizacion = Cotizacion.objects.latest('id')
        self.assertEqual(cotizacion.cliente.tipo_persona, 'FISICA')
        self.assertEqual(cotizacion.retencion_isr, Decimal('0.00'))

    def test_no_pisa_un_tipo_de_persona_ya_corregido_a_mano(self):
        """
        Si alguien del equipo ya marcó el tipo de persona correcto en el
        admin (por ejemplo, tras corregir un dato capturado mal), un
        reenvío del mismo cliente no debe revertirlo silenciosamente si el
        RFC no cambió — el auto-detección solo actúa cuando corrige un
        mismatch real (RFC de 12 vs 13), nunca en vano.
        """
        respuesta = self._enviar(
            servicio='PASADIA', personas='10', telefono='5215555550099',
            requiere_factura=True, rfc='ABC010101AB1',
            razon_social='EMPRESA SA DE CV',
        )
        self.assertEqual(respuesta.status_code, 200)
        cliente = Cliente.objects.get(telefono__endswith='5555550099')
        self.assertEqual(cliente.tipo_persona, 'MORAL')

        # Segundo envío del mismo cliente/RFC: no debe volver a tocar el
        # régimen fiscal si alguien ya lo hubiera corregido a mano.
        cliente.regimen_fiscal = '603'
        cliente.save(update_fields=['regimen_fiscal'])

        self._enviar(
            servicio='PASADIA', personas='10', telefono='5215555550099',
            requiere_factura=True, rfc='ABC010101AB1',
            razon_social='EMPRESA SA DE CV',
        )
        cliente.refresh_from_db()
        self.assertEqual(cliente.regimen_fiscal, '603')
