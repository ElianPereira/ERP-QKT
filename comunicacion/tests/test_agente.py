"""Agente de WhatsApp con IA (Issue #346): webhook, reglas de cuándo contesta,
herramientas de consulta y bucle con el modelo (simulado, nunca la API real)."""
import copy
import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import ConstanteSistema, Cotizacion, Producto, ProductoComponente
from comunicacion import herramientas_agente, services_agente
from comunicacion.models import ConversacionWhatsApp, MensajeWhatsApp

from .utils import TEL_CLIENTE, wa_settings

SECRETO = 'app-secret-de-prueba'
TOKEN = 'token-verificacion-prueba'
AGENTE = dict(WA_APP_SECRET=SECRETO, WA_WEBHOOK_VERIFY_TOKEN=TOKEN, WA_AGENTE_ACTIVO=True,
              WA_AGENTE_NUMEROS_PRUEBA=[], WA_AGENTE_MODELO='claude-opus-5-5',
              ALERTAS_INTERNAS_EMAIL=['equipo@qkt.test'])


def _payload_mensaje(texto='Hola', wamid='wamid.1', telefono='5215555550001'):
    return {'object': 'whatsapp_business_account', 'entry': [{'changes': [{
        'field': 'messages',
        'value': {
            'contacts': [{'wa_id': telefono, 'profile': {'name': 'Ana'}}],
            'messages': [{'from': telefono, 'id': wamid, 'type': 'text', 'text': {'body': texto}}],
        },
    }]}]}


def _payload_eco(texto='Te marco en un rato', wamid='wamid.eco1', telefono='5215555550001'):
    return {'entry': [{'changes': [{
        'field': 'smb_message_echoes',
        'value': {'message_echoes': [{'to': telefono, 'id': wamid, 'type': 'text', 'text': {'body': texto}}]},
    }]}]}


class Bloque(SimpleNamespace):
    def to_dict(self, exclude_none=False):
        return {k: v for k, v in vars(self).items() if v is not None}


def _respuesta(*bloques, stop='end_turn'):
    return SimpleNamespace(content=list(bloques), stop_reason=stop)


def _texto(t):
    return Bloque(type='text', text=t)


def _uso(nombre, entrada, id_='toolu_1'):
    return Bloque(type='tool_use', id=id_, name=nombre, input=entrada)


def _cliente_falso(*respuestas):
    """Cliente de Anthropic simulado. `enviados` guarda una copia de `messages`
    de cada llamada: el agente sigue agregando a la misma lista después."""
    cliente = MagicMock()
    cliente.enviados = []
    pendientes = list(respuestas)

    def _crear(**kwargs):
        cliente.enviados.append(copy.deepcopy(kwargs['messages']))
        return pendientes.pop(0)
    cliente.beta.messages.create.side_effect = _crear
    return cliente


