"""Tests del transporte: normalización, errores de Meta, plantillas e idempotencia."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import requests
from django.core import mail
from django.db import IntegrityError
from django.test import TestCase, override_settings

from comercial.models import Cliente, Cotizacion
from comunicacion.models import ComunicacionCliente
from comunicacion.services import (
    WA_ERRORES_META,
    alertar_equipo_email,
    alertar_equipo_fecha_chocada,
    enviar_email,
    enviar_whatsapp,
    enviar_whatsapp_template,
    normalizar_telefono_wa,
    numero_emisor_wa,
    remitente_por_tipo,
    reservar_comunicacion,
    telefono_seguro,
    texto_plano_wa,
)

from .utils import (
    TEL_CLIENTE,
    TEL_EMISOR,
    RespuestaFalsa,
    error_meta,
    limpiar_cache_emisor,
    wa_settings,
)


class NormalizarTelefonoTest(TestCase):
    def test_formatos_mexicanos(self):
        self.assertEqual(normalizar_telefono_wa('9991234567'), '5219991234567')
        self.assertEqual(normalizar_telefono_wa('529991234567'), '5219991234567')
        self.assertEqual(normalizar_telefono_wa('5219991234567'), '5219991234567')

    def test_ignora_separadores(self):
        self.assertEqual(normalizar_telefono_wa('(999) 123-45 67'), '5219991234567')
        self.assertEqual(normalizar_telefono_wa('+52 999 123 4567'), '5219991234567')

    def test_valores_invalidos(self):
        for entrada in ('', None, 'sin dígitos', '123', '12345678'):
            self.assertEqual(normalizar_telefono_wa(entrada), '', entrada)

    def test_no_inventa_prefijo_para_numeros_cortos(self):
        # La implementación anterior convertía 8 dígitos en '5219999'+número,
        # fabricando un destinatario que podía ser una persona real distinta.
        self.assertEqual(normalizar_telefono_wa('12345678'), '')

    def test_telefono_seguro_solo_expone_ultimos_cuatro(self):
        seguro = telefono_seguro('5219991234567')
        self.assertEqual(seguro, '…4567')
        self.assertNotIn('999123', seguro)


class TextoPlanoTest(TestCase):
    def test_colapsa_saltos_de_linea_y_espacios(self):
        # Meta rechaza los parámetros de plantilla con saltos de línea.
        self.assertEqual(texto_plano_wa('Boda\nJardín\t\tprincipal'), 'Boda Jardín principal')
        self.assertEqual(texto_plano_wa('  hola     mundo  '), 'hola mundo')

    def test_valores_no_texto(self):
        self.assertEqual(texto_plano_wa(None), '')
        self.assertEqual(texto_plano_wa(Decimal('1234.50')), '1234.50')


@wa_settings()
class TransporteWhatsAppTest(TestCase):
    def setUp(self):
        limpiar_cache_emisor()
        self.cliente = Cliente.objects.create(nombre='Ana Ruiz', telefono=TEL_CLIENTE)
        self.cot = Cotizacion.objects.create(
            cliente=self.cliente,
            nombre_evento='Boda Test',
            fecha_evento=date.today() + timedelta(days=45),
            num_personas=80,
            precio_final=Decimal('30000.00'),
        )

    def _enviar(self, respuesta):
        with patch('comunicacion.services.requests.post', return_value=respuesta) as post, \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            comm = enviar_whatsapp(
                cotizacion=self.cot, tipo='OTRO',
                telefono=TEL_CLIENTE, mensaje='hola',
            )
        return comm, post

    def test_envio_correcto_guarda_proveedor_id(self):
        comm, post = self._enviar(RespuestaFalsa())
        self.assertEqual(comm.estado, 'ENVIADO')
        self.assertEqual(comm.proveedor_id, 'wamid.TEST')
        self.assertEqual(post.call_count, 1)
        self.assertIn('/v20.0/PHONE_ID_TEST/messages', post.call_args.args[0])

    def test_errores_de_meta_quedan_auditados_con_su_codigo(self):
        for codigo in (100, 131026, 131030, 131047, 132001, 133010, 190):
            with self.subTest(codigo=codigo):
                comm, _ = self._enviar(error_meta(codigo))
                self.assertEqual(comm.estado, 'FALLIDO')
                self.assertIn(f'Meta {codigo}', comm.error)
                self.assertIn(WA_ERRORES_META[codigo], comm.error)
                self.assertIn('TRACE_TEST', comm.error)

    def test_token_invalido(self):
        comm, _ = self._enviar(error_meta(190, 'token inválido', status_code=401))
        self.assertEqual(comm.estado, 'FALLIDO')
        self.assertIn('HTTP 401', comm.error)
        self.assertIn('token permanente', comm.error)

    def test_numero_emisor_sin_registrar(self):
        # Visto en producción: el número estaba en la WABA pero sin el PIN de
        # verificación en dos pasos, así que la Cloud API lo rechazaba todo.
        comm, _ = self._enviar(error_meta(133010, '(#133010) Account not registered'))
        self.assertEqual(comm.estado, 'FALLIDO')
        self.assertIn('Meta 133010', comm.error)
        self.assertIn('PIN de verificación en dos pasos', comm.error)

    def test_rate_limit(self):
        comm, _ = self._enviar(error_meta(131056, status_code=429))
        self.assertEqual(comm.estado, 'FALLIDO')
        self.assertIn('HTTP 429', comm.error)

    def test_timeout_no_propaga_excepcion(self):
        with patch('comunicacion.services.requests.post', side_effect=requests.Timeout('timeout')), \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            comm = enviar_whatsapp(
                cotizacion=self.cot, tipo='OTRO', telefono=TEL_CLIENTE, mensaje='hola',
            )
        self.assertEqual(comm.estado, 'FALLIDO')
        self.assertTrue(comm.error)

    def test_telefono_invalido_no_llama_a_meta(self):
        with patch('comunicacion.services.requests.post') as post:
            comm = enviar_whatsapp(
                cotizacion=self.cot, tipo='OTRO', telefono='abc', mensaje='hola',
            )
        post.assert_not_called()
        self.assertEqual(comm.estado, 'FALLIDO')

    @wa_settings(WA_CLOUD_API_TOKEN='')
    def test_sin_credenciales_no_llama_a_meta(self):
        with patch('comunicacion.services.requests.post') as post:
            comm = enviar_whatsapp(
                cotizacion=self.cot, tipo='OTRO', telefono=TEL_CLIENTE, mensaje='hola',
            )
        post.assert_not_called()
        self.assertEqual(comm.estado, 'FALLIDO')
        self.assertIn('no configurado', comm.error)


@wa_settings()
class GuardaEmisorTest(TestCase):
    """El emisor no puede ser también el destinatario (Meta 131021)."""

    def setUp(self):
        limpiar_cache_emisor()
        self.cliente = Cliente.objects.create(nombre='Ana', telefono=TEL_EMISOR)
        self.cot = Cotizacion.objects.create(
            cliente=self.cliente, nombre_evento='Boda',
            fecha_evento=date.today() + timedelta(days=30),
            num_personas=50, precio_final=Decimal('1000.00'),
        )

    def test_destino_igual_al_emisor_no_llama_a_messages(self):
        respuesta_emisor = RespuestaFalsa(payload={'display_phone_number': TEL_EMISOR})
        with patch('comunicacion.services.requests.get', return_value=respuesta_emisor), \
             patch('comunicacion.services.requests.post') as post:
            comm = enviar_whatsapp(
                cotizacion=self.cot, tipo='OTRO', telefono=TEL_EMISOR, mensaje='hola',
            )
        post.assert_not_called()
        self.assertEqual(comm.estado, 'FALLIDO')
        self.assertIn('131021', comm.error)

    def test_el_numero_emisor_se_resuelve_una_sola_vez(self):
        respuesta_emisor = RespuestaFalsa(payload={'display_phone_number': TEL_EMISOR})
        with patch('comunicacion.services.requests.get', return_value=respuesta_emisor) as get:
            self.assertEqual(numero_emisor_wa(), TEL_EMISOR)
            self.assertEqual(numero_emisor_wa(), TEL_EMISOR)
        self.assertEqual(get.call_count, 1)

    def test_fallo_al_resolver_el_emisor_no_bloquea_el_envio(self):
        with patch('comunicacion.services.requests.get', side_effect=requests.Timeout()), \
             patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()) as post:
            comm = enviar_whatsapp(
                cotizacion=self.cot, tipo='OTRO', telefono=TEL_CLIENTE, mensaje='hola',
            )
        post.assert_called_once()
        self.assertEqual(comm.estado, 'ENVIADO')

    def test_un_fallo_de_resolucion_no_se_cachea(self):
        with patch('comunicacion.services.requests.get', side_effect=requests.Timeout()) as get:
            self.assertEqual(numero_emisor_wa(), '')
            self.assertEqual(numero_emisor_wa(), '')
        self.assertEqual(get.call_count, 2)


@wa_settings()
class PlantillasTest(TestCase):
    def setUp(self):
        limpiar_cache_emisor()
        self.cliente = Cliente.objects.create(nombre='Ana', telefono=TEL_CLIENTE)
        self.cot = Cotizacion.objects.create(
            cliente=self.cliente, nombre_evento='Boda',
            fecha_evento=date.today() + timedelta(days=30),
            num_personas=50, precio_final=Decimal('1000.00'),
        )

    def test_payload_de_plantilla(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()) as post, \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            comm = enviar_whatsapp_template(
                cotizacion=self.cot, tipo='COTIZACION', telefono=TEL_CLIENTE,
                template_name='qkt_cotizacion_lista',
                parametros=['Ana', 'Boda\ncivil', '01/01/2027', '1,000.00', 'https://portal.test/x/'],
            )
        self.assertEqual(comm.estado, 'ENVIADO')
        enviado = post.call_args.kwargs['json']
        self.assertEqual(enviado['type'], 'template')
        self.assertEqual(enviado['template']['name'], 'qkt_cotizacion_lista')
        self.assertEqual(enviado['template']['language'], {'code': 'es_MX'})
        textos = [p['text'] for p in enviado['template']['components'][0]['parameters']]
        self.assertEqual(len(textos), 5)
        # Ninguna variable puede llevar saltos de línea: Meta rechaza la plantilla.
        for texto in textos:
            self.assertNotIn('\n', texto)
        self.assertEqual(textos[1], 'Boda civil')

    def test_plantilla_sin_configurar_falla_sin_llamar_a_meta(self):
        with patch('comunicacion.services.requests.post') as post:
            comm = enviar_whatsapp_template(
                cotizacion=self.cot, tipo='CONFIRMACION_PAGO', telefono=TEL_CLIENTE,
                template_name='', parametros=['x'],
            )
        post.assert_not_called()
        self.assertEqual(comm.estado, 'FALLIDO')
        self.assertIn('no configurada', comm.error)

    def test_plantilla_inexistente_en_meta(self):
        with patch('comunicacion.services.requests.post', return_value=error_meta(132001)), \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            comm = enviar_whatsapp_template(
                cotizacion=self.cot, tipo='COTIZACION', telefono=TEL_CLIENTE,
                template_name='no_existe', parametros=['x'],
            )
        self.assertEqual(comm.estado, 'FALLIDO')
        self.assertIn('Meta 132001', comm.error)


class IdempotenciaTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nombre='Ana', email='ana@example.com')
        self.cot = Cotizacion.objects.create(
            cliente=self.cliente, nombre_evento='Boda',
            fecha_evento=date.today() + timedelta(days=30),
            num_personas=50, precio_final=Decimal('1000.00'),
        )

    def test_la_clave_repetida_devuelve_none(self):
        campos = dict(
            cotizacion=self.cot, canal='EMAIL', tipo='COTIZACION',
            destinatario='ana@example.com', clave_idempotencia='cotizacion:1:web:email',
        )
        primera = reservar_comunicacion(**campos)
        segunda = reservar_comunicacion(**campos)
        self.assertIsNotNone(primera)
        self.assertIsNone(segunda)
        self.assertEqual(ComunicacionCliente.objects.count(), 1)

    def test_el_integrityerror_no_deja_la_transaccion_rota(self):
        # En PostgreSQL un IntegrityError sin savepoint aborta la transacción
        # envolvente: tras la colisión hay que poder seguir escribiendo.
        campos = dict(
            cotizacion=self.cot, canal='EMAIL', tipo='COTIZACION',
            destinatario='ana@example.com', clave_idempotencia='clave-repetida',
        )
        reservar_comunicacion(**campos)
        self.assertIsNone(reservar_comunicacion(**campos))
        posterior = reservar_comunicacion(
            cotizacion=self.cot, canal='EMAIL', tipo='OTRO',
            destinatario='ana@example.com', clave_idempotencia='otra-clave',
        )
        self.assertIsNotNone(posterior)
        self.assertEqual(ComunicacionCliente.objects.count(), 2)

    def test_sin_clave_no_hay_deduplicacion(self):
        for _ in range(2):
            reservar_comunicacion(
                cotizacion=self.cot, canal='EMAIL', tipo='OTRO',
                destinatario='ana@example.com',
            )
        self.assertEqual(ComunicacionCliente.objects.count(), 2)

    def test_sin_clave_el_integrityerror_se_propaga(self):
        with patch(
            'comunicacion.services.ComunicacionCliente.objects.create',
            side_effect=IntegrityError('otro fallo'),
        ):
            with self.assertRaises(IntegrityError):
                reservar_comunicacion(
                    cotizacion=self.cot, canal='EMAIL', tipo='OTRO',
                    destinatario='ana@example.com',
                )


@override_settings(
    EMAIL_FROM_RESERVAS='reservas@qkt.mx',
    EMAIL_FROM_PAGOS='pagos@qkt.mx',
    EMAIL_FROM_NOTIFICACIONES='notificaciones@qkt.mx',
)
class RemitentePorTipoTest(TestCase):
    """El remitente del email depende del tipo de comunicación (Issue #221)."""

    def setUp(self):
        self.cliente = Cliente.objects.create(nombre='Ana', email='ana@example.com')
        self.cot = Cotizacion.objects.create(
            cliente=self.cliente, nombre_evento='Boda',
            fecha_evento=date.today() + timedelta(days=30),
            num_personas=50, precio_final=Decimal('1000.00'),
        )

    def test_remitente_por_tipo_mapea_cada_tipo(self):
        self.assertEqual(remitente_por_tipo('COTIZACION'), 'reservas@qkt.mx')
        self.assertEqual(remitente_por_tipo('CONFIRMACION_PAGO'), 'pagos@qkt.mx')
        self.assertEqual(remitente_por_tipo('REEMBOLSO'), 'pagos@qkt.mx')
        self.assertEqual(remitente_por_tipo('RECORDATORIO_PAGO'), 'pagos@qkt.mx')
        self.assertEqual(remitente_por_tipo('OTRO'), 'notificaciones@qkt.mx')
        self.assertEqual(remitente_por_tipo('TIPO_FUTURO_NO_MAPEADO'), 'notificaciones@qkt.mx')

    def _enviar(self, tipo, clave):
        return enviar_email(
            cotizacion=self.cot, tipo=tipo, destinatario='ana@example.com',
            asunto='asunto', template='comunicacion/email/cotizacion.html',
            context={}, clave_idempotencia=clave,
        )

    def test_cotizacion_sale_de_reservas(self):
        self._enviar('COTIZACION', 'clave-1')
        self.assertEqual(mail.outbox[-1].from_email, 'reservas@qkt.mx')

    def test_pago_reembolso_y_recordatorio_salen_de_pagos(self):
        for i, tipo in enumerate(('CONFIRMACION_PAGO', 'REEMBOLSO', 'RECORDATORIO_PAGO')):
            with self.subTest(tipo=tipo):
                self._enviar(tipo, f'clave-pago-{i}')
                self.assertEqual(mail.outbox[-1].from_email, 'pagos@qkt.mx')

    def test_otro_sale_de_notificaciones(self):
        self._enviar('OTRO', 'clave-otro')
        self.assertEqual(mail.outbox[-1].from_email, 'notificaciones@qkt.mx')

    @override_settings(
        EMAIL_FROM_RESERVAS='fallback@qkt.mx',
        EMAIL_FROM_PAGOS='fallback@qkt.mx',
        EMAIL_FROM_NOTIFICACIONES='fallback@qkt.mx',
    )
    def test_sin_variables_configuradas_cae_a_default_from_email(self):
        # En settings.py las tres variables nuevas caen a DEFAULT_FROM_EMAIL si
        # no están definidas en el entorno; aquí se simula ese fallback ya
        # resuelto (override_settings no puede "desconfigurar" una variable).
        self._enviar('COTIZACION', 'clave-fallback')
        self.assertEqual(mail.outbox[-1].from_email, 'fallback@qkt.mx')

    def test_alertar_equipo_fecha_chocada_sale_de_notificaciones(self):
        alertar_equipo_fecha_chocada(self.cot, 'mensaje de prueba')
        self.assertEqual(mail.outbox[-1].from_email, 'notificaciones@qkt.mx')

    def test_alertar_equipo_email_sale_de_notificaciones(self):
        alertar_equipo_email(self.cot, asunto='asunto', cuerpo='cuerpo', clave_idempotencia='clave-alerta')
        self.assertEqual(mail.outbox[-1].from_email, 'notificaciones@qkt.mx')
