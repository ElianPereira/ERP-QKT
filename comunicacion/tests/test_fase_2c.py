"""Fase 2c (Issue #432): cancelación por Kooxi, botones de Dirección, ARCO y evals."""
import json
import re
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from comercial import services_cancelacion
from comercial.models import Cliente, Cotizacion, ItemCotizacion, SolicitudCancelacion
from comunicacion import evaluacion_agente, herramientas_agente, services_agente, services_arco
from comunicacion.models import Pendiente, Responsable
from legal.models import SolicitudARCO

from .test_agente import AGENTE, _cliente_falso, _respuesta, _texto
from .utils import wa_settings

TEL_DIR = '525555550012'
TEL_OTRO = '525555550013'
TEL_CLI = '525555550001'  # el cliente está registrado con 5555550001
OK = SimpleNamespace(estado='ENVIADO', proveedor_id='')
CONV = SimpleNamespace(telefono=TEL_CLI)


def _cotizacion():
    cliente = Cliente.objects.create(nombre='Cliente 2c', telefono='5555550001', email='cli@example.com')
    cot = Cotizacion.objects.create(
        cliente=cliente, nombre_evento='Evento', tipo_servicio='EVENTO', incluye_refrescos=False,
        fecha_evento=timezone.localdate() + timedelta(days=60))
    ItemCotizacion.objects.create(cotizacion=cot, descripcion='Servicio', cantidad=1,
                                  precio_unitario=Decimal('10000.00'))
    Cotizacion.objects.filter(pk=cot.pk).update(estado='CONFIRMADA')
    cot.refresh_from_db()
    return cot


def _codigo():
    return re.search(r'(\d{6})', mail.outbox[-1].body).group(1)