class WebhookTest(TestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse('whatsapp_webhook')

    def _post(self, payload, secreto=SECRETO):
        cuerpo = json.dumps(payload).encode()
        firma = 'sha256=' + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
        return self.client.post(self.url, cuerpo, content_type='application/json',
                                HTTP_X_HUB_SIGNATURE_256=firma)

    @wa_settings(**AGENTE)
    def test_verificacion_de_meta_solo_con_el_token_correcto(self):
        ok = self.client.get(self.url, {'hub.mode': 'subscribe', 'hub.verify_token': TOKEN,
                                        'hub.challenge': '12345'})
        self.assertEqual((ok.status_code, ok.content), (200, b'12345'))
        mal = self.client.get(self.url, {'hub.mode': 'subscribe', 'hub.verify_token': 'otro',
                                         'hub.challenge': '12345'})
        self.assertEqual(mal.status_code, 403)

    @wa_settings(**{**AGENTE, 'WA_WEBHOOK_VERIFY_TOKEN': ''})
    def test_sin_token_configurado_no_se_verifica_nada(self):
        r = self.client.get(self.url, {'hub.mode': 'subscribe', 'hub.verify_token': '',
                                       'hub.challenge': '1'})
        self.assertEqual(r.status_code, 403)

    @wa_settings(**AGENTE)
    def test_firma_invalida_se_rechaza_y_no_guarda(self):
        r = self._post(_payload_mensaje(), secreto='otro-secreto')
        self.assertEqual(r.status_code, 403)
        self.assertFalse(MensajeWhatsApp.objects.exists())

    @wa_settings(**{**AGENTE, 'WA_APP_SECRET': ''})
    def test_sin_app_secret_todo_se_rechaza(self):
        self.assertEqual(self._post(_payload_mensaje(), secreto='x').status_code, 403)

    @wa_settings(**AGENTE)
    def test_guarda_el_mensaje_y_lanza_el_agente_una_sola_vez_aunque_meta_reintente(self):
        with patch.object(services_agente, 'lanzar_procesamiento') as lanzar:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self._post(_payload_mensaje()).status_code, 200)
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self._post(_payload_mensaje()).status_code, 200)
        conv = ConversacionWhatsApp.objects.get()
        self.assertEqual((conv.telefono, conv.nombre), (TEL_CLIENTE, 'Ana'))
        self.assertEqual(conv.mensajes.count(), 1)
        lanzar.assert_called_once_with(conv.pk)

    @wa_settings(**AGENTE)
    def test_eco_del_propietario_pausa_al_agente(self):
        with patch.object(services_agente, 'lanzar_procesamiento'):
            self._post(_payload_eco())
        conv = ConversacionWhatsApp.objects.get()
        self.assertTrue(conv.agente_en_pausa())
        self.assertEqual(conv.mensajes.get().direccion, 'HUMANO')


