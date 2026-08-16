"""
Tests del backlog de seguridad (Issue #190), órdenes 19-21 (SEC-RL-001a/b/c):
rate limiting en descargas del portal, APIs públicas del cotizador y el
webhook de Openpay. El feed iCal (airbnb) y el webhook de Jibble (nomina)
se prueban en sus propias apps.

Ejecutar: python manage.py test comercial.test_rate_limit_publico --verbosity=2
"""
import time
from datetime import date, timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from comercial.models import Cliente, Cotizacion, ItemCotizacion, PortalCliente


def _inicio_ventana():
    """Ancla del reloj del rate limiter, calculada al momento de usarse.

    Fijar el reloj evita que el bucket (`int(time.time() // window)`) cruce
    de ventana a mitad del bucle de peticiones — visto tanto en local como
    en CI real (backlog órdenes 19-21). Pero el valor tiene que calcularse
    **en cada test, justo antes de usarlo**, no una sola vez al importar el
    módulo: Django's DatabaseCache calcula la expiración de cada entrada con
    `time.time() + timeout` usando el mismo `time.time()` parcheado, así que
    si el valor congelado quedara desfasado del reloj real por más que el
    timeout (~120s) — como pasa si el módulo se importa al arrancar una
    suite de cientos de tests y este test corre varios minutos después — la
    entrada nace "expirada" y el conteo nunca avanza. Mismo patrón que ya
    usa `core_erp/test_ratelimit.py`.
    """
    return int(time.time() // 60) * 60


def _crear_cotizacion():
    cliente = Cliente.objects.create(nombre='Cliente RL', telefono='9990001122')
    cotizacion = Cotizacion.objects.create(
        cliente=cliente,
        nombre_evento='Evento RL',
        fecha_evento=date.today() + timedelta(days=45),
    )
    ItemCotizacion.objects.create(
        cotizacion=cotizacion, descripcion='Servicio', cantidad=1, precio_unitario=1000,
    )
    return cotizacion


class PortalDescargasRateLimitTest(TestCase):
    """Orden 19: portal_evento y las 3 descargas del portal, ~10/min.

    El decorador cuenta antes de ejecutar el cuerpo de la vista, así que no
    hace falta que exista contrato/plan real: el límite se agota igual con
    404 de por medio.
    """

    def setUp(self):
        cache.clear()
        self.portal = PortalCliente.objects.get(cotizacion=_crear_cotizacion())

    def _agota_y_verifica_429(self, nombre_url):
        url = reverse(nombre_url, args=[self.portal.token])
        with patch('core_erp.ratelimit.time.time', return_value=_inicio_ventana()):
            for _ in range(10):
                self.assertNotEqual(self.client.get(url).status_code, 429)
            respuesta = self.client.get(url)
            self.assertEqual(respuesta.status_code, 429)
            self.assertIn('Retry-After', respuesta.headers)

    def test_portal_evento_bloquea_tras_diez_peticiones(self):
        self._agota_y_verifica_429('portal_evento')

    def test_portal_descargar_cotizacion_bloquea_tras_diez_peticiones(self):
        self._agota_y_verifica_429('portal_descargar_cotizacion')

    def test_portal_descargar_plan_bloquea_tras_diez_peticiones(self):
        self._agota_y_verifica_429('portal_descargar_plan')

    def test_portal_descargar_contrato_bloquea_tras_diez_peticiones(self):
        self._agota_y_verifica_429('portal_descargar_contrato')

    def test_cada_vista_tiene_su_propio_cupo(self):
        # Agotar portal_evento no debe afectar a portal_descargar_plan: cada
        # vista usa una key de bucket distinta en @_rate_limit.
        with patch('core_erp.ratelimit.time.time', return_value=_inicio_ventana()):
            url_evento = reverse('portal_evento', args=[self.portal.token])
            for _ in range(11):
                self.client.get(url_evento)
            self.assertEqual(self.client.get(url_evento).status_code, 429)

            url_plan = reverse('portal_descargar_plan', args=[self.portal.token])
            self.assertNotEqual(self.client.get(url_plan).status_code, 429)


class CotizadorApisRateLimitTest(TestCase):
    """Orden 20: las 5 APIs públicas del cotizador, ~60/min."""

    def setUp(self):
        cache.clear()

    def _agota_y_verifica_429(self, nombre_url, query=''):
        url = reverse(nombre_url) + query
        with patch('core_erp.ratelimit.time.time', return_value=_inicio_ventana()):
            for _ in range(60):
                self.assertNotEqual(self.client.get(url).status_code, 429)
            self.assertEqual(self.client.get(url).status_code, 429)

    def test_api_disponibilidad_fecha_bloquea_tras_sesenta_peticiones(self):
        self._agota_y_verifica_429('api_disponibilidad_fecha', '?fecha=2027-01-01')

    def test_api_fechas_ocupadas_bloquea_tras_sesenta_peticiones(self):
        self._agota_y_verifica_429('api_fechas_ocupadas')

    def test_api_productos_cotizador_bloquea_tras_sesenta_peticiones(self):
        self._agota_y_verifica_429('api_productos_cotizador')

    def test_api_total_cotizador_bloquea_tras_sesenta_peticiones(self):
        self._agota_y_verifica_429('api_total_cotizador')

    def test_api_paquetes_cotizador_bloquea_tras_sesenta_peticiones(self):
        self._agota_y_verifica_429('api_paquetes_cotizador')


class OpenpayWebhookRateLimitTest(TestCase):
    """Orden 21 (parte 1 de 3): el webhook de Openpay, ~120/min.

    Se cuenta antes de validar Basic Auth, así que peticiones sin
    credenciales (401) también agotan el cupo — es justo lo que se quiere
    contra el martilleo de credenciales.
    """

    def setUp(self):
        cache.clear()
        self.url = reverse('openpay_webhook')

    def test_bloquea_tras_ciento_veinte_peticiones(self):
        with patch('core_erp.ratelimit.time.time', return_value=_inicio_ventana()):
            for _ in range(120):
                respuesta = self.client.post(
                    self.url, data='{}', content_type='application/json',
                )
                self.assertNotEqual(respuesta.status_code, 429)
            respuesta = self.client.post(
                self.url, data='{}', content_type='application/json',
            )
            self.assertEqual(respuesta.status_code, 429)