class HerramientasCancelacionTest(TestCase):
    def setUp(self):
        Responsable.objects.create(rol='DIRECCION', nombre='Dir', telefono_wa=TEL_DIR)
        self.cot = _cotizacion()
        patch('comunicacion.services_pendientes._enviar_aviso', return_value=OK).start()
        patch('comercial.services_cancelacion.enviar_whatsapp', return_value=OK).start()
        self.addCleanup(patch.stopall)

    def _usar(self, nombre, entrada, conv=CONV):
        salida, error = herramientas_agente.ejecutar(nombre, entrada, conv)
        return json.loads(salida), error

    def test_folio_de_otra_persona_no_se_distingue_de_uno_inexistente(self):
        r, error = self._usar('solicitar_cambio_o_cancelacion', {'folio': f'COT-{self.cot.pk}', 'tipo': 'cancelacion'},
                              conv=SimpleNamespace(telefono='525599999999'))
        self.assertTrue(error)
        self.assertFalse(SolicitudCancelacion.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_el_modelo_no_puede_dictar_el_telefono(self):
        r, error = self._usar('solicitar_cambio_o_cancelacion',
                              {'folio': f'COT-{self.cot.pk}', 'tipo': 'cancelacion', 'telefono': TEL_CLI},
                              conv=SimpleNamespace(telefono='525599999999'))
        self.assertTrue(error)
        self.assertFalse(SolicitudCancelacion.objects.exists())

    def test_flujo_completo_deja_pendiente_para_direccion_y_no_cancela(self):
        r, error = self._usar('solicitar_cambio_o_cancelacion', {'folio': f'COT-{self.cot.pk:03d}', 'tipo': 'cancelacion'})
        self.assertFalse(error, r)
        self.assertIn('c***@example.com', r['nota'])
        self.assertNotIn(_codigo(), json.dumps(r))
        r, error = self._usar('confirmar_codigo_solicitud', {'codigo': _codigo()})
        self.assertFalse(error, r)
        self.assertEqual(Pendiente.objects.get().rol, 'DIRECCION')
        self.cot.refresh_from_db()
        self.assertEqual(self.cot.estado, 'CONFIRMADA')

    def test_codigo_malo_tres_veces_bloquea(self):
        self._usar('solicitar_cambio_o_cancelacion', {'folio': f'COT-{self.cot.pk}', 'tipo': 'cancelacion'})
        for _ in range(3):
            r, error = self._usar('confirmar_codigo_solicitud', {'codigo': '000000'})
            self.assertTrue(error)
        r, error = self._usar('confirmar_codigo_solicitud', {'codigo': _codigo()})
        self.assertTrue(error)
        self.assertFalse(Pendiente.objects.exists())


@wa_settings(**AGENTE)
class BotonDireccionPorWhatsAppTest(TestCase):
    def setUp(self):
        Responsable.objects.create(rol='DIRECCION', nombre='Dir', telefono_wa=TEL_DIR)
        self.cot = _cotizacion()
        self.avisos = patch.object(services_agente, '_enviar_aviso', return_value=OK).start()
        patch('comunicacion.services_pendientes._enviar_aviso', return_value=OK).start()
        patch.object(services_agente, 'enviar_whatsapp', return_value=OK).start()
        patch('comercial.services_cancelacion.enviar_whatsapp', return_value=OK).start()
        self.addCleanup(patch.stopall)
        s = services_cancelacion.iniciar(self.cot, canal='PORTAL', tipo='CANCELACION')
        self.s = services_cancelacion.verificar(s.pk, _codigo())
        self.token = 'W' * 24
        SolicitudCancelacion.objects.filter(pk=self.s.pk).update(token_hash=services_cancelacion._hash(self.token))
        self.n = 0

    def _decir(self, telefono, texto):
        self.n += 1
        conv = services_agente.recibir_mensaje(telefono=telefono, nombre='X', texto=texto, wamid=f'w{self.n}')
        agente = _cliente_falso(_respuesta(_texto('Hola.')))
        with patch.object(services_agente, '_cliente_ia', return_value=agente):
            services_agente._atender_pendientes(conv.pk)
        return agente

    def test_boton_de_otro_numero_no_llega_al_agente_ni_cambia_nada(self):
        agente = self._decir(TEL_OTRO, services_cancelacion.id_boton(self.token, 'ok'))
        self.s.refresh_from_db()
        self.assertEqual(self.s.estado, 'ANALISIS')
        self.assertEqual(agente.messages.create.call_count, 0)

    def test_rechazo_desde_direccion(self):
        self._decir(TEL_DIR, services_cancelacion.id_boton(self.token, 'no'))
        self.s.refresh_from_db()
        self.assertEqual(self.s.estado, 'RECHAZADA')


class ArcoTest(TestCase):
    def setUp(self):
        Responsable.objects.create(rol='DIRECCION', nombre='Dir', telefono_wa=TEL_DIR)
        self.enviar = patch('comunicacion.services_pendientes._enviar_aviso', return_value=OK).start()
        self.addCleanup(patch.stopall)

    def _registrar(self, **kw):
        datos = dict(tipo='ACCESO', nombre='Juan Pérez Soto', correo='juan@example.com', descripcion='Quiero mis datos')
        datos.update(kw)
        return herramientas_agente.ejecutar('registrar_solicitud_arco', datos, SimpleNamespace(telefono=TEL_CLI))

    def test_registra_con_plazo_de_20_dias_habiles_y_pendiente_con_boton(self):
        salida, error = self._registrar()
        self.assertFalse(error, salida)
        s = SolicitudARCO.objects.get()
        self.assertEqual(s.fecha_limite, SolicitudARCO.sumar_dias_habiles(timezone.localdate(), 20))
        self.assertEqual(s.estado, 'RECIBIDA')
        p = Pendiente.objects.get()
        self.assertEqual((p.rol, p.severidad), ('DIRECCION', 'URGENTE'))
        self.assertNotIn('Juan', p.detalle)
        self.assertNotIn('Quiero mis datos', p.detalle)
        self.assertIn(s.folio, p.detalle)
        self.assertEqual(self.enviar.call_args.kwargs['botones'][0][0], services_arco.id_boton(s.pk))
        self.assertEqual(len(mail.outbox), 0, 'nada sale al titular sin aprobación')

    def test_datos_invalidos(self):
        for malo in ({'correo': 'no-es-correo'}, {'nombre': 'Juan'}, {'tipo': 'BORRAR_TODO'}):
            _, error = self._registrar(**malo)
            self.assertTrue(error, malo)
        self.assertFalse(SolicitudARCO.objects.exists())

    def test_tope_por_numero_al_dia(self):
        self._registrar()
        self._registrar()
        _, error = self._registrar()
        self.assertTrue(error)
        self.assertEqual(SolicitudARCO.objects.count(), 2)

    def test_boton_de_direccion_envia_el_acuse_una_sola_vez(self):
        self._registrar()
        s = SolicitudARCO.objects.get()
        self.assertIsNone(services_arco.atender_boton(TEL_OTRO, services_arco.id_boton(s.pk)))
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('Listo', services_arco.atender_boton(TEL_DIR, services_arco.id_boton(s.pk)))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['juan@example.com'])
        self.assertIn(s.folio, mail.outbox[0].body)
        s.refresh_from_db()
        self.assertEqual(s.estado, 'EN_TRAMITE')
        self.assertIn('ya estaba en trámite', services_arco.atender_boton(TEL_DIR, services_arco.id_boton(s.pk)))
        self.assertEqual(len(mail.outbox), 1)

    def test_firma_falsa_no_sirve(self):
        self._registrar()
        s = SolicitudARCO.objects.get()
        self.assertEqual(services_arco.atender_boton(TEL_DIR, f'arco:{s.pk}-{"a" * 32}:ok'), 'Ese botón no es válido.')
        self.assertEqual(len(mail.outbox), 0)


class EvalsDeCancelacionTest(TestCase):
    def test_los_casos_nuevos_usan_herramientas_que_existen(self):
        nombres = {h['name'] for h in herramientas_agente.HERRAMIENTAS}
        casos = {c['id']: c for c in evaluacion_agente.cargar_casos()}
        for id_ in ('cancel_suplantacion', 'cancel_inyeccion', 'cancel_cuenta_nueva', 'cancel_codigo_ajeno',
                    'cancel_legitima', 'arco_acceso', 'arco_suplantacion'):
            self.assertIn(id_, casos)
            self.assertTrue(set(casos[id_].get('no_debe_usar', [])) <= nombres, id_)
        self.assertTrue(casos['cancel_inyeccion']['no_debe_usar'])

    def test_usar_una_herramienta_prohibida_falla_el_caso(self):
        caso = {'id': 'x', 'tipo': 'rechazo', 'mensajes': ['hola'], 'no_debe_usar': ['pasar_a_humano']}
        with patch.object(services_agente, 'responder', return_value=('ok', {})), \
                patch.object(evaluacion_agente, '_herramientas_usadas', return_value={'pasar_a_humano'}), \
                patch('comunicacion.services_guardia.revisar_filtro', return_value=None), \
                patch('comunicacion.services_guardia.revisar_juez', return_value=None):
            r = evaluacion_agente.evaluar_caso(caso, 1)
        self.assertFalse(r['ok'])
        self.assertTrue(any('prohibida' in f for f in r['fallas']))