@wa_settings(**AGENTE)
class ProcesarConversacionTest(TestCase):
    def setUp(self):
        cache.clear()
        self.conv = services_agente.recibir_mensaje(
            telefono=TEL_CLIENTE, nombre='Ana', texto='¿Cuánto cuesta una pasadía?', wamid='w1')

    def _procesar(self, cliente):
        with patch.object(services_agente, '_cliente_ia', return_value=cliente), \
                patch.object(services_agente, 'enviar_whatsapp',
                             return_value=SimpleNamespace(proveedor_id='')) as enviar:
            services_agente.procesar_conversacion(self.conv.pk)
        return enviar

    def test_apagado_no_llama_al_modelo_pero_marca_procesado(self):
        cliente = _cliente_falso()
        with self.settings(WA_AGENTE_ACTIVO=False):
            enviar = self._procesar(cliente)
        cliente.beta.messages.create.assert_not_called()
        enviar.assert_not_called()
        self.assertFalse(self.conv.mensajes.filter(procesado=False).exists())

    def test_numero_fuera_de_la_lista_de_prueba_no_se_contesta(self):
        cliente = _cliente_falso()
        with self.settings(WA_AGENTE_NUMEROS_PRUEBA=['9999999999']):
            enviar = self._procesar(cliente)
        enviar.assert_not_called()

    def test_numero_en_la_lista_de_prueba_si_se_contesta(self):
        cliente = _cliente_falso(_respuesta(_texto('¡Hola! Soy el asistente virtual.')))
        with self.settings(WA_AGENTE_NUMEROS_PRUEBA=[TEL_CLIENTE[-10:]]):
            enviar = self._procesar(cliente)
        self.assertEqual(enviar.call_count, 2)  # aviso inicial + respuesta

    def test_no_le_contesta_al_numero_del_propietario(self):
        cliente = _cliente_falso()
        with self.settings(WA_NUMERO_NEGOCIO=TEL_CLIENTE[-10:]):
            enviar = self._procesar(cliente)
        cliente.beta.messages.create.assert_not_called()
        enviar.assert_not_called()

    def test_en_pausa_por_respuesta_humana_no_contesta(self):
        self.conv.pausado_hasta = timezone.now() + timedelta(hours=2)
        self.conv.save()
        enviar = self._procesar(_cliente_falso())
        enviar.assert_not_called()

    def test_turno_con_herramienta_contesta_y_guarda_historial_append_only(self):
        cliente = _cliente_falso(
            _respuesta(_uso('preguntas_frecuentes', {}), stop='tool_use'),
            _respuesta(_texto('La pasadía es de 11 a 7.')),
        )
        enviar = self._procesar(cliente)

        self.assertEqual(enviar.call_count, 2)
        self.assertEqual(enviar.call_args.kwargs['mensaje'], 'La pasadía es de 11 a 7.')
        self.conv.refresh_from_db()
        roles = [m['role'] for m in self.conv.historial]
        self.assertEqual(roles, ['user', 'assistant', 'user', 'assistant'])
        self.assertIn('Hoy es', self.conv.historial[0]['content'][0]['text'])
        self.assertEqual(self.conv.historial[2]['content'][0]['type'], 'tool_result')
        # El segundo llamado reenvía el primero intacto (append-only).
        primero, segundo = cliente.enviados
        self.assertEqual(segundo[:len(primero)], primero)
        self.assertEqual(self.conv.mensajes.filter(direccion='AGENTE').count(), 2)

    def test_pasar_a_humano_avisa_al_equipo_y_despues_calla(self):
        cliente = _cliente_falso(
            _respuesta(_uso('pasar_a_humano', {'motivo': 'Quiere negociar precio'}), stop='tool_use'),
            _respuesta(_texto('Te comunico con alguien del equipo.')),
        )
        self._procesar(cliente)
        self.conv.refresh_from_db()
        self.assertTrue(self.conv.requiere_humano)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Quiere negociar precio', mail.outbox[0].body)

        services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='¿Hola?', wamid='w2')
        otro = _cliente_falso()
        enviar = self._procesar(otro)
        otro.beta.messages.create.assert_not_called()
        enviar.assert_not_called()

    def test_el_primer_contacto_avisa_que_es_ia_y_enlaza_el_aviso_una_sola_vez(self):
        enviar = self._procesar(_cliente_falso(_respuesta(_texto('Hola, ¿para cuántas personas?'))))
        primero = enviar.call_args_list[0].kwargs['mensaje']
        self.assertEqual(primero, services_agente.AVISO_INICIAL)
        self.assertIn('inteligencia artificial', primero)
        self.assertIn(services_agente.URL_AVISO_PRIVACIDAD, primero)

        services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='Somos 15', wamid='w-2')
        enviar = self._procesar(_cliente_falso(_respuesta(_texto('Perfecto.'))))
        enviar.assert_called_once_with(tipo='AGENTE_IA', telefono=self.conv.telefono,
                                       mensaje='Perfecto.', trigger='SIGNAL')

    def _pasado_a_humano_hace(self, horas):
        """Conversación que el agente pasó a una persona hace `horas`."""
        self.conv.mensajes.update(procesado=True)
        self.conv.requiere_humano = True
        self.conv.save()
        salida = MensajeWhatsApp.objects.create(
            conversacion=self.conv, direccion='AGENTE', texto='Te comunico con alguien.', procesado=True)
        MensajeWhatsApp.objects.filter(pk=salida.pk).update(
            created_at=timezone.now() - timedelta(hours=horas))
        services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='¿Siguen ahí?',
                                        wamid=f'w-espera-{horas}')

    def test_quien_espera_a_una_persona_recibe_aviso_sin_gastar_ia(self):
        self._pasado_a_humano_hace(4)
        cliente = _cliente_falso()
        enviar = self._procesar(cliente)
        cliente.beta.messages.create.assert_not_called()
        self.assertEqual(enviar.call_args.kwargs['mensaje'], services_agente.AVISO_ESPERA)

        # Un segundo mensaje enseguida no repite el aviso.
        services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='?', wamid='w-otra')
        self._procesar(cliente).assert_not_called()

    def test_recien_pasado_a_humano_no_manda_aviso(self):
        self._pasado_a_humano_hace(1)
        self._procesar(_cliente_falso()).assert_not_called()

    def test_sin_atencion_en_24_h_el_agente_se_reactiva_solo(self):
        self._pasado_a_humano_hace(25)
        # Un aviso de espera reciente no cuenta como atención del equipo.
        aviso = MensajeWhatsApp.objects.create(
            conversacion=self.conv, direccion='AGENTE', texto=services_agente.AVISO_ESPERA, procesado=True)
        MensajeWhatsApp.objects.filter(pk=aviso.pk).update(created_at=timezone.now() - timedelta(hours=5))
        cliente = _cliente_falso(_respuesta(_texto('¡Hola de nuevo! ¿En qué te ayudo?')))
        enviar = self._procesar(cliente)
        self.conv.refresh_from_db()
        self.assertFalse(self.conv.requiere_humano)
        self.assertEqual(enviar.call_args.kwargs['mensaje'], '¡Hola de nuevo! ¿En qué te ayudo?')
        self.assertIn('¿Siguen ahí?', cliente.enviados[0][-1]['content'][0]['text'])

    def test_si_el_equipo_contesto_no_se_reactiva_ni_avisa(self):
        self._pasado_a_humano_hace(30)
        MensajeWhatsApp.objects.create(
            conversacion=self.conv, direccion='HUMANO', texto='Te marco en un rato', procesado=True)
        self.conv.pausado_hasta = timezone.now() + timedelta(hours=12)
        self.conv.save()
        cliente = _cliente_falso()
        self._procesar(cliente).assert_not_called()
        self.conv.refresh_from_db()
        self.assertTrue(self.conv.requiere_humano)

    def test_error_de_la_api_pasa_a_humano_sin_romper(self):
        import anthropic
        cliente = MagicMock()
        cliente.beta.messages.create.side_effect = anthropic.APIConnectionError(request=MagicMock())
        enviar = self._procesar(cliente)
        self.conv.refresh_from_db()
        self.assertTrue(self.conv.requiere_humano)
        self.assertEqual(enviar.call_args.kwargs['mensaje'], services_agente.MENSAJE_HUMANO)

    def test_la_respuesta_humana_previa_llega_al_agente_como_contexto(self):
        services_agente.registrar_respuesta_humana(telefono=TEL_CLIENTE, texto='Es $2,000', wamid='e1')
        self.conv.refresh_from_db()
        self.conv.pausado_hasta = None
        self.conv.save()
        cliente = _cliente_falso(_respuesta(_texto('Así es.')))
        self._procesar(cliente)
        enviado = cliente.enviados[0][-1]['content'][0]['text']
        self.assertIn('Equipo QKT: Es $2,000', enviado)
        self.assertIn('Cliente: ¿Cuánto cuesta una pasadía?', enviado)


