"""
Tests de seguridad del calendario unificado (comercial/views_calendario.py)
===========================================================================

Portados de `airbnb/test_seguridad.py` al retirar la app Airbnb (Issue #311):

  - SEC-XSS-001: el calendario interpolaba con |safe un json.dumps dentro de
    un <script>; un nombre con "</script>" —que el cotizador público acepta
    sin autenticación— inyectaba HTML en la sesión de staff. Desde la orden
    44 (SEC-DOS-001) los eventos viajan por un endpoint JSON, nunca
    embebidos en el HTML de la página.

  - SEC-DOS-001: `calendario_unificado_eventos` solo consulta el rango
    pedido (start/end), no el histórico completo.
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from comercial.models import Cliente, Cotizacion
from core_erp.test_utils import login_superuser_con_totp

PAYLOAD = '</script><script>window.PWNED=1</script>'


class CalendarioAdminXssTest(TestCase):
    """SEC-XSS-001 — el calendario no debe inyectar HTML de datos de usuario."""

    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username='staff_seguridad', password='Segura-190!', is_staff=True, is_superuser=True,
        )
        login_superuser_con_totp(self.client, self.staff)
        self.fecha_evento = date.today() + timedelta(days=15)

    def _cotizacion(self, nombre_cliente, nombre_evento):
        cliente = Cliente.objects.create(nombre=nombre_cliente, telefono='9990000000')
        return Cotizacion.objects.create(
            cliente=cliente,
            nombre_evento=nombre_evento,
            fecha_evento=self.fecha_evento,
            estado='COTIZADA',
        )

    def _eventos(self):
        # Rango que cubre self.fecha_evento con margen de sobra.
        inicio = (self.fecha_evento - timedelta(days=5)).isoformat()
        fin = (self.fecha_evento + timedelta(days=5)).isoformat()
        return self.client.get(reverse('calendario_unificado_eventos'), {'start': inicio, 'end': fin})

    def test_la_pagina_del_calendario_no_embebe_datos_de_eventos(self):
        # La vulnerabilidad original era interpolar datos de usuario dentro
        # de un <script> embebido en la página; ahora la página no trae
        # ningún evento, solo la URL del endpoint que FullCalendar consulta.
        self._cotizacion(PAYLOAD, 'Evento normal')

        cuerpo = self.client.get(reverse('calendario_unificado')).content.decode()

        self.assertNotIn('</script><script>', cuerpo)
        self.assertNotIn(PAYLOAD, cuerpo)
        self.assertIn('id="eventos-url"', cuerpo)

    def test_nombre_de_cliente_con_script_no_se_sirve_como_html(self):
        self._cotizacion(PAYLOAD, 'Evento normal')

        respuesta = self._eventos()

        self.assertEqual(respuesta.headers['Content-Type'], 'application/json')
        eventos = respuesta.json()
        self.assertTrue(any(PAYLOAD in e['title'] for e in eventos))

    def test_nombre_de_evento_con_script_no_se_sirve_como_html(self):
        # El cotizador público arma nombre_evento con texto del formulario,
        # así que este campo es tan controlable por el atacante como el otro.
        self._cotizacion('Cliente normal', PAYLOAD)

        respuesta = self._eventos()

        self.assertEqual(respuesta.headers['Content-Type'], 'application/json')
        eventos = respuesta.json()
        self.assertTrue(any(PAYLOAD in e['title'] for e in eventos))

    def test_el_calendario_sigue_entregando_los_eventos(self):
        self._cotizacion('Cliente Legítimo', 'Boda de prueba')

        respuesta = self._eventos()
        eventos = respuesta.json()

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(any('Boda de prueba' in e['title'] for e in eventos))


class CalendarioEventosRangoTest(TestCase):
    """SEC-DOS-001 — calendario_unificado_eventos solo consulta el rango
    pedido, no el histórico completo."""

    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username='staff_rango', password='Segura-190!', is_staff=True, is_superuser=True,
        )
        login_superuser_con_totp(self.client, self.staff)

    def _cotizacion(self, nombre_evento, fecha_evento):
        cliente = Cliente.objects.create(nombre='Cliente Rango', telefono='9990000001')
        return Cotizacion.objects.create(
            cliente=cliente, nombre_evento=nombre_evento, fecha_evento=fecha_evento, estado='COTIZADA',
        )

    def test_un_evento_fuera_del_rango_pedido_no_se_incluye(self):
        self._cotizacion('Dentro del rango', date(2027, 6, 15))
        self._cotizacion('Fuera del rango', date(2030, 1, 1))

        respuesta = self.client.get(
            reverse('calendario_unificado_eventos'), {'start': '2027-06-01', 'end': '2027-07-01'},
        )
        titulos = [e['title'] for e in respuesta.json()]

        self.assertTrue(any('Dentro del rango' in t for t in titulos))
        self.assertFalse(any('Fuera del rango' in t for t in titulos))

    def test_sin_start_o_end_responde_400_en_vez_de_devolver_todo(self):
        respuesta = self.client.get(reverse('calendario_unificado_eventos'))
        self.assertEqual(respuesta.status_code, 400)

    def test_staff_sin_permiso_recibe_403(self):
        staff_sin_permiso = get_user_model().objects.create_user(
            username='staff_sin_permiso_calendario', password='Segura-190!', is_staff=True,
        )
        self.client.force_login(staff_sin_permiso)

        respuesta = self.client.get(
            reverse('calendario_unificado_eventos'), {'start': '2027-06-01', 'end': '2027-07-01'},
        )
        self.assertEqual(respuesta.status_code, 403)
