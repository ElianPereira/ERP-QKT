"""Fase 1e (Issue #428): renglones pendientes de la Fase 1 — pendientes urgentes
de choque de fecha, contracargo, terminal por WhatsApp, quejas, FAQ por
WhatsApp, «listo» del personal y SAT."""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from comercial.models import Cliente, Contracargo, Cotizacion, ItemCotizacion, Pago
from comunicacion import services_agente, services_pendientes
from comunicacion.models import Pendiente, Responsable
from comunicacion.services_notificaciones import alertar_equipo_contracargo, alertar_equipo_evidencia_automatica

from .test_agente import AGENTE, _cliente_falso, _respuesta, _texto, _uso
from .utils import TEL_CLIENTE, wa_settings

TEL_ENC = '525555550011'
TEL_DIR = '525555550012'
TEL_OTRO = '525555550013'
OK = SimpleNamespace(estado='ENVIADO', proveedor_id='')


def _resp(rol, telefono, **kw):
    return Responsable.objects.create(rol=rol, nombre=f'{rol} test', telefono_wa=telefono, **kw)


class MocksWA:
    """Intercepta los transportes de WhatsApp de la bandeja y del agente."""

    def setUp(self):
        super().setUp()
        mail.outbox = []
        for n in ('enviar_whatsapp', 'enviar_whatsapp_template', 'enviar_whatsapp_botones'):
            p = patch.object(services_pendientes, n, return_value=OK)
            setattr(self, n, p.start())
            self.addCleanup(p.stop)


def _cotizacion(dias=60, fecha=None, precio='10000.00', nombre='Cliente 1e'):
    cliente = Cliente.objects.create(nombre=nombre, telefono='5555550001')
    cot = Cotizacion.objects.create(
        cliente=cliente, nombre_evento='Evento', incluye_refrescos=False,
        fecha_evento=fecha or (timezone.localdate() + timedelta(days=dias)))
    ItemCotizacion.objects.create(cotizacion=cot, descripcion='Servicio', cantidad=1,
                                  precio_unitario=Decimal(precio))
    cot.refresh_from_db()
    return cot  # 10000 + IVA = 11,600.00


# ───────────────────────────── Punto 0 ────────────────────────────────────────

@wa_settings(**AGENTE)
class MismoDiaTest(MocksWA, TestCase):
    def test_consulta_para_hoy_termina_en_pase_a_humano_urgente_para_el_encargado(self):
        _resp('ENCARGADO', TEL_ENC)
        conv = services_agente.recibir_mensaje(
            telefono=TEL_CLIENTE, nombre='Ana', texto='¿Hay lugar hoy?', wamid='w1')
        hoy = timezone.localdate().isoformat()
        cliente = _cliente_falso(
            _respuesta(_uso('consultar_disponibilidad', {'fecha': hoy}), stop='tool_use'),
            _respuesta(_uso('pasar_a_humano', {'motivo': 'Quiere contratar hoy'}, id_='toolu_2'), stop='tool_use'),
            _respuesta(_texto('Alguien del equipo te confirma por este chat.')),
        )
        with patch.object(services_agente, '_cliente_ia', return_value=cliente), \
                patch.object(services_agente, 'enviar_whatsapp', return_value=OK):
            services_agente.procesar_conversacion(conv.pk)
        p = Pendiente.objects.get(tipo='PASE_HUMANO')
        self.assertEqual((p.severidad, p.rol), ('URGENTE', 'ENCARGADO'))


# ───────────────────────────── Punto 1 ────────────────────────────────────────

