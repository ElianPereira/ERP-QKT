"""
Tests de seguridad del módulo Airbnb
====================================

Regresiones de dos hallazgos de la auditoría del Issue #190:

  - SEC-XSS-001: el calendario del admin interpolaba con |safe el resultado
    de json.dumps dentro de un <script>. json.dumps no escapa <, > ni &, así
    que un nombre con "</script>" —que el cotizador público acepta sin
    autenticación— cerraba el bloque e inyectaba HTML en la sesión de staff.

  - SEC-DATA-001: el feed iCal se saltaba la validación entera cuando
    ICAL_PUBLIC_TOKEN no estaba configurado, y publicaba el nombre de cada
    cliente con su evento, sus asistentes y su fecha.
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from comercial.models import Cliente, Cotizacion

PAYLOAD = '</script><script>window.PWNED=1</script>'
TOKEN = 'token-de-prueba-para-el-feed-ical'


class CalendarioAdminXssTest(TestCase):
    """SEC-XSS-001 — el calendario no debe inyectar HTML de datos de usuario."""

    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username='staff_seguridad', password='Segura-190!', is_staff=True, is_superuser=True,
        )
        self.client.force_login(self.staff)

    def _cotizacion(self, nombre_cliente, nombre_evento):
        cliente = Cliente.objects.create(nombre=nombre_cliente, telefono='9990000000')
        return Cotizacion.objects.create(
            cliente=cliente,
            nombre_evento=nombre_evento,
            fecha_evento=date.today() + timedelta(days=15),
            estado='COTIZADA',
        )

    def _afirmar_payload_neutralizado(self, cuerpo):
        """El texto del atacante puede aparecer —es el nombre que se muestra—
        pero nunca como marcado ejecutable: json_script escapa < y > a \\u003C
        y \\u003E, así que el </script> del payload no cierra nada."""
        self.assertNotIn('</script><script>', cuerpo)
        self.assertIn('\\u003C/script\\u003E', cuerpo)
        self.assertIn('id="eventos-data" type="application/json"', cuerpo)

    def test_nombre_de_cliente_con_script_no_rompe_el_bloque(self):
        self._cotizacion(PAYLOAD, 'Evento normal')

        cuerpo = self.client.get(reverse('calendario_unificado')).content.decode()

        self._afirmar_payload_neutralizado(cuerpo)

    def test_nombre_de_evento_con_script_no_rompe_el_bloque(self):
        # El cotizador público arma nombre_evento con texto del formulario,
        # así que este campo es tan controlable por el atacante como el otro.
        self._cotizacion('Cliente normal', PAYLOAD)

        cuerpo = self.client.get(reverse('calendario_unificado')).content.decode()

        self._afirmar_payload_neutralizado(cuerpo)

    def test_el_calendario_sigue_entregando_los_eventos(self):
        # Que no se rompa el escapado a costa de dejar de renderizar.
        self._cotizacion('Cliente Legítimo', 'Boda de prueba')

        respuesta = self.client.get(reverse('calendario_unificado'))
        cuerpo = respuesta.content.decode()

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('id="eventos-data"', cuerpo)
        self.assertIn('Boda de prueba', cuerpo)


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
        for _ in range(120):
            self.assertNotEqual(self.client.get(self.url).status_code, 429)
        self.assertEqual(self.client.get(self.url).status_code, 429)
