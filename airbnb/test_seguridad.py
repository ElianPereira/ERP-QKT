"""
Tests de seguridad del módulo Airbnb
====================================

Regresiones de tres hallazgos de la auditoría del Issue #190:

  - SEC-XSS-001: el calendario del admin interpolaba con |safe el resultado
    de json.dumps dentro de un <script>. json.dumps no escapa <, > ni &, así
    que un nombre con "</script>" —que el cotizador público acepta sin
    autenticación— cerraba el bloque e inyectaba HTML en la sesión de staff.
    Desde la orden 44 (SEC-DOS-001) los eventos ya no viajan embebidos en el
    HTML de la página: la vista solo entrega la URL de un endpoint JSON que
    FullCalendar consulta por AJAX, así que la clase de vulnerabilidad
    (romper un bloque <script>) ya no aplica a ese endpoint — se sirve como
    `application/json`, nunca interpretado como HTML/JS por el navegador.

  - SEC-DATA-001: el feed iCal se saltaba la validación entera cuando
    ICAL_PUBLIC_TOKEN no estaba configurado, y publicaba el nombre de cada
    cliente con su evento, sus asistentes y su fecha.

  - SEC-DOS-001: `calendario_unificado` consultaba el histórico completo de
    cotizaciones/reservas/asignaciones en cada carga de página. Ahora solo
    consulta el rango que pide `calendario_unificado_eventos` (start/end).
"""
import time
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from comercial.models import Cliente, Cotizacion
from core_erp.test_utils import login_superuser_con_totp

PAYLOAD = '</script><script>window.PWNED=1</script>'
TOKEN = 'token-de-prueba-para-el-feed-ical'


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


@override_settings(ICAL_PUBLIC_TOKEN=TOKEN)
class FeedIcalTest(TestCase):
    """SEC-DATA-001 — el feed exige token y no publica datos personales."""

    def setUp(self):
        self.cliente = Cliente.objects.create(nombre='ANDREA PEREZ', telefono='9991112233')
        self.cotizacion = Cotizacion.objects.create(
            cliente=self.cliente,
            nombre_evento='Boda de Andrea',
            fecha_evento=date.today() + timedelta(days=20),
            estado='CONFIRMADA',
            num_personas=137,
        )
        self.url = reverse('airbnb:ical_eventos')

    @override_settings(ICAL_PUBLIC_TOKEN='')
    def test_sin_token_configurado_rechaza(self):
        # El fallo original: sin la variable, la validación entera se saltaba
        # y el feed quedaba abierto a internet.
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_token_incorrecto_rechaza(self):
        respuesta = self.client.get(self.url, {'token': 'otro-token'})

        self.assertEqual(respuesta.status_code, 403)

    def test_token_ausente_rechaza(self):
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_token_correcto_entrega_el_calendario(self):
        respuesta = self.client.get(self.url, {'token': TOKEN})

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('BEGIN:VCALENDAR', respuesta.content.decode())

    def test_el_feed_no_publica_datos_del_cliente(self):
        cuerpo = self.client.get(self.url, {'token': TOKEN}).content.decode()

        # DTSTAMP y CREATED llevan la hora actual, y buscar el número de
        # invitados (137) sobre el cuerpo completo choca con ella cada vez que
        # esos dígitos caen seguidos en la marca de tiempo — p. ej.
        # `20260814T22`0137`Z`. Se comprueba sobre el resto del feed, que es
        # donde un dato del cliente podría filtrarse de verdad.
        sin_marcas_de_tiempo = '\n'.join(
            linea for linea in cuerpo.splitlines()
            if not linea.startswith(('DTSTAMP', 'CREATED'))
        )

        self.assertNotIn('ANDREA PEREZ', sin_marcas_de_tiempo)
        self.assertNotIn('Boda de Andrea', sin_marcas_de_tiempo)
        self.assertNotIn('Cliente:', sin_marcas_de_tiempo)
        self.assertNotIn('137', sin_marcas_de_tiempo)
        # Sigue bloqueando la fecha, que es para lo único que existe el feed.
        self.assertIn('BEGIN:VEVENT', cuerpo)
        self.assertIn(f'COT-{self.cotizacion.id:03d}', cuerpo)

    def test_un_nombre_con_saltos_de_linea_no_inyecta_propiedades(self):
        # SEC-INJ-001: al no interpolar texto libre ya no hay CRLF que escapar.
        self.cotizacion.nombre_evento = 'Boda\r\nSUMMARY:INYECTADO'
        self.cotizacion.save(update_fields=['nombre_evento'])

        cuerpo = self.client.get(self.url, {'token': TOKEN}).content.decode()

        self.assertEqual(cuerpo.count('BEGIN:VEVENT'), 1)
        self.assertEqual(cuerpo.count('SUMMARY:'), 1)
        self.assertNotIn('INYECTADO', cuerpo)


@override_settings(ICAL_PUBLIC_TOKEN=TOKEN)
class FeedIcalRateLimitTest(TestCase):
    """SEC-RL-001c (backlog orden 21, parte 2 de 3): el feed iCal, ~120/min.

    Se cuenta antes de validar el token, así que peticiones sin token
    (403) también agotan el cupo.
    """

    def setUp(self):
        cache.clear()
        self.url = reverse('airbnb:ical_eventos')

    def test_bloquea_tras_ciento_veinte_peticiones(self):
        # Fija el reloj del rate limiter: sin esto, el bucle de 120
        # peticiones puede cruzar de ventana de 60s a mitad de camino y
        # dejar la petición 121 con el cupo vuelto a cero (backlog orden 21).
        inicio_ventana = int(time.time() // 60) * 60
        with patch('core_erp.ratelimit.time.time', return_value=inicio_ventana):
            for _ in range(120):
                self.assertNotEqual(self.client.get(self.url).status_code, 429)
            self.assertEqual(self.client.get(self.url).status_code, 429)
