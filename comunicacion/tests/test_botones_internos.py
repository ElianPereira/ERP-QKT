"""El webhook entrega el ID de los botones internos (no su título) y el abono llega de punta a punta."""
import hashlib
import hmac
import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from comercial import (
    services_cancelacion,
    services_ordenes_wa,
    services_terminal,
)
from comercial.models import Cliente, Pago
from comunicacion import (
    services_agente,
    services_arco,
    services_contador,
    services_direccion,
    services_encuesta,
    services_inspeccion,
    services_pendientes,
    services_preguntas,
    services_visitas,
    views_whatsapp,
)
from comunicacion.models import ConversacionWhatsApp, Responsable
from contabilidad import services_abonos
from contabilidad.models import (
    AbonoIdentificado,
    CuentaBancaria,
    CuentaContable,
    EstadoCuentaBancario,
    MovimientoEstadoCuenta,
    UnidadNegocio,
)
from contabilidad.services_abonos import identificar_abonos
from contabilidad.tests import _crear_cotizacion
from cumplimiento import services_renovacion
from marketing.services import medios

from .test_agente import AGENTE, SECRETO
from .utils import wa_settings

TOKEN = 'T' * 24
TEL_DIRECCION = '525511112222'

# Un ID válido de cada módulo que atiende botones internos.
IDS_VALIDOS = {
    'pendientes': (services_pendientes, 'pend:1:ok'),
    'terminal': (services_terminal, f'term:{TOKEN}:ok'),
    'preguntas': (services_preguntas, 'faq:1:ok'),
    'encuesta': (services_encuesta, 'enc:1:5'),
    'renovacion': (services_renovacion, 'vig:1:ok'),
    'inspeccion': (services_inspeccion, 'insp:1:apr'),
    'visitas': (services_visitas, 'vis:1:ok'),
    'ordenes_wa': (services_ordenes_wa, f'ord:{TOKEN}:ok'),
    'cancelacion': (services_cancelacion, f'canc:{TOKEN}:ok'),
    'arco': (services_arco, f'arco:1-{"a" * 32}:ok'),
    'direccion_hal': (services_direccion, 'hal:1:tarea'),
    'direccion_nom': (services_direccion, 'nom:1:ok'),
    'contador': (services_contador, 'cfdi:1:no'),
    'abonos': (services_abonos, f'abo:{TOKEN}:ok'),
    'medios': (medios, 'med:1:si'),
}


def _payload_boton(boton_id, titulo, telefono):
    return {'object': 'whatsapp_business_account', 'entry': [{'changes': [{
        'field': 'messages',
        'value': {
            'contacts': [{'wa_id': telefono, 'profile': {'name': 'Dir'}}],
            'messages': [{'from': telefono, 'id': 'wamid.boton1', 'type': 'interactive', 'interactive': {
                'type': 'button_reply', 'button_reply': {'id': boton_id, 'title': titulo}}}],
        },
    }]}]}


class TextoDelBotonTest(TestCase):
    def test_cada_modulo_con_es_boton_devuelve_el_id_y_no_el_titulo(self):
        for nombre, (modulo, identificador) in IDS_VALIDOS.items():
            with self.subTest(nombre):
                self.assertTrue(modulo.es_boton(identificador), f'{nombre}: el ID de prueba no es válido')
                mensaje = {'type': 'interactive', 'interactive': {
                    'type': 'button_reply', 'button_reply': {'id': identificador, 'title': 'Registrar'}}}
                self.assertEqual(views_whatsapp._texto(mensaje), identificador)

    def test_boton_de_plantilla_devuelve_el_payload(self):
        mensaje = {'type': 'button', 'button': {'payload': f'abo:{TOKEN}:no', 'text': 'No es'}}
        self.assertEqual(views_whatsapp._texto(mensaje), f'abo:{TOKEN}:no')

    def test_un_boton_desconocido_sigue_entregando_el_titulo(self):
        mensaje = {'type': 'interactive', 'interactive': {
            'type': 'button_reply', 'button_reply': {'id': 'otro:1', 'title': 'Hola'}}}
        self.assertEqual(views_whatsapp._texto(mensaje), 'Hola')


@wa_settings(**AGENTE)
class AbonoPorWebhookTest(TestCase):
    def setUp(self):
        cache.clear()
        cuenta_banco = CuentaContable.objects.get(codigo_sat='102.02.01')
        cuenta = CuentaBancaria.objects.create(
            nombre='BBVA Webhook', banco='BBVA', clabe='012345678901234555', cuenta_contable=cuenta_banco,
            unidad_negocio=UnidadNegocio.objects.get(clave='QUINTA'))
        self.estado = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=cuenta, banco='BBVA', periodo_mes=11, periodo_anio=2026, formato='PDF',
            estado='PROCESADO', fecha_corte_real=date(2026, 11, 30), saldo_inicial_estado=Decimal('0.00'),
            saldo_final_estado=Decimal('0.00'))
        self.cot = _crear_cotizacion(Cliente.objects.create(nombre='Ana María López Pérez', tipo_persona='FISICA'),
                                     Decimal('10000.00'))
        Responsable.objects.create(rol='DIRECCION', nombre='Dir', telefono_wa=TEL_DIRECCION)
        MovimientoEstadoCuenta.objects.create(
            estado_cuenta=self.estado, fecha=date(2026, 11, 3), descripcion='SPEI ANA MARIA LOPEZ',
            abono=Decimal('5000.00'))

    def _post(self, payload):
        cuerpo = json.dumps(payload).encode()
        firma = 'sha256=' + hmac.new(SECRETO.encode(), cuerpo, hashlib.sha256).hexdigest()
        return self.client.post(reverse('whatsapp_webhook'), cuerpo, content_type='application/json',
                                HTTP_X_HUB_SIGNATURE_256=firma)

    def _token(self):
        with patch('comunicacion.services_pendientes._enviar_aviso') as aviso:
            identificar_abonos(self.estado)
        return aviso.call_args.kwargs['botones'][0][0].split(':')[1]

    def test_registrar_desde_el_telefono_de_direccion_crea_el_pago(self):
        token = self._token()
        with patch.object(services_agente, 'lanzar_procesamiento'), \
                self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self._post(_payload_boton(f'abo:{token}:ok', 'Registrar', TEL_DIRECCION)).status_code, 200)
        conv = ConversacionWhatsApp.objects.get(telefono=TEL_DIRECCION)
        with patch.object(services_agente, '_enviar_automatico') as enviar:
            services_agente._atender_pendientes(conv.pk)

        pago = Pago.objects.get(cotizacion=self.cot)
        self.assertEqual((pago.monto, pago.metodo), (Decimal('5000.00'), 'TRANSFERENCIA'))
        self.assertEqual(AbonoIdentificado.objects.get().estado, 'REGISTRADO')
        self.assertIn('Listo', enviar.call_args.args[1])

    def test_el_mismo_boton_desde_otro_telefono_no_registra_nada(self):
        token = self._token()
        with patch.object(services_agente, 'lanzar_procesamiento'), \
                self.captureOnCommitCallbacks(execute=True):
            self._post(_payload_boton(f'abo:{token}:ok', 'Registrar', '525533334444'))
        conv = ConversacionWhatsApp.objects.get(telefono='525533334444')
        with patch.object(services_agente, '_enviar_automatico') as enviar:
            services_agente._atender_pendientes(conv.pk)
        self.assertFalse(Pago.objects.exists())
        enviar.assert_not_called()
