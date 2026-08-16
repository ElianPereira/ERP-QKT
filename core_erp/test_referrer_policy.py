"""
Tests del backlog de seguridad (Issue #190), orden 41 (SEC-CFG-003):
SECURE_REFERRER_POLICY explícita para no filtrar el token del portal del
cliente por la cabecera Referer hacia un sitio de otro origen.

Ejecutar: python manage.py test core_erp.test_referrer_policy --verbosity=2
"""
from datetime import date, timedelta

from django.test import TestCase

from comercial.models import Cliente, Cotizacion, PortalCliente


class ReferrerPolicyTest(TestCase):
    def setUp(self):
        cliente = Cliente.objects.create(nombre='Cliente RP', telefono='9990001122')
        cotizacion = Cotizacion.objects.create(
            cliente=cliente,
            nombre_evento='Evento RP',
            fecha_evento=date.today() + timedelta(days=30),
        )
        self.portal = PortalCliente.objects.get(cotizacion=cotizacion)

    def test_portal_evento_incluye_la_cabecera_referrer_policy(self):
        respuesta = self.client.get(f'/mi-evento/{self.portal.token}/')
        self.assertEqual(respuesta.headers['Referrer-Policy'], 'strict-origin-when-cross-origin')

    def test_una_pagina_publica_cualquiera_tambien_la_incluye(self):
        respuesta = self.client.get('/')
        self.assertEqual(respuesta.headers['Referrer-Policy'], 'strict-origin-when-cross-origin')