@wa_settings(**AGENTE)
class ChoqueFechaTest(MocksWA, TestCase):
    def _choque(self):
        a = _cotizacion()
        Pago.objects.create(cotizacion=a, monto=Decimal('5800.00'), metodo='TRANSFERENCIA')
        b = _cotizacion(fecha=a.fecha_evento, nombre='Segundo')
        with self.captureOnCommitCallbacks(execute=True):
            pago = Pago.objects.create(cotizacion=b, monto=Decimal('5800.00'), metodo='TRANSFERENCIA')
        return b, pago

    def test_pago_con_fecha_ocupada_deja_un_pendiente_urgente_para_direccion(self):
        b, pago = self._choque()
        p = Pendiente.objects.get(tipo='CHOQUE_FECHA')
        self.assertEqual((p.severidad, p.rol), ('URGENTE', 'DIRECCION'))
        self.assertEqual(p.titulo, f'Pago con fecha ocupada — COT-{b.pk:03d}')
        self.assertEqual(p.clave_idempotencia, f'pago_fecha_ocupada:{pago.pk}')
        self.assertIn('5,800.00', p.detalle)
        self.assertIn('mover fecha o reembolsar', p.detalle)
        self.assertNotRegex(p.titulo, r'[^\w\s—.,-]')  # sin emojis en el admin

    def test_no_queda_el_pendiente_alerta_normal_duplicado(self):
        self._choque()
        self.assertFalse(Pendiente.objects.filter(tipo='ALERTA', titulo__icontains='fecha ocupada').exists())
        self.assertTrue(any('fecha ocupada' in m.subject for m in mail.outbox))  # el correo sí sale

    def test_repetirlo_no_crea_otro(self):
        b, pago = self._choque()
        with self.captureOnCommitCallbacks(execute=True):
            pago._confirmar_cotizacion()
        self.assertEqual(Pendiente.objects.filter(tipo='CHOQUE_FECHA').count(), 1)


# ───────────────────────────── Punto 2 ────────────────────────────────────────

@wa_settings(**AGENTE)
class ContracargoPendienteTest(MocksWA, TestCase):
    def setUp(self):
        super().setUp()
        self.cot = _cotizacion()

    def _contracargo(self, estado='EN_DISPUTA', **kw):
        return Contracargo.objects.create(
            openpay_id=f'cb_{estado}', cotizacion=self.cot, estado=estado,
            monto=Decimal('1000.00'), payload_crudo={}, **kw)

    def test_en_disputa_deja_un_pendiente_urgente_con_folio_monto_y_limite(self):
        c = self._contracargo(fecha_limite_evidencia=timezone.localdate() + timedelta(days=3))
        alertar_equipo_contracargo(c)
        alertar_equipo_contracargo(c)  # repetirlo no crea otro
        p = Pendiente.objects.get(tipo='CONTRACARGO')
        self.assertEqual((p.severidad, p.rol), ('URGENTE', 'DIRECCION'))
        self.assertEqual(p.clave_idempotencia, f'contracargo:{c.pk}:EN_DISPUTA')
        self.assertIn(f'COT-{self.cot.pk:03d}', p.titulo)
        self.assertIn('1,000.00', p.detalle)
        self.assertIn('Fecha límite', p.detalle)
        self.assertFalse(Pendiente.objects.filter(tipo='ALERTA', titulo__icontains='contracargo').exists())
        self.assertEqual(len(mail.outbox), 1)

    def test_un_pendiente_por_estado(self):
        c = self._contracargo()
        alertar_equipo_contracargo(c)
        Contracargo.objects.filter(pk=c.pk).update(estado='PERDIDO')
        c.refresh_from_db()
        alertar_equipo_contracargo(c)
        self.assertEqual(Pendiente.objects.filter(tipo='CONTRACARGO').count(), 2)

    def test_la_evidencia_automatica_se_anota_en_el_pendiente(self):
        c = self._contracargo(fecha_limite_evidencia=timezone.localdate() + timedelta(days=1))
        alertar_equipo_contracargo(c)
        alertar_equipo_evidencia_automatica(c)
        p = Pendiente.objects.get(tipo='CONTRACARGO')
        self.assertIn('evidencia automática', p.detalle)