class HerramientasTest(TestCase):
    def setUp(self):
        cache.clear()
        self.basico = Producto.objects.create(
            nombre='Pasadía Básico', precio_venta_fijo=Decimal('1724.14'), visible_cotizador=True,
            cotizador_pasadia=True, rol_cotizador='BASE_PASADIA_BASICO', descripcion='Alberca y mesas',
        )
        self.paquete = Producto.objects.create(
            nombre='Paquete Esencial', precio_venta_fijo=Decimal('9000.00'), visible_cotizador=True,
            cotizador_evento=True, es_paquete=True,
        )

    def _total_web(self, **params):
        return self.client.get(reverse('api_total_cotizador'), params).json()['total_formateado']

    def test_el_precio_del_agente_es_el_mismo_del_cotizador_web(self):
        agente = herramientas_agente.cotizar_estimado(servicio='PASADIA', personas=15)
        self.assertEqual(agente['total'], self._total_web(servicio='PASADIA', personas=15))
        self.assertEqual(agente['total'], '$2,000.00')

        evento = herramientas_agente.cotizar_estimado(
            servicio='EVENTO', personas=40, paquete_id=self.paquete.id, horas=7)
        self.assertEqual(evento['total'], self._total_web(
            servicio='EVENTO', personas=40, paquete=self.paquete.id, horas=7))

    def test_topes_de_aforo_iguales_al_cotizador(self):
        self.assertIn('error', herramientas_agente.cotizar_estimado(servicio='PASADIA', personas=31))
        self.assertIn('error', herramientas_agente.cotizar_estimado(
            servicio='EVENTO', personas=151, paquete_id=self.paquete.id))
        self.assertIn('error', herramientas_agente.cotizar_estimado(
            servicio='EVENTO', personas=80, paquete_id=999999))

    def test_solo_la_renta_del_lugar_con_el_minimo_del_cotizador_web(self):
        renta = Producto.objects.create(
            nombre='Renta de la Quinta', precio_venta_fijo=Decimal('3448.28'),
            rol_cotizador='BASE_EVENTO', descripcion='Uso del lugar por 6 horas',
        )
        r = herramientas_agente.cotizar_estimado(servicio='EVENTO', personas=60)
        self.assertEqual(r['total'], '$4,000.00')
        self.assertEqual(r['total'], self._total_web(servicio='EVENTO', personas=60))
        self.assertIn('error', herramientas_agente.cotizar_estimado(servicio='EVENTO', personas=30))

        opcion = herramientas_agente.ver_opciones(servicio='EVENTO', personas=30)['solo_renta']
        self.assertEqual((opcion['nombre'], opcion['precio_total']), (renta.nombre, '$4,000.00'))
        self.assertEqual(opcion['minimo_personas'], 31)

    def test_disponibilidad_no_revela_datos_de_otra_reservacion(self):
        manana = (timezone.localdate() + timedelta(days=10)).isoformat()
        with patch.object(herramientas_agente, 'verificar_disponibilidad_rango',
                          return_value=(False, 'ya existe un evento COT-007 de Juan')):
            r = herramientas_agente.consultar_disponibilidad(fecha=manana)
        self.assertFalse(r['disponible'])
        self.assertNotIn('COT', json.dumps(r))

    def test_fecha_pasada_o_mal_escrita_regresa_error(self):
        self.assertIn('error', herramientas_agente.consultar_disponibilidad(fecha='2020-01-01'))
        self.assertIn('error', herramientas_agente.consultar_disponibilidad(fecha='sábado'))

    def test_ver_opciones_lista_niveles_con_su_descripcion_del_admin(self):
        r = herramientas_agente.ver_opciones(servicio='PASADIA')
        self.assertEqual(r['niveles'][0]['incluye']['descripcion'], 'Alberca y mesas')
        self.assertEqual(r['niveles'][0]['precio_total'], '$2,000.00')

    def test_ver_opciones_dice_que_productos_trae_cada_paquete(self):
        for nombre in ('Sillas Tiffany', 'Mesero'):
            ProductoComponente.objects.create(
                producto_padre=self.paquete, cantidad=Decimal('1'),
                producto_hijo=Producto.objects.create(nombre=nombre, precio_venta_fijo=Decimal('10')),
            )
        paquete = herramientas_agente.ver_opciones(servicio='EVENTO', personas=50)['paquetes'][0]
        self.assertEqual(paquete['incluye']['productos_incluidos'], ['Mesero', 'Sillas Tiffany'])

    def test_condiciones_de_pago_salen_de_las_reglas_del_erp(self):
        r = herramientas_agente.condiciones_de_pago(servicio='EVENTO')
        self.assertEqual(r['primer_pago_minimo'], '50% del total')
        self.assertEqual(r['aparta_la_fecha'], 'Con el primer pago.')
        self.assertIn(f"{Cotizacion.DIAS_PAGO_TOTAL['EVENTO']} días", r['liquidar'])

        lejos = timezone.localdate() + timedelta(days=60)
        r = herramientas_agente.condiciones_de_pago(servicio='PASADIA', fecha=lejos.isoformat())
        self.assertEqual(r['fecha_limite_para_liquidar'],
                         (lejos - timedelta(days=Cotizacion.DIAS_PAGO_TOTAL['PASADIA'])).isoformat())
        self.assertFalse(r['paga_total_desde_el_inicio'])

        cerca = (timezone.localdate() + timedelta(days=3)).isoformat()
        self.assertTrue(herramientas_agente.condiciones_de_pago(
            servicio='PASADIA', fecha=cerca)['paga_total_desde_el_inicio'])
        self.assertIn('error', herramientas_agente.condiciones_de_pago(servicio='BODA'))

    def test_el_apartado_no_contradice_el_minimo_del_primer_pago(self):
        # Un % de apartado menor al 50% del primer pago (dato viejo en producción)
        # no debe llegarle al cliente como una cifra distinta.
        constante = ConstanteSistema.objects.create(clave='PORCENTAJE_ANTICIPO_MINIMO', valor=Decimal('30'))
        r = herramientas_agente.condiciones_de_pago(servicio='EVENTO')
        self.assertEqual(r['aparta_la_fecha'], 'Con el primer pago.')
        self.assertNotIn('30%', json.dumps(r, ensure_ascii=False))

        constante.valor = Decimal('70')
        constante.save()
        r = herramientas_agente.condiciones_de_pago(servicio='EVENTO')
        self.assertEqual(r['aparta_la_fecha'], 'Al llevar pagado el 70% del total.')

    def test_las_preguntas_frecuentes_sembradas_llegan_al_agente(self):
        preguntas = {p['pregunta'] for p in herramientas_agente.preguntas_frecuentes()['preguntas']}
        self.assertIn('¿Puedo llevar a mi mascota?', preguntas)
        self.assertIn('¿Incluyen hielo?', preguntas)

    def test_ejecutar_herramienta_desconocida_es_error(self):
        salida, error = herramientas_agente.ejecutar('borrar_todo', {})
        self.assertTrue(error)
        self.assertIn('desconocida', salida)


