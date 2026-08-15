"""
Tests del backlog de seguridad (Issue #190), órdenes 23-24
(SEC-CSRF-001, SEC-VAL-001): CSRF real en `cotizador_enviar` y su
validación formalizada con `CotizadorEnviarForm`.

Ejecutar: python manage.py test comercial.test_cotizador_seguridad --verbosity=2
"""
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import Producto
from comunicacion.tests.utils import RespuestaFalsa, limpiar_cache_emisor, wa_settings


def _payload(**extra):
    return {
        'nombre': 'Ana Ruiz',
        'telefono': '5215555550001',
        'email': 'ana@example.com',
        'servicio': 'EVENTO',
        'fecha': (timezone.localdate() + timedelta(days=60)).strftime('%Y-%m-%d'),
        'personas': '80',
        'acepta_legales': True,
        **extra,
    }


@wa_settings()
class CotizadorEnviarCsrfTest(TestCase):
    """SEC-CSRF-001 (backlog orden 23): la vista ya no es @csrf_exempt."""

    def setUp(self):
        limpiar_cache_emisor()
        cache.clear()
        Producto.objects.create(
            nombre='Paquete Esencial', precio_venta_fijo=Decimal('25000.00'),
            visible_cotizador=True,
        )
        self.client = Client(enforce_csrf_checks=True)

    def test_sin_token_csrf_responde_403(self):
        # Sin visitar antes /cotizar/ no hay cookie csrftoken en el cliente.
        respuesta = self.client.post(
            reverse('cotizador_enviar'),
            data=json.dumps(_payload()),
            content_type='application/json',
        )
        self.assertEqual(respuesta.status_code, 403)

    def test_con_token_csrf_valido_se_procesa(self):
        # La página pública renderiza {% csrf_token %}: visitarla deja la
        # cookie csrftoken puesta, igual que en un navegador real.
        pagina = self.client.get(reverse('cotizador_publico'))
        token = pagina.cookies['csrftoken'].value

        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()), \
             patch('comunicacion.services.numero_emisor_wa', return_value='5215555550003'):
            respuesta = self.client.post(
                reverse('cotizador_enviar'),
                data=json.dumps(_payload()),
                content_type='application/json',
                HTTP_X_CSRFTOKEN=token,
            )

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.json()['ok'])

    def test_pagina_publica_incluye_el_campo_del_token(self):
        respuesta = self.client.get(reverse('cotizador_publico'))
        self.assertContains(respuesta, 'csrfmiddlewaretoken')


class CotizadorEnviarValidacionTest(TestCase):
    """SEC-VAL-001 (backlog orden 24): CotizadorEnviarForm acota tipos,
    longitudes y choices en vez de aceptar cualquier string."""

    def setUp(self):
        cache.clear()
        Producto.objects.create(
            nombre='Paquete Esencial', precio_venta_fijo=Decimal('25000.00'),
            visible_cotizador=True,
        )

    def _post(self, **extra):
        return self.client.post(
            reverse('cotizador_enviar'),
            data=json.dumps(_payload(**extra)),
            content_type='application/json',
        )

    def test_tipo_evento_fuera_de_las_opciones_conocidas_es_rechazado(self):
        respuesta = self._post(tipo_evento='</script><script>alert(1)</script>')
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('opción válida', ' '.join(respuesta.json()['errores']))

    def test_como_nos_encontro_fuera_de_las_opciones_conocidas_es_rechazado(self):
        respuesta = self._post(como_nos_encontro='cualquier cosa no listada')
        self.assertEqual(respuesta.status_code, 400)

    def test_notas_demasiado_largas_son_rechazadas(self):
        respuesta = self._post(notas='x' * 301)
        self.assertEqual(respuesta.status_code, 400)

    def test_una_opcion_valida_de_tipo_evento_se_acepta(self):
        with patch('comunicacion.services_notificaciones.notificar_cotizacion'), \
             patch('comunicacion.services_notificaciones.alertar_equipo_nueva_cotizacion'):
            respuesta = self._post(tipo_evento='Boda', como_nos_encontro='Instagram')
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.json()['ok'])

    def test_sigue_agregando_todos_los_errores_de_una_vez(self):
        # Mismo comportamiento que antes de este cambio: no se detiene en el
        # primer campo inválido.
        respuesta = self.client.post(
            reverse('cotizador_enviar'), data=json.dumps({}), content_type='application/json',
        )
        self.assertEqual(respuesta.status_code, 400)
        errores = ' '.join(respuesta.json()['errores'])
        self.assertIn('nombre', errores.lower())
        self.assertIn('teléfono', errores.lower())
        self.assertIn('servicio', errores.lower())
        self.assertIn('fecha', errores.lower())
        self.assertIn('Aviso de Privacidad', errores)
