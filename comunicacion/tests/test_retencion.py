"""Plazos de conservación de las conversaciones del agente (Aviso de Privacidad §9)."""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from comercial.models import Cliente, Cotizacion
from comunicacion.models import ComunicacionCliente, ConversacionWhatsApp, MensajeWhatsApp
from comunicacion.services_retencion import purgar_conversaciones

TEL = '529995550001'


class PurgarConversacionesTest(TestCase):
    def _conversacion(self, dias, telefono=TEL, humano=False):
        conv = ConversacionWhatsApp.objects.create(telefono=telefono)
        MensajeWhatsApp.objects.create(conversacion=conv, direccion='ENTRADA', texto='¿Precio?')
        if humano:
            MensajeWhatsApp.objects.create(conversacion=conv, direccion='HUMANO', texto='Te llamo')
        ConversacionWhatsApp.objects.filter(pk=conv.pk).update(
            ultimo_mensaje=timezone.now() - timedelta(days=dias))
        return conv

    def _cotizacion(self, estado):
        cliente = Cliente.objects.create(nombre='Ana', telefono=TEL[-10:])
        cot = Cotizacion.objects.create(
            cliente=cliente, tipo_servicio='PASADIA',
            fecha_evento=timezone.localdate() + timedelta(days=30), num_personas=15)
        Cotizacion.objects.filter(pk=cot.pk).update(estado=estado)

    def test_consulta_sin_concretar_se_borra_al_ano_con_su_copia_en_bitacora(self):
        self._conversacion(dias=400)
        ComunicacionCliente.objects.create(
            canal='WHATSAPP', tipo='AGENTE_IA', destinatario=TEL, cuerpo='Cuesta $1,500')
        self.assertEqual(purgar_conversaciones(aplicar=False)['CONSULTA'], 1)
        self.assertTrue(ConversacionWhatsApp.objects.exists())  # simular no borra

        r = purgar_conversaciones(aplicar=True)
        self.assertEqual((r['CONSULTA'], r['copias_bitacora']), (1, 1))
        self.assertFalse(ConversacionWhatsApp.objects.exists())
        self.assertFalse(MensajeWhatsApp.objects.exists())
        self.assertFalse(ComunicacionCliente.objects.exists())

    def test_consulta_reciente_se_conserva(self):
        self._conversacion(dias=300)
        purgar_conversaciones(aplicar=True)
        self.assertTrue(ConversacionWhatsApp.objects.exists())

    def test_con_atencion_humana_se_conserva_dos_anos(self):
        self._conversacion(dias=400, humano=True)
        purgar_conversaciones(aplicar=True)
        self.assertTrue(ConversacionWhatsApp.objects.exists())

        ConversacionWhatsApp.objects.update(ultimo_mensaje=timezone.now() - timedelta(days=800))
        self.assertEqual(purgar_conversaciones(aplicar=True)['ATENCION'], 1)

    def test_cliente_que_contrato_se_conserva_cinco_anos(self):
        self._cotizacion('CONFIRMADA')
        self._conversacion(dias=800)
        purgar_conversaciones(aplicar=True)
        self.assertTrue(ConversacionWhatsApp.objects.exists())

        ConversacionWhatsApp.objects.update(ultimo_mensaje=timezone.now() - timedelta(days=365 * 5 + 10))
        self.assertEqual(purgar_conversaciones(aplicar=True)['CONTRATACION'], 1)

    def test_cotizacion_que_no_se_concreto_cuenta_como_consulta(self):
        self._cotizacion('EXPIRADA')
        self._conversacion(dias=400)
        self.assertEqual(purgar_conversaciones(aplicar=True)['CONSULTA'], 1)

    def test_no_toca_comunicaciones_ligadas_a_una_cotizacion_ni_otros_numeros(self):
        self._conversacion(dias=400)
        ComunicacionCliente.objects.create(
            canal='WHATSAPP', tipo='AGENTE_IA', destinatario='529990000000', cuerpo='otro cliente')
        ComunicacionCliente.objects.create(
            canal='WHATSAPP', tipo='RECORDATORIO_PAGO', destinatario=TEL, cuerpo='recordatorio')
        purgar_conversaciones(aplicar=True)
        self.assertEqual(ComunicacionCliente.objects.count(), 2)

    def test_comando_simula_por_defecto(self):
        self._conversacion(dias=400)
        salida = StringIO()
        call_command('purgar_conversaciones_whatsapp', stdout=salida)
        self.assertIn('simulación', salida.getvalue())
        self.assertTrue(ConversacionWhatsApp.objects.exists())
        call_command('purgar_conversaciones_whatsapp', '--aplicar', stdout=StringIO())
        self.assertFalse(ConversacionWhatsApp.objects.exists())