@wa_settings(**AGENTE)
class ResponderComoPersonaTest(TestCase):
    """Sin coexistencia, el equipo contesta desde Conversaciones en el admin."""

    def setUp(self):
        cache.clear()
        from django.contrib.auth.models import User

        from core_erp.test_utils import login_superuser_con_totp
        self.usuario = User.objects.create_superuser('direccion', 'd@qkt.test', 'x')
        login_superuser_con_totp(self.client, self.usuario)
        self.conv = services_agente.recibir_mensaje(
            telefono=TEL_CLIENTE, nombre='Ana', texto='¿Me atiende alguien?', wamid='w1')
        self.url = reverse('admin:comunicacion_conversacionwhatsapp_change', args=[self.conv.pk])

    def _guardar(self, texto):
        return self.client.post(self.url, {
            'cliente': '', 'responder': texto, 'requiere_humano': 'on', 'motivo_humano': '',
            'pausado_hasta_0': '', 'pausado_hasta_1': '',
            'mensajes-TOTAL_FORMS': '1', 'mensajes-INITIAL_FORMS': '1',
            'mensajes-MIN_NUM_FORMS': '0', 'mensajes-MAX_NUM_FORMS': '1000',
            'mensajes-0-id': str(self.conv.mensajes.get().pk), 'mensajes-0-conversacion': str(self.conv.pk),
        }, follow=True)

    def test_responder_desde_el_admin_manda_whatsapp_y_pausa_al_agente(self):
        with patch.object(services_agente, 'enviar_whatsapp',
                          return_value=SimpleNamespace(estado='ENVIADO', proveedor_id='wamid.h1')) as enviar:
            r = self._guardar('Hola Ana, soy Elián. Te ayudo.')
        self.assertEqual(r.status_code, 200)
        enviar.assert_called_once()
        self.assertEqual(enviar.call_args.kwargs['mensaje'], 'Hola Ana, soy Elián. Te ayudo.')
        respuesta = self.conv.mensajes.get(direccion='HUMANO')
        self.assertEqual(respuesta.enviado_por, self.usuario)
        self.conv.refresh_from_db()
        self.assertTrue(self.conv.agente_en_pausa())

    def test_ventana_de_24_horas_cerrada_no_manda_nada(self):
        self.conv.mensajes.update(created_at=timezone.now() - timedelta(hours=25))
        with patch.object(services_agente, 'enviar_whatsapp') as enviar:
            r = self._guardar('¿Sigues ahí?')
        enviar.assert_not_called()
        self.assertContains(r, 'No se envió la respuesta')
        self.assertFalse(self.conv.mensajes.filter(direccion='HUMANO').exists())

    def test_si_meta_rechaza_no_se_registra_como_enviada(self):
        with patch.object(services_agente, 'enviar_whatsapp',
                          return_value=SimpleNamespace(estado='FALLIDO', error='Meta 131047', proveedor_id='')):
            r = self._guardar('Hola')
        self.assertContains(r, 'Meta 131047')
        self.assertFalse(self.conv.mensajes.filter(direccion='HUMANO').exists())
