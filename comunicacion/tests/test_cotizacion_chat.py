"""Crear la cotización desde el chat de WhatsApp (Issue #366, fase C)."""
import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cotizacion, Producto
from comunicacion import herramientas_agente, services_agente
from comunicacion.models import ConversacionWhatsApp, MensajeWhatsApp
from legal.models import AceptacionLegal, DocumentoLegal, Finalidad, OrigenAceptacion, TipoDocumento

from .test_agente import AGENTE, SECRETO
from .utils import TEL_CLIENTE, wa_settings


def _ejecutar(conv, **entrada):
    salida, error = herramientas_agente.ejecutar('crear_cotizacion', entrada, conv=conv)
    return json.loads(salida), error


def _payload_boton(boton_id, titulo, wamid='wamid.boton1', telefono=TEL_CLIENTE):
    return {'object': 'whatsapp_business_account', 'entry': [{'changes': [{
        'field': 'messages',
        'value': {
            'contacts': [{'wa_id': telefono, 'profile': {'name': 'Ana'}}],
            'messages': [{'from': telefono, 'id': wamid, 'type': 'interactive', 'interactive': {
                'type': 'button_reply', 'button_reply': {'id': boton_id, 'title': titulo}}}],
        },
    }]}]}


@wa_settings(**AGENTE)
class CotizacionEnElChatTest(TestCase):
    def setUp(self):
        cache.clear()
        for tipo in (TipoDocumento.AVISO_PRIVACIDAD, TipoDocumento.TERMINOS, TipoDocumento.POLITICA_CANCELACION):
            DocumentoLegal.objects.create(tipo=tipo, version='1.0', titulo=f'Doc {tipo}',
                                          contenido_md='Texto', vigente_desde=timezone.localdate(), vigente=True)
        Finalidad.objects.create(clave='MARKETING', nombre='Promociones', requiere_consentimiento=True)
        Producto.objects.create(nombre='Pasadía Básico', precio_venta_fijo=Decimal('1724.14'),
                                visible_cotizador=True, cotizador_pasadia=True,
                                rol_cotizador='BASE_PASADIA_BASICO')
        self.conv = ConversacionWhatsApp.objects.create(telefono=TEL_CLIENTE, nombre='Ana')
        self.fecha = (timezone.localdate() + timedelta(days=40)).isoformat()
        self.datos = dict(servicio='PASADIA', fecha=self.fecha, personas=15,
                          nombre='Ana Ruiz', correo='ana@example.com')

    def _aceptar(self, boton=services_agente.BOTON_ACEPTO):
        services_agente.registrar_consentimiento(telefono=TEL_CLIENTE, boton_id=boton, wamid='wamid.acepto')
        self.conv.refresh_from_db()

    def _sin_red(self):
        return patch('comunicacion.services_notificaciones._seguro', return_value=None)

    def test_sin_autorizacion_manda_los_botones_y_no_crea_nada(self):
        with patch.object(services_agente, 'enviar_whatsapp_botones',
                          return_value=SimpleNamespace(estado='ENVIADO', proveedor_id='wamid.botones')) as botones:
            r, _ = _ejecutar(self.conv, **self.datos)
            r2, _ = _ejecutar(self.conv, **self.datos)
        self.assertTrue(r['requiere_autorizacion'] and r2['requiere_autorizacion'])
        botones.assert_called_once()  # el segundo intento no repite el mensaje
        ids = [i for i, _ in botones.call_args.kwargs['botones']]
        self.assertIn(services_agente.BOTON_ACEPTO, ids)
        self.assertFalse(Cotizacion.objects.exists())
        self.assertTrue(MensajeWhatsApp.objects.filter(automatico=True, wamid='wamid.botones').exists())

    def test_con_acepto_crea_la_misma_cotizacion_que_la_web_y_deja_evidencia(self):
        self._aceptar(services_agente.BOTON_ACEPTO_PROMOS)
        with self._sin_red():
            r, error = _ejecutar(self.conv, **self.datos)
        self.assertFalse(error, r)
        cot = Cotizacion.objects.get()
        self.assertEqual(r['folio'], f'COT-{cot.id:03d}')
        self.assertEqual(r['total'], '$2,000.00')
        self.assertEqual(cot.precio_final,
                         herramientas_agente.estimar_total(servicio='PASADIA', num_personas=15)['total'])
        self.assertEqual(cot.cliente.telefono[-10:], TEL_CLIENTE[-10:])
        self.assertNotIn('portal', r)

        ac = AceptacionLegal.objects.get()
        self.assertEqual((ac.origen, ac.referencia_externa, ac.cliente_id),
                         (OrigenAceptacion.WHATSAPP, 'wamid.acepto', cot.cliente_id))
        self.assertEqual(ac.finalidades_aceptadas, ['MARKETING'])
        self.assertEqual(ac.aceptado_en, self.conv.consentimiento_en)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.cliente_id, cot.cliente_id)

    def test_no_acepto_retira_la_autorizacion(self):
        self._aceptar()
        self._aceptar(services_agente.BOTON_NO_ACEPTO)
        self.assertFalse(services_agente.consentimiento_vigente(self.conv))

    def test_autorizacion_vencida_se_vuelve_a_pedir(self):
        self._aceptar()
        ConversacionWhatsApp.objects.filter(pk=self.conv.pk).update(
            consentimiento_en=timezone.now() - timedelta(hours=25))
        self.conv.refresh_from_db()
        with patch.object(services_agente, 'enviar_whatsapp_botones', return_value=None):
            r, _ = _ejecutar(self.conv, **self.datos)
        self.assertTrue(r['requiere_autorizacion'])

    def test_reglas_del_cotizador_y_del_chat(self):
        self._aceptar()
        with self._sin_red():
            self.assertIn('error', _ejecutar(self.conv, **{**self.datos, 'correo': 'no-es-correo'})[0])
            self.assertIn('error', _ejecutar(self.conv, **{**self.datos, 'personas': 31})[0])
            with patch.object(herramientas_agente, 'verificar_disponibilidad_rango', return_value=(False, '')):
                self.assertIn('error', _ejecutar(self.conv, **self.datos)[0])
        self.assertFalse(Cotizacion.objects.exists())

    def test_tope_diario_por_numero(self):
        self._aceptar()
        with self._sin_red():
            for _ in range(herramientas_agente.MAX_COTIZACIONES_CHAT_DIA):
                self.assertNotIn('error', _ejecutar(self.conv, **self.datos)[0])
            self.assertIn('error', _ejecutar(self.conv, **self.datos)[0])

    def test_el_webhook_guarda_el_boton(self):
        cuerpo = json.dumps(_payload_boton(services_agente.BOTON_ACEPTO, 'Acepto')).encode()
        firma = 'sha256=' + hmac.new(SECRETO.encode(), cuerpo, hashlib.sha256).hexdigest()
        with patch.object(services_agente, 'lanzar_procesamiento'):
            r = self.client.post(reverse('whatsapp_webhook'), cuerpo, content_type='application/json',
                                 HTTP_X_HUB_SIGNATURE_256=firma)
        self.assertEqual(r.status_code, 200)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.consentimiento_wamid, 'wamid.boton1')
        self.assertTrue(services_agente.consentimiento_vigente(self.conv))
        self.assertIn('Acepto', self.conv.mensajes.get(direccion='ENTRADA').texto)
