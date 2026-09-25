"""
El cotizador público nunca preguntó "¿física o moral?" — un cliente que
entraba con RFC de empresa (12 caracteres) quedaba marcado 'FISICA' (el
default del campo) para siempre, y `Cotizacion.calcular_totales()` nunca le
aplicaba la retención de ISR que le corresponde como persona moral.

Se detecta a partir de la longitud del RFC (regla del SAT). Desde que el
formulario pide el régimen fiscal (CFDI 4.0), además se valida que el
régimen corresponda a ese tipo de persona y se guarda el uso de CFDI
compatible con él.
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
            razon_social='EMPRESA SA DE CV', cp_fiscal='97000', regimen_fiscal='601',
        )
        self.assertEqual(respuesta.status_code, 200)

        cotizacion = Cotizacion.objects.latest('id')
        self.assertEqual(cotizacion.cliente.tipo_persona, 'MORAL')
        self.assertEqual(cotizacion.cliente.regimen_fiscal, '601')
        self.assertEqual(cotizacion.cliente.uso_cfdi, 'G03')
        # La retención de ISR de persona moral debe reflejarse en la
        # cotización creada con este mismo envío, no solo en un cliente
        # editado después.
        self.assertGreater(cotizacion.retencion_isr, Decimal('0.00'))

    def test_rfc_de_trece_caracteres_se_queda_como_fisica(self):
        respuesta = self._enviar(
            servicio='PASADIA', personas='10',
            requiere_factura=True, rfc='XAXX010101000',
            razon_social='Persona Física', cp_fiscal='97000', regimen_fiscal='612',
        )
        self.assertEqual(respuesta.status_code, 200)

        cotizacion = Cotizacion.objects.latest('id')
        self.assertEqual(cotizacion.cliente.tipo_persona, 'FISICA')
        self.assertEqual(cotizacion.retencion_isr, Decimal('0.00'))

    def test_el_regimen_declarado_en_la_web_se_guarda(self):
        """El régimen lo declara el cliente con su constancia: un reenvío con
        otro régimen lo actualiza (antes el ERP lo deducía y nunca lo pedía)."""
        datos = dict(
            servicio='PASADIA', personas='10', telefono='5215555550099',
            requiere_factura=True, rfc='ABC010101AB1',
            razon_social='EMPRESA SA DE CV', cp_fiscal='97000',
        )
        self.assertEqual(self._enviar(regimen_fiscal='601', **datos).status_code, 200)
        self.assertEqual(self._enviar(regimen_fiscal='603', **datos).status_code, 200)
        cliente = Cliente.objects.get(telefono__endswith='5555550099')
        self.assertEqual(cliente.regimen_fiscal, '603')

    def test_regimen_sin_obligaciones_guarda_uso_s01(self):
        """616 con G03 (los defaults de Cliente) es una combinación que el PAC
        rechaza: 616 solo admite S01."""
        respuesta = self._enviar(
            servicio='PASADIA', personas='10',
            requiere_factura=True, rfc='PEGJ800101AB1',
            razon_social='JUAN PEREZ GOMEZ', cp_fiscal='97000', regimen_fiscal='616',
        )
        self.assertEqual(respuesta.status_code, 200)
        cliente = Cotizacion.objects.latest('id').cliente
        self.assertEqual(cliente.regimen_fiscal, '616')
        self.assertEqual(cliente.uso_cfdi, 'S01')

    def test_rechaza_regimen_de_persona_fisica_con_rfc_de_empresa(self):
        respuesta = self._enviar(
            servicio='PASADIA', personas='10',
            requiere_factura=True, rfc='ABC010101AB1',
            razon_social='EMPRESA SA DE CV', cp_fiscal='97000', regimen_fiscal='612',
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(Cotizacion.objects.exists())

    def test_rechaza_datos_fiscales_incompletos(self):
        """Sin razón social, C.P. o régimen la solicitud caía en silencio a
        Público en General o salía un CFDI inválido."""
        base = dict(
            servicio='PASADIA', personas='10', requiere_factura=True,
            rfc='ABC010101AB1', razon_social='EMPRESA SA DE CV',
            cp_fiscal='97000', regimen_fiscal='601',
        )
        for campo, valor in (('razon_social', ''), ('cp_fiscal', '970'), ('regimen_fiscal', '')):
            with self.subTest(campo=campo):
                respuesta = self._enviar(**{**base, campo: valor})
                self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(Cotizacion.objects.exists())

    def test_sin_factura_no_exige_datos_fiscales(self):
        respuesta = self._enviar(servicio='PASADIA', personas='10', requiere_factura=False)
        self.assertEqual(respuesta.status_code, 200)
