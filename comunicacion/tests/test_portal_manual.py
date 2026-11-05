"""Botón «Enviar portal por WhatsApp» de Cotizaciones: sale por la API, no por wa.me."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from comercial.models import Cliente, Cotizacion
from comunicacion import services_agente
from comunicacion.models import ComunicacionCliente, MensajeWhatsApp
from comunicacion.services_notificaciones import EnvioPortalError, enviar_portal_whatsapp
from core_erp.test_utils import login_superuser_con_totp

from .utils import TEL_CLIENTE, TEL_EMISOR, RespuestaFalsa, error_meta, limpiar_cache_emisor, wa_settings


@wa_settings()
class EnviarPortalWhatsappTest(TestCase):
    def setUp(self):
        limpiar_cache_emisor()
        self.cliente = Cliente.objects.create(nombre='Jazmín Chi', telefono=TEL_CLIENTE)
        self.cot = Cotizacion.objects.create(
            cliente=self.cliente, nombre_evento='XV Años', num_personas=50,
            fecha_evento=date.today() + timedelta(days=90))

    def _enviar(self, respuesta=None):
        with patch('comunicacion.services.requests.post', return_value=respuesta or RespuestaFalsa()) as post, \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            enviar_portal_whatsapp(self.cot)
        return post.call_args.kwargs['json']

    def _abrir_ventana(self):
        conv = services_agente._conversacion(TEL_CLIENTE)
        MensajeWhatsApp.objects.create(conversacion=conv, direccion='ENTRADA', texto='hola', procesado=True)

    def test_sin_ventana_usa_la_plantilla_con_el_portal(self):
        payload = self._enviar()
        self.assertEqual(payload['type'], 'template')
        self.assertEqual(payload['template']['name'], 'qkt_cotizacion_lista')
        parametros = payload['template']['components'][0]['parameters']
        self.assertEqual(parametros[-1]['text'], self.cot.portal.get_full_url())
        comm = ComunicacionCliente.objects.get(cotizacion=self.cot, canal='WHATSAPP')
        self.assertEqual((comm.estado, comm.trigger), ('ENVIADO', 'MANUAL'))

    def test_con_ventana_abierta_manda_texto_libre(self):
        self._abrir_ventana()
        payload = self._enviar()
        self.assertEqual(payload['type'], 'text')
        self.assertIn(self.cot.portal.get_full_url(), payload['text']['body'])

    def test_queda_en_la_conversacion_para_kooxi(self):
        self._enviar()
        self.assertTrue(MensajeWhatsApp.objects.filter(
            conversacion__telefono=TEL_CLIENTE, direccion='AGENTE', automatico=True,
            texto__contains=f'COT-{self.cot.pk:03d}').exists())

    @wa_settings(META_WA_PLANTILLA_COTIZACION='')
    def test_sin_ventana_ni_plantilla_avisa_y_no_envia(self):
        with patch('comunicacion.services.requests.post') as post:
            with self.assertRaisesMessage(EnvioPortalError, '24 h'):
                enviar_portal_whatsapp(self.cot)
        post.assert_not_called()

    def test_sin_telefono(self):
        Cliente.objects.filter(pk=self.cliente.pk).update(telefono='')
        self.cot.refresh_from_db()
        with self.assertRaisesMessage(EnvioPortalError, 'teléfono'):
            enviar_portal_whatsapp(self.cot)

    def test_rechazo_de_meta_se_reporta(self):
        with self.assertRaisesMessage(EnvioPortalError, 'WhatsApp no aceptó'):
            self._enviar(error_meta(132001, 'plantilla inexistente'))

    def test_doble_clic_no_duplica(self):
        self._enviar()
        with self.assertRaisesMessage(EnvioPortalError, 'Ya se envió'):
            self._enviar()
        self.assertEqual(ComunicacionCliente.objects.filter(cotizacion=self.cot, canal='WHATSAPP').count(), 1)


@wa_settings()
class BotonEnviarPortalTest(TestCase):
    def setUp(self):
        limpiar_cache_emisor()
        cliente = Cliente.objects.create(nombre='Jazmín Chi', telefono=TEL_CLIENTE)
        self.cot = Cotizacion.objects.create(
            cliente=cliente, nombre_evento='XV Años', num_personas=50,
            fecha_evento=date.today() + timedelta(days=90), precio_final=Decimal('4000.00'))
        self.url = reverse('admin:cotizacion_enviar_portal_wa', args=[self.cot.pk])

    def test_el_menu_ya_no_abre_wa_me(self):
        from django.contrib import admin as dj_admin

        from comercial.admin import CotizacionAdmin
        items = CotizacionAdmin(Cotizacion, dj_admin.site)._items_menu(self.cot)
        boton = next(i for i in items if i.get('texto') == 'Enviar portal por WhatsApp')
        self.assertEqual(boton['url'], self.url)
        self.assertNotIn('nueva_pestana', boton)

    def test_superusuario_envia(self):
        su = User.objects.create_superuser('dir', 'd@x.mx', 'x')
        login_superuser_con_totp(self.client, su)
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()) as post, \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        post.assert_called_once()

    def test_staff_sin_permiso_no_envia(self):
        staff = User.objects.create_user('v', password='x', is_staff=True)
        self.client.force_login(staff)
        with patch('comunicacion.services.requests.post') as post:
            self.client.get(self.url)
        post.assert_not_called()
