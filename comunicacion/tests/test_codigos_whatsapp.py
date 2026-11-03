"""Código de verificación por WhatsApp para clientes sin correo."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from comercial import services_cancelacion as sc
from comercial.models import Cliente, Cotizacion
from comunicacion import services_codigos
from comunicacion.models import ConversacionWhatsApp, MensajeWhatsApp

from .utils import TEL_CLIENTE, RespuestaFalsa, wa_settings


def _cotizacion(email=''):
    cliente = Cliente.objects.create(nombre='Ana Ruiz', tipo_persona='FISICA', email=email, telefono=TEL_CLIENTE)
    cot = Cotizacion.objects.create(
        cliente=cliente, nombre_evento='Boda', tipo_servicio='EVENTO',
        fecha_evento=timezone.localdate() + timedelta(days=60), incluye_refrescos=False)
    Cotizacion.objects.filter(pk=cot.pk).update(estado='CONFIRMADA')
    cot.refresh_from_db()
    return cot


class _Comm:
    estado = 'ENVIADO'


@wa_settings()
class CodigoPorWhatsappTest(TestCase):
    def setUp(self):
        self.cot = _cotizacion()

    def _abrir_ventana(self):
        conv = ConversacionWhatsApp.objects.create(telefono=TEL_CLIENTE)
        MensajeWhatsApp.objects.create(conversacion=conv, direccion='ENTRADA', texto='hola', procesado=True)

    def _enviar(self):
        with patch.object(services_codigos, '_enviar_wa', return_value=_Comm()) as envio:
            ok = services_codigos.enviar_codigo_whatsapp(cotizacion=self.cot, codigo='123456', minutos=10)
        return ok, envio

    def test_con_ventana_abierta_sale_como_texto(self):
        self._abrir_ventana()
        ok, envio = self._enviar()
        self.assertTrue(ok)
        payload = envio.call_args.args[1]
        self.assertEqual(payload['type'], 'text')
        self.assertIn('123456', payload['text']['body'])

    @override_settings(META_WA_PLANTILLA_CODIGO='codigo_verificacion')
    def test_sin_ventana_usa_la_plantilla_de_autenticacion(self):
        ok, envio = self._enviar()
        self.assertTrue(ok)
        plantilla = envio.call_args.args[1]['template']
        self.assertEqual(plantilla['name'], 'codigo_verificacion')
        tipos = [c['type'] for c in plantilla['components']]
        self.assertEqual(tipos, ['body', 'button'])

    @override_settings(META_WA_PLANTILLA_CODIGO='')
    def test_sin_ventana_ni_plantilla_no_envia(self):
        ok, envio = self._enviar()
        self.assertFalse(ok)
        envio.assert_not_called()

    def test_el_codigo_no_queda_en_la_bitacora(self):
        self._abrir_ventana()
        with patch('comunicacion.services.numero_emisor_wa', return_value=''), \
                patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()):
            services_codigos.enviar_codigo_whatsapp(cotizacion=self.cot, codigo='654321', minutos=10)
        self.assertFalse(self.cot.comunicaciones.filter(cuerpo__contains='654321').exists())
        self.assertTrue(self.cot.comunicaciones.filter(canal='WHATSAPP').exists())

    def test_cancelacion_sin_correo_manda_el_codigo_por_whatsapp(self):
        self._abrir_ventana()
        with patch.object(services_codigos, '_enviar_wa', return_value=_Comm()) as envio:
            s = sc.iniciar(self.cot, canal='PORTAL', tipo='CANCELACION')
        self.assertEqual(s.estado, 'VERIFICANDO')
        self.assertTrue(envio.called)
        self.assertNotIn('@', s.codigo_destino)

    def test_cliente_sin_correo_ni_telefono_se_rechaza(self):
        Cliente.objects.filter(pk=self.cot.cliente_id).update(telefono='')
        self.cot.refresh_from_db()
        with self.assertRaises(sc.CancelacionError):
            sc.iniciar(self.cot, canal='PORTAL', tipo='CANCELACION')
