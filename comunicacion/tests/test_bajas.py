"""Bajas de promociones por WhatsApp: «BAJA» o pedírselo a Kooxi."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from comercial.models import Cliente, Cotizacion
from comunicacion import herramientas_agente, services_agente
from comunicacion.models import BajaWhatsApp, ComunicacionCliente, MensajeWhatsApp
from comunicacion.services_bajas import CONFIRMACION_BAJA, dado_de_baja, es_palabra_baja
from legal.models import AceptacionLegal

from .test_agente import AGENTE, _cliente_falso, _respuesta, _texto
from .utils import TEL_CLIENTE, TEL_EMISOR, RespuestaFalsa, limpiar_cache_emisor, wa_settings


class PalabraBajaTest(TestCase):
    def test_reconoce_la_palabra_sin_importar_mayusculas_ni_signos(self):
        for texto in ('BAJA', 'baja.', ' Baja! ', 'Darme de baja', 'STOP'):
            self.assertTrue(es_palabra_baja(texto), texto)

    def test_una_frase_que_menciona_baja_no_es_una_baja(self):
        for texto in ('¿Cuándo es la baja del mobiliario?', 'Hola', '', 'bajar el precio'):
            self.assertFalse(es_palabra_baja(texto), texto)


@wa_settings(**AGENTE)
class BajaEnElChatTest(TestCase):
    def setUp(self):
        cache.clear()

    def _procesar(self, conv, cliente_ia=None):
        enviar = patch.object(services_agente, 'enviar_whatsapp', return_value=SimpleNamespace(proveedor_id=''))
        with enviar as envio, patch.object(services_agente, '_cliente_ia', return_value=cliente_ia):
            services_agente.procesar_conversacion(conv.pk)
        return [c.kwargs['mensaje'] for c in envio.call_args_list]

    def test_baja_se_registra_y_confirma_sin_llamar_al_modelo(self):
        conv = services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='BAJA', wamid='w1')
        conv.consentimiento_marketing = True
        conv.save(update_fields=['consentimiento_marketing'])
        enviados = self._procesar(conv, cliente_ia=None)
        self.assertEqual(enviados, [CONFIRMACION_BAJA])
        baja = BajaWhatsApp.objects.get()
        self.assertEqual((baja.origen, baja.wamid), ('PALABRA', 'w1'))
        conv.refresh_from_db()
        self.assertFalse(conv.consentimiento_marketing)
        self.assertTrue(MensajeWhatsApp.objects.get(texto=CONFIRMACION_BAJA).automatico)

    @wa_settings(**{**AGENTE, 'WA_AGENTE_ACTIVO': False})
    def test_la_baja_se_atiende_aunque_el_agente_este_apagado(self):
        conv = services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='baja', wamid='w1')
        self.assertEqual(self._procesar(conv), [CONFIRMACION_BAJA])
        self.assertTrue(BajaWhatsApp.objects.exists())

    def test_si_trae_otra_pregunta_el_agente_la_contesta(self):
        services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='BAJA', wamid='w1')
        conv = services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana',
                                               texto='¿A qué hora abre la pasadía?', wamid='w2')
        cliente = _cliente_falso(_respuesta(_texto('Abre a las 11.')))
        enviados = self._procesar(conv, cliente_ia=cliente)
        self.assertEqual(enviados[0], CONFIRMACION_BAJA)
        self.assertEqual(enviados[-1], 'Abre a las 11.')
        self.assertIn('¿A qué hora abre la pasadía?', cliente.enviados[-1][-1]['content'][0]['text'])

    def test_la_herramienta_registra_la_baja_del_numero_que_escribe(self):
        conv = services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='Hola', wamid='w1')
        salida, error = herramientas_agente.ejecutar('dar_de_baja_promociones', {}, conv=conv)
        self.assertFalse(error, salida)
        self.assertEqual(BajaWhatsApp.objects.get().origen, 'AGENTE')
        self.assertTrue(dado_de_baja(TEL_CLIENTE))


@wa_settings(WA_TEMPLATE_SEGUIMIENTO='seguimiento_cotizacion', WA_SEGUIMIENTO_DIAS=3)
class BajaYSeguimientoTest(TestCase):
    def setUp(self):
        limpiar_cache_emisor()
        hoy = timezone.localdate()
        self.cliente = Cliente.objects.create(nombre='Ana Ruiz', telefono=TEL_CLIENTE[-10:])
        cot = Cotizacion.objects.create(cliente=self.cliente, tipo_servicio='PASADIA', num_personas=15,
                                        fecha_evento=hoy + timedelta(days=40))
        Cotizacion.objects.filter(pk=cot.pk).update(
            estado='COTIZADA', precio_final=Decimal('2000.00'),
            created_at=timezone.now() - timedelta(days=3))
        self._acepta_marketing(timezone.now() - timedelta(days=3))

    def _acepta_marketing(self, cuando):
        AceptacionLegal.objects.create(cliente=self.cliente, correo='ana@example.com', aceptado_en=cuando,
                                       origen='FORM_COTIZACION', finalidades_aceptadas=['MARKETING'])

    def _correr(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()), \
                patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            call_command('enviar_seguimientos', stdout=StringIO())
        return ComunicacionCliente.objects.filter(tipo='SEGUIMIENTO').count()

    def test_con_baja_no_se_manda_el_seguimiento(self):
        BajaWhatsApp.objects.create(telefono=TEL_CLIENTE, origen='PALABRA')
        self.assertEqual(self._correr(), 0)

    def test_si_vuelve_a_aceptar_promociones_la_baja_deja_de_valer(self):
        BajaWhatsApp.objects.create(telefono=TEL_CLIENTE, origen='PALABRA',
                                    created_at=timezone.now() - timedelta(days=2))
        self._acepta_marketing(timezone.now() - timedelta(days=1))
        self.assertEqual(self._correr(), 1)

    def test_la_baja_sobrevive_a_la_purga_de_conversaciones(self):
        conv = services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='BAJA', wamid='w1')
        BajaWhatsApp.objects.create(telefono=TEL_CLIENTE, origen='PALABRA')
        conv.delete()
        self.assertTrue(dado_de_baja(TEL_CLIENTE, self.cliente))
