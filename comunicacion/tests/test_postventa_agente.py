"""Ruteo de los botones, números y archivos de la Fase 3 por el webhook y el agente."""
from unittest.mock import patch

from django.test import TestCase, override_settings

from comunicacion import services_agente, views_whatsapp
from comunicacion.models import ConversacionWhatsApp, MensajeWhatsApp, Responsable

DIRECCION = '9994440000'
CLIENTE = '9991234567'


def _cambio(telefono, **mensaje):
    mensaje.setdefault('id', f'wamid.{len(str(mensaje))}{telefono}')
    return {'field': 'messages', 'value': {'messages': [{'from': '52' + telefono, **mensaje}], 'contacts': []}}


class WebhookFase3Test(TestCase):
    def setUp(self):
        Responsable.objects.create(rol='DIRECCION', nombre='Dueño', telefono_wa=DIRECCION)

    def _guardado(self):
        return MensajeWhatsApp.objects.get()

    def test_boton_de_plantilla_de_encuesta_llega_como_payload(self):
        views_whatsapp._procesar_cambio(_cambio(
            CLIENTE, type='button', button={'payload': 'enc:7:5', 'text': 'Excelente'}))
        self.assertEqual(self._guardado().texto, 'enc:7:5')

    def test_boton_de_confirmar_vigencia_se_conserva(self):
        views_whatsapp._procesar_cambio(_cambio(
            DIRECCION, type='interactive', interactive={'button_reply': {'id': 'vig:3:ok', 'title': 'Confirmar'}}))
        self.assertEqual(self._guardado().texto, 'vig:3:ok')

    def test_archivo_de_un_responsable_guarda_la_referencia_y_el_pie(self):
        views_whatsapp._procesar_cambio(_cambio(
            DIRECCION, type='document', document={'id': 'm1', 'mime_type': 'application/pdf', 'caption': 'Licencia'}))
        m = self._guardado()
        self.assertEqual((m.texto, m.media_id, m.media_mime), ('Licencia', 'm1', 'application/pdf'))

    def test_archivo_de_un_cliente_sigue_como_antes_sin_guardar_referencia(self):
        views_whatsapp._procesar_cambio(_cambio(CLIENTE, type='image', image={'id': 'm2', 'mime_type': 'image/jpeg'}))
        m = self._guardado()
        self.assertEqual(m.media_id, '')
        self.assertIn('solo puede leer texto', m.texto)


class AgenteFase3Test(TestCase):
    def _conv(self, telefono):
        return ConversacionWhatsApp.objects.create(telefono='52' + telefono)

    def _mensaje(self, conv, texto, **kw):
        return MensajeWhatsApp.objects.create(conversacion=conv, direccion='ENTRADA', texto=texto, **kw)

    def test_calificacion_no_llega_al_agente_y_se_contesta_con_texto_fijo(self):
        conv = self._conv(CLIENTE)
        self._mensaje(conv, 'enc:1:5')
        with patch.object(services_agente.services_encuesta, 'atender_respuesta', return_value=(True, 'Gracias')), \
                patch.object(services_agente, '_enviar_automatico') as enviar, \
                patch.object(services_agente, 'responder') as modelo:
            services_agente._atender_pendientes(conv.pk)
        enviar.assert_called_once_with(conv, 'Gracias')
        modelo.assert_not_called()

    def test_archivo_se_atiende_sin_pasar_por_el_agente(self):
        conv = self._conv(DIRECCION)
        self._mensaje(conv, 'Licencia', media_id='m1', media_mime='application/pdf')
        with patch.object(services_agente.services_renovacion, 'recibir_documento', return_value='Listo') as rec, \
                patch.object(services_agente, '_enviar_automatico') as enviar, \
                patch.object(services_agente, 'responder') as modelo:
            services_agente._atender_pendientes(conv.pk)
        rec.assert_called_once_with('52' + DIRECCION, 'm1', 'Licencia')
        enviar.assert_called_once_with(conv, 'Listo')
        modelo.assert_not_called()

    def test_boton_de_vigencia_ajeno_se_descarta_en_silencio(self):
        conv = self._conv(CLIENTE)
        self._mensaje(conv, 'vig:1:ok')
        with patch.object(services_agente, '_enviar_automatico') as enviar, \
                patch.object(services_agente, 'responder') as modelo, \
                patch.object(services_agente, 'agente_contesta_a', return_value=False):
            services_agente._atender_pendientes(conv.pk)
        enviar.assert_not_called()
        modelo.assert_not_called()
