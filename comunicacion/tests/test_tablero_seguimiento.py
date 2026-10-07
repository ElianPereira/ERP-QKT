"""Tablero de Kooxi y seguimiento de cotizaciones sin pago (Issue #366, fase B)."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, Pago
from comunicacion import services_agente, services_tablero
from comunicacion.models import ComunicacionCliente, MensajeWhatsApp, PaseAHumano
from core_erp.test_utils import login_superuser_con_totp
from legal.models import AceptacionLegal

from .test_agente import AGENTE, _cliente_falso, _respuesta, _texto
from .utils import TEL_CLIENTE, TEL_EMISOR, RespuestaFalsa, limpiar_cache_emisor, wa_settings


def _con_uso(respuesta, entrada=1000, salida=200, lectura=5000, escritura=0):
    respuesta.model = 'claude-opus-5-5'
    respuesta.usage = SimpleNamespace(input_tokens=entrada, output_tokens=salida,
                                      cache_read_input_tokens=lectura,
                                      cache_creation_input_tokens=escritura)
    return respuesta


@wa_settings(**AGENTE)
class ConsumoYPasesTest(TestCase):
    def setUp(self):
        cache.clear()
        self.conv = services_agente.recibir_mensaje(
            telefono=TEL_CLIENTE, nombre='Ana', texto='Hola', wamid='w1')

    def _procesar(self, cliente):
        with patch.object(services_agente, '_cliente_ia', return_value=cliente), \
                patch.object(services_agente, 'enviar_whatsapp', return_value=SimpleNamespace(proveedor_id='')):
            services_agente.procesar_conversacion(self.conv.pk)

    def test_la_respuesta_guarda_el_consumo_sumado_de_sus_vueltas(self):
        from .test_agente import _uso
        cliente = _cliente_falso(
            _con_uso(_respuesta(_uso('preguntas_frecuentes', {}), stop='tool_use')),
            _con_uso(_respuesta(_texto('La pasadía es de 11 a 7.')), entrada=300, salida=50, lectura=6000),
        )
        self._procesar(cliente)
        m = MensajeWhatsApp.objects.get(texto='La pasadía es de 11 a 7.')
        self.assertEqual((m.modelo, m.tokens_entrada, m.tokens_salida, m.tokens_cache_lectura),
                         ('claude-opus-5-5', 1300, 250, 11000))

    def test_cada_pase_a_humano_queda_registrado(self):
        services_agente._pasar_a_humano(self.conv, 'Quiere negociar', avisar=False)
        services_agente._pasar_a_humano(self.conv, 'Queja', avisar=False)
        self.assertEqual(list(PaseAHumano.objects.values_list('motivo', flat=True).order_by('id')),
                         ['Quiere negociar', 'Queja'])

    def test_el_seguimiento_llega_al_modelo_como_contexto(self):
        services_agente.registrar_mensaje_automatico(
            telefono=TEL_CLIENTE, texto='Seguimiento: la cotización COT-001 sigue disponible.')
        services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='Sí, me interesa', wamid='w2')
        cliente = _cliente_falso(_respuesta(_texto('¡Qué gusto!')))
        self._procesar(cliente)
        enviado = cliente.enviados[-1][-1]['content'][0]['text']
        self.assertIn('Mensaje automático de QKT: Seguimiento', enviado)
        self.assertIn('Cliente: Sí, me interesa', enviado)


class TableroTest(TestCase):
    def setUp(self):
        self.conv = services_agente.recibir_mensaje(
            telefono=TEL_CLIENTE, nombre='Ana', texto='Hola', wamid='w1')
        MensajeWhatsApp.objects.create(
            conversacion=self.conv, direccion='AGENTE', texto='Hola Ana', procesado=True,
            modelo='claude-opus-5-5', tokens_entrada=1_000_000, tokens_salida=100_000,
            tokens_cache_lectura=1_000_000)
        PaseAHumano.objects.create(conversacion=self.conv, motivo='Queja')

    def test_metricas(self):
        m = services_tablero.metricas_agente(30)
        self.assertEqual((m['conversaciones_activas'], m['respuestas_ia'], m['pases_a_humano']), (1, 1, 1))
        # 1M entrada x $4 + 0.1M salida x $20 + 1M caché x $0.20
        self.assertEqual(m['consumo']['costo_usd'], Decimal('6.20'))
        self.assertEqual(m['conversion']['con_cotizacion'], 0)

    def test_conversion_cuenta_cotizacion_y_pago_posteriores(self):
        cliente = Cliente.objects.create(nombre='Ana', telefono=TEL_CLIENTE[-10:])
        cot = Cotizacion.objects.create(cliente=cliente, tipo_servicio='PASADIA', num_personas=15,
                                        fecha_evento=timezone.localdate() + timedelta(days=30))
        Cotizacion.objects.filter(pk=cot.pk).update(estado='CONFIRMADA')
        c = services_tablero.metricas_agente(30)['conversion']
        self.assertEqual((c['nuevas'], c['con_cotizacion'], c['con_pago'], c['tasa_pago']), (1, 1, 1, 100))

    def test_modelo_sin_precio_no_inventa_costo(self):
        MensajeWhatsApp.objects.update(modelo='claude-desconocido')
        consumo = services_tablero.metricas_agente(30)['consumo']
        self.assertFalse(consumo['costo_completo'])
        self.assertIsNone(consumo['costo_por_respuesta_usd'])

    def test_la_pantalla_carga(self):
        usuario = User.objects.create_superuser('dir', 'dir@qkt.test', 'x')
        login_superuser_con_totp(self.client, usuario)
        r = self.client.get(reverse('admin:comunicacion_tablero_agente'), {'dias': 7})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Tablero de Kooxi')
        self.assertContains(r, 'Queja')

    def test_sin_permiso_no_entra(self):
        usuario = User.objects.create_user('ventas', 'v@qkt.test', 'x', is_staff=True)
        self.client.force_login(usuario)
        r = self.client.get(reverse('admin:comunicacion_tablero_agente'))
        self.assertEqual(r.status_code, 403)


@wa_settings(WA_TEMPLATE_SEGUIMIENTO='seguimiento_cotizacion', WA_SEGUIMIENTO_DIAS=3)
class SeguimientoTest(TestCase):
    def setUp(self):
        limpiar_cache_emisor()
        self.hoy = timezone.localdate()
        self.cliente = Cliente.objects.create(nombre='Ana Ruiz', telefono=TEL_CLIENTE[-10:])
        self.cot = self._cot()

    def _cot(self, dias_creada=3, estado='COTIZADA'):
        cot = Cotizacion.objects.create(cliente=self.cliente, tipo_servicio='PASADIA', num_personas=15,
                                        fecha_evento=self.hoy + timedelta(days=40))
        Cotizacion.objects.filter(pk=cot.pk).update(
            estado=estado, precio_final=Decimal('2000.00'),
            created_at=timezone.now() - timedelta(days=dias_creada))
        cot.refresh_from_db()
        return cot

    def _acepta_marketing(self):
        AceptacionLegal.objects.create(cliente=self.cliente, correo='ana@example.com',
                                       origen='FORM_COTIZACION', finalidades_aceptadas=['MARKETING'])

    def _correr(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()) as post, \
                patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            call_command('enviar_seguimientos', stdout=StringIO())
        return post

    def test_sin_consentimiento_de_marketing_no_se_manda(self):
        post = self._correr()
        post.assert_not_called()

    def test_se_manda_una_sola_vez_y_queda_en_la_conversacion(self):
        self._acepta_marketing()
        self._correr()
        self._correr()
        envios = ComunicacionCliente.objects.filter(tipo='SEGUIMIENTO', cotizacion=self.cot)
        self.assertEqual(envios.count(), 1)
        self.assertIn(f'COT-{self.cot.pk:03d}', envios.get().cuerpo)
        msj = MensajeWhatsApp.objects.get(automatico=True)
        self.assertEqual(msj.direccion, 'AGENTE')

    def test_pagada_vieja_o_reciente_no_se_manda(self):
        self._acepta_marketing()
        Pago.objects.bulk_create([Pago(cotizacion=self.cot, monto=Decimal('1000.00'), metodo='EFECTIVO')])
        self._cot(dias_creada=1)
        self._cot(dias_creada=30)
        self._cot(estado='CANCELADA')
        self._correr()
        self.assertFalse(ComunicacionCliente.objects.filter(tipo='SEGUIMIENTO').exists())

    def test_sin_plantilla_no_hace_nada(self):
        self._acepta_marketing()
        with self.settings(WA_TEMPLATE_SEGUIMIENTO=''):
            post = self._correr()
        post.assert_not_called()
