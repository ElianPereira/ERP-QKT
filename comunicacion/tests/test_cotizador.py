"""Tests de las notificaciones que dispara el cotizador público."""
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, PortalCliente, Producto
from comunicacion.models import ComunicacionCliente
from comunicacion.services_notificaciones import alertar_equipo_nueva_cotizacion

from .utils import (
    TEL_CLIENTE,
    TEL_EMISOR,
    TEL_NEGOCIO,
    RespuestaFalsa,
    error_meta,
    limpiar_cache_emisor,
    wa_settings,
)


@wa_settings()
class CotizadorEnviarTest(TestCase):
    """Recorre la vista completa: la cotización se crea pase lo que pase."""

    def _payload(self, **extra):
        return {
            'nombre': 'Ana Ruiz',
            'telefono': TEL_CLIENTE,
            'email': 'ana@example.com',
            'servicio': 'EVENTO',
            'fecha': (timezone.localdate() + timedelta(days=60)).strftime('%Y-%m-%d'),
            'personas': '80',
            'acepta_legales': True,
            **extra,
        }

    def _post(self, **extra):
        return self.client.post(
            reverse('cotizador_enviar'),
            data=json.dumps(self._payload(**extra)),
            content_type='application/json',
        )

    def setUp(self):
        limpiar_cache_emisor()
        mail.outbox = []
        # Sin catálogo la cotización queda en $0.00 y no se podría comprobar que
        # el total del mensaje es el definitivo.
        Producto.objects.create(
            nombre='Paquete Esencial',
            precio_venta_fijo=Decimal('25000.00'),
            visible_cotizador=True,
        )

    def test_caso_feliz(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()), \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        datos = resp.json()
        self.assertTrue(datos['ok'])

        cot = Cotizacion.objects.get(pk=datos['cotizacion_id'])
        portal = PortalCliente.objects.get(cotizacion=cot)
        self.assertEqual(datos['portal_url'], portal.get_full_url())

        # Alerta interna (email + WhatsApp) y notificación al cliente.
        interna = ComunicacionCliente.objects.filter(cotizacion=cot, tipo='OTRO')
        self.assertEqual(interna.filter(canal='WHATSAPP').count(), 1)
        self.assertEqual(interna.filter(canal='EMAIL').count(), 1)
        self.assertEqual(interna.get(canal='WHATSAPP').destinatario, TEL_NEGOCIO)

        cliente = ComunicacionCliente.objects.filter(cotizacion=cot, tipo='COTIZACION')
        self.assertEqual(cliente.filter(canal='EMAIL').count(), 1)
        self.assertEqual(cliente.filter(canal='WHATSAPP').count(), 1)
        self.assertEqual(set(cliente.values_list('estado', flat=True)), {'ENVIADO'})

    def test_el_whatsapp_del_cliente_lleva_el_total_ya_recalculado(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()), \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            resp = self._post()
        cot = Cotizacion.objects.get(pk=resp.json()['cotizacion_id'])
        wa = ComunicacionCliente.objects.get(
            cotizacion=cot, tipo='COTIZACION', canal='WHATSAPP'
        )
        self.assertGreater(cot.precio_final, Decimal('0.00'))
        self.assertIn(f"{cot.precio_final:,.2f}", wa.cuerpo)
        self.assertIn(cot.fecha_evento.strftime('%d/%m/%Y'), wa.cuerpo)
        self.assertIn(PortalCliente.objects.get(cotizacion=cot).token, wa.cuerpo)

    @wa_settings(WA_NUMERO_NEGOCIO='')
    def test_sin_numero_de_negocio_la_cotizacion_se_crea_igual(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()) as post, \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR), \
             self.assertLogs('comunicacion.services_notificaciones', level='ERROR') as logs:
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        cot = Cotizacion.objects.get(pk=resp.json()['cotizacion_id'])
        # No hay alerta interna por WhatsApp, pero sí la copia por email…
        self.assertFalse(
            ComunicacionCliente.objects.filter(
                cotizacion=cot, tipo='OTRO', canal='WHATSAPP'
            ).exists()
        )
        self.assertTrue(
            ComunicacionCliente.objects.filter(
                cotizacion=cot, tipo='OTRO', canal='EMAIL'
            ).exists()
        )
        # …y el cliente sí recibe lo suyo.
        self.assertEqual(post.call_count, 1)
        texto_log = '\n'.join(logs.output)
        self.assertIn('WA_NUMERO_NEGOCIO', texto_log)
        self.assertNotIn('TOKEN_TEST', texto_log)

    def test_meta_caido_no_rompe_el_cotizador(self):
        with patch('comunicacion.services.requests.post', side_effect=RuntimeError('Meta caído')), \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        cot = Cotizacion.objects.get(pk=resp.json()['cotizacion_id'])
        self.assertEqual(
            ComunicacionCliente.objects.get(
                cotizacion=cot, tipo='COTIZACION', canal='WHATSAPP'
            ).estado,
            'FALLIDO',
        )
        # El email del cliente sigue saliendo.
        self.assertEqual(
            ComunicacionCliente.objects.get(
                cotizacion=cot, tipo='COTIZACION', canal='EMAIL'
            ).estado,
            'ENVIADO',
        )

    def test_brevo_caido_no_impide_el_whatsapp(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()), \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR), \
             patch('comunicacion.services.EmailMultiAlternatives.send',
                   side_effect=RuntimeError('Brevo caído')):
            resp = self._post()
        self.assertTrue(resp.json()['ok'])
        cot = Cotizacion.objects.get(pk=resp.json()['cotizacion_id'])
        self.assertEqual(
            ComunicacionCliente.objects.get(
                cotizacion=cot, tipo='COTIZACION', canal='WHATSAPP'
            ).estado,
            'ENVIADO',
        )


@wa_settings()
class AlertaInternaTest(TestCase):
    def setUp(self):
        limpiar_cache_emisor()
        self.cliente = Cliente.objects.create(nombre='Ana', telefono=TEL_CLIENTE)
        self.cot = Cotizacion.objects.create(
            cliente=self.cliente, nombre_evento='Boda',
            fecha_evento=timezone.localdate() + timedelta(days=30),
            num_personas=50, precio_final=Decimal('12345.60'),
        )
        # save() recalcula los totales desde los items; sin items el precio se
        # va a cero, así que se fija por UPDATE como en el resto de la suite.
        Cotizacion.objects.filter(pk=self.cot.pk).update(precio_final=Decimal('12345.60'))
        self.cot.refresh_from_db()

    def test_el_destino_es_el_numero_configurado(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()) as post, \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            alertar_equipo_nueva_cotizacion(self.cot)
        self.assertEqual(post.call_args.kwargs['json']['to'], TEL_NEGOCIO)

    def test_el_mensaje_es_breve_y_lleva_lo_indispensable(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()) as post, \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            alertar_equipo_nueva_cotizacion(self.cot)
        cuerpo = post.call_args.kwargs['json']['text']['body']
        self.assertIn('12,345.60', cuerpo)
        self.assertIn(self.cot.fecha_evento.strftime('%d/%m/%Y'), cuerpo)
        self.assertIn('/mi-evento/', cuerpo)
        # Ya no se manda el desglose largo: eso vive en el portal.
        for ausente in ('Personas', 'Horario', 'Nos encontró', 'Teléfono'):
            self.assertNotIn(ausente, cuerpo)

    @wa_settings(WA_TEMPLATE_ALERTA_INTERNA='qkt_alerta_nueva_cotizacion')
    def test_con_plantilla_configurada_usa_plantilla(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()) as post, \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            alertar_equipo_nueva_cotizacion(self.cot)
        enviado = post.call_args.kwargs['json']
        self.assertEqual(enviado['type'], 'template')
        self.assertEqual(enviado['template']['name'], 'qkt_alerta_nueva_cotizacion')
        textos = [p['text'] for p in enviado['template']['components'][0]['parameters']]
        self.assertEqual(len(textos), 4)
        for texto in textos:
            self.assertNotIn('\n', texto)

    def test_fuera_de_ventana_queda_auditado_con_su_codigo(self):
        with patch('comunicacion.services.requests.post', return_value=error_meta(131047)), \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            alertar_equipo_nueva_cotizacion(self.cot)
        comm = ComunicacionCliente.objects.get(
            cotizacion=self.cot, tipo='OTRO', canal='WHATSAPP'
        )
        self.assertEqual(comm.estado, 'FALLIDO')
        self.assertIn('Meta 131047', comm.error)

    @wa_settings(WA_NUMERO_NEGOCIO=TEL_EMISOR)
    def test_si_el_destino_es_el_emisor_no_llama_a_messages(self):
        respuesta_emisor = RespuestaFalsa(payload={'display_phone_number': TEL_EMISOR})
        with patch('comunicacion.services.requests.get', return_value=respuesta_emisor), \
             patch('comunicacion.services.requests.post') as post:
            alertar_equipo_nueva_cotizacion(self.cot)
        post.assert_not_called()
        comm = ComunicacionCliente.objects.get(
            cotizacion=self.cot, tipo='OTRO', canal='WHATSAPP'
        )
        self.assertEqual(comm.estado, 'FALLIDO')
        self.assertIn('131021', comm.error)

    def test_no_duplica_si_se_dispara_dos_veces(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()), \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            alertar_equipo_nueva_cotizacion(self.cot)
            alertar_equipo_nueva_cotizacion(self.cot)
        self.assertEqual(
            ComunicacionCliente.objects.filter(cotizacion=self.cot, tipo='OTRO').count(),
            2,  # un email + un WhatsApp
        )
