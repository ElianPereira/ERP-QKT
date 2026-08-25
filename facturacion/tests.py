"""
Tests del módulo Facturación
============================
"""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, Pago
from core_erp.test_utils import login_superuser_con_totp
from facturacion.models import ConfiguracionContador, SolicitudFactura
from facturacion.services import enviar_solicitud_por_whatsapp


def _crear_cotizacion(cliente, precio):
    cot = Cotizacion.objects.create(
        cliente=cliente,
        nombre_evento='Evento Test',
        fecha_evento=date.today() + timedelta(days=30),
        incluye_refrescos=False, incluye_cerveza=False,
        incluye_licor_nacional=False, incluye_licor_premium=False,
        incluye_cocteleria_basica=False, incluye_cocteleria_premium=False,
    )
    Cotizacion.objects.filter(pk=cot.pk).update(precio_final=precio)
    cot.refresh_from_db()
    return cot


class SolicitudFacturaClienteNoFiscalTest(TestCase):
    """
    Regresión: un pago de un cliente SIN datos fiscales debe generar una
    solicitud con el snapshot "público en general" (RFC genérico, S01), y el
    PDF debe leer esos datos de la propia solicitud — nunca del Cliente en
    vivo, que para un cliente no fiscal está vacío/None.
    """

    def setUp(self):
        self.user = User.objects.create_user('u', password='x')
        self.cliente = Cliente.objects.create(nombre='Cliente sin factura')
        self.cot = _crear_cotizacion(self.cliente, Decimal('11600.00'))

    def test_solicitud_usa_snapshot_publico_en_general(self):
        pago = Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('11600.00'),
            metodo='EFECTIVO', usuario=self.user,
        )
        solicitud = SolicitudFactura.objects.get(pago=pago)
        self.assertEqual(solicitud.rfc, 'XAXX010101000')
        self.assertEqual(solicitud.razon_social, 'PUBLICO EN GENERAL')
        self.assertEqual(solicitud.uso_cfdi, 'S01')

    def test_pdf_no_muestra_none_y_usa_datos_de_la_solicitud(self):
        pago = Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('11600.00'),
            metodo='EFECTIVO', usuario=self.user,
        )
        solicitud = SolicitudFactura.objects.get(pago=pago)
        html = render_to_string('facturacion/solicitud_pdf.html', {
            'solicitud': solicitud, 'cliente': self.cliente,
            'folio': f'SOL-{solicitud.id:03d}', 'logo_url': '',
            'calc_subtotal': Decimal('0'), 'calc_iva': Decimal('0'),
            'calc_ret_isr': Decimal('0'), 'calc_total': solicitud.monto,
        })
        self.assertNotIn('None', html)
        self.assertIn('XAXX010101000', html)
        self.assertIn('PUBLICO EN GENERAL', html)
        self.assertIn('S01 - Sin efectos fiscales', html)

    def test_cliente_fiscal_conserva_sus_propios_datos(self):
        """No debe romperse el caso normal: cliente con datos fiscales reales."""
        cliente_fiscal = Cliente.objects.create(
            nombre='Empresa SA', es_cliente_fiscal=True, tipo_persona='MORAL',
            rfc='ABC010101AB1', razon_social='EMPRESA SA DE CV',
            codigo_postal_fiscal='97000', regimen_fiscal='601', uso_cfdi='G03',
        )
        cot = _crear_cotizacion(cliente_fiscal, Decimal('11600.00'))
        pago = Pago.objects.create(
            cotizacion=cot, monto=Decimal('11600.00'),
            metodo='TRANSFERENCIA', usuario=self.user,
        )
        solicitud = SolicitudFactura.objects.get(pago=pago)
        self.assertEqual(solicitud.rfc, 'ABC010101AB1')
        self.assertEqual(solicitud.razon_social, 'EMPRESA SA DE CV')
        self.assertEqual(solicitud.uso_cfdi, 'G03')


class MarcarCanceladasAdminAccionTest(TestCase):
    """SEC-BIZ-002: cancelar solicitudes desde el admin exige un segundo
    POST con 'confirmar=si' — un solo POST directo no las cancela."""

    def setUp(self):
        self.superusuario = User.objects.create_superuser(
            'jefa_factura', 'jefa_factura@quintakooxtanil.com', 'clave-de-prueba',
        )
        login_superuser_con_totp(self.client, self.superusuario)
        cliente = Cliente.objects.create(nombre='Cliente factura')
        cot = _crear_cotizacion(cliente, Decimal('5000.00'))
        pago = Pago.objects.create(
            cotizacion=cot, monto=Decimal('5000.00'),
            metodo='EFECTIVO', usuario=self.superusuario,
        )
        self.solicitud = SolicitudFactura.objects.get(pago=pago)
        self.url = reverse('admin:facturacion_solicitudfactura_changelist')

    def test_sin_confirmar_no_cancela(self):
        respuesta = self.client.post(self.url, {
            'action': 'marcar_canceladas',
            '_selected_action': [str(self.solicitud.pk)],
        }, follow=True)

        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'PENDIENTE')
        self.assertContains(respuesta, '¿Confirmar esta acción?')

    def test_con_confirmar_si_cancela(self):
        self.client.post(self.url, {
            'action': 'marcar_canceladas',
            '_selected_action': [str(self.solicitud.pk)],
            'confirmar': 'si',
        }, follow=True)

        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'CANCELADA')


@override_settings(EMAIL_FROM_NOTIFICACIONES='notificaciones@qkt.mx')
class EnviarEmailContadorRemitenteTest(TestCase):
    """El email al contador (interno, no al cliente) sale de EMAIL_FROM_NOTIFICACIONES."""

    def setUp(self):
        self.user = User.objects.create_superuser('admin', password='x', email='admin@qkt.mx')
        login_superuser_con_totp(self.client, self.user)
        self.contador = ConfiguracionContador.objects.create(
            nombre='Contador Test', email='contador@example.com',
            telefono_whatsapp='529991234567',
        )
        cliente = Cliente.objects.create(nombre='Cliente sin factura')
        cot = _crear_cotizacion(cliente, Decimal('11600.00'))
        pago = Pago.objects.create(
            cotizacion=cot, monto=Decimal('11600.00'), metodo='EFECTIVO', usuario=self.user,
        )
        self.solicitud = SolicitudFactura.objects.get(pago=pago)

    def test_email_al_contador_sale_de_notificaciones(self):
        with patch('facturacion.services.generar_pdf_solicitud', return_value=b'%PDF-fake'):
            self.client.get(reverse(
                'admin:solicitudfactura_enviar_email',
                args=[self.solicitud.id],
            ))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].from_email, 'notificaciones@qkt.mx')
        self.assertEqual(mail.outbox[0].to, ['contador@example.com'])


class EnviarWhatsappContadorPlantillaTest(TestCase):
    """Sin WA_TEMPLATE_SOLICITUD_FACTURA configurada, sigue mandando el PDF
    como mensaje 'document' directo (comportamiento de siempre). Con la
    variable configurada, manda una plantilla con el mismo PDF como
    cabecera — sin tocar nada más del flujo (mismo media_id, mismo
    endpoint, mismo manejo de error)."""

    def setUp(self):
        ConfiguracionContador.objects.create(
            nombre='Contador Test', email='contador@example.com',
            telefono_whatsapp='529991234567',
        )
        cliente = Cliente.objects.create(nombre='Cliente Plantilla')
        cot = _crear_cotizacion(cliente, Decimal('5000.00'))
        user = User.objects.create_user('u_wa', password='x')
        pago = Pago.objects.create(
            cotizacion=cot, monto=Decimal('5000.00'), metodo='EFECTIVO', usuario=user,
        )
        self.solicitud = SolicitudFactura.objects.get(pago=pago)

    def _mock_respuestas(self, mock_post):
        respuesta_upload = type('R', (), {'status_code': 200, 'json': lambda self: {'id': 'media-123'}})()
        respuesta_send = type('R', (), {'status_code': 200, 'text': ''})()
        mock_post.side_effect = [respuesta_upload, respuesta_send]

    @override_settings(WA_TEMPLATE_SOLICITUD_FACTURA='')
    def test_sin_plantilla_manda_documento_directo(self):
        with patch('facturacion.services.generar_pdf_solicitud', return_value=b'%PDF-fake'), \
             patch('facturacion.services.config', return_value='token-o-id'), \
             patch('facturacion.services.requests.post') as mock_post:
            self._mock_respuestas(mock_post)
            ok, error = enviar_solicitud_por_whatsapp(self.solicitud)

        self.assertTrue(ok, error)
        payload_enviado = mock_post.call_args_list[1].kwargs['json']
        self.assertEqual(payload_enviado['type'], 'document')
        self.assertEqual(payload_enviado['document']['id'], 'media-123')

    @override_settings(WA_TEMPLATE_SOLICITUD_FACTURA='solicitud_factura')
    def test_con_plantilla_manda_template_con_documento_en_la_cabecera(self):
        with patch('facturacion.services.generar_pdf_solicitud', return_value=b'%PDF-fake'), \
             patch('facturacion.services.config', return_value='token-o-id'), \
             patch('facturacion.services.requests.post') as mock_post:
            self._mock_respuestas(mock_post)
            ok, error = enviar_solicitud_por_whatsapp(self.solicitud)

        self.assertTrue(ok, error)
        payload_enviado = mock_post.call_args_list[1].kwargs['json']
        self.assertEqual(payload_enviado['type'], 'template')
        self.assertEqual(payload_enviado['template']['name'], 'solicitud_factura')
        cabecera = payload_enviado['template']['components'][0]
        self.assertEqual(cabecera['type'], 'header')
        self.assertEqual(cabecera['parameters'][0]['document']['id'], 'media-123')
        cuerpo = payload_enviado['template']['components'][1]
        self.assertEqual(cuerpo['parameters'][0]['text'], f"SOL-{self.solicitud.id:04d}")
        self.assertEqual(cuerpo['parameters'][1]['text'], 'Cliente Plantilla')


class EnvioAutomaticoAlRegistrarPagoTest(TestCase):
    """
    La solicitud de factura ya no requiere darle click a los botones del
    admin: se manda sola (email + WhatsApp) en cuanto el Pago que la generó
    queda confirmado en la base de datos.
    """

    def setUp(self):
        self.user = User.objects.create_user('u2', password='x')
        ConfiguracionContador.objects.create(
            nombre='Contador Test', email='contador@example.com',
            telefono_whatsapp='529991234567',
        )
        self.cliente = Cliente.objects.create(nombre='Cliente auto-envío')
        self.cot = _crear_cotizacion(self.cliente, Decimal('5000.00'))

    def test_se_manda_solo_al_confirmarse_la_transaccion(self):
        with patch('facturacion.services.enviar_solicitud_por_email', return_value=(True, '')) as m_email, \
             patch('facturacion.services.enviar_solicitud_por_whatsapp', return_value=(True, '')) as m_wa:
            with self.captureOnCommitCallbacks(execute=True):
                pago = Pago.objects.create(
                    cotizacion=self.cot, monto=Decimal('5000.00'),
                    metodo='EFECTIVO', usuario=self.user,
                )
        solicitud = SolicitudFactura.objects.get(pago=pago)
        m_email.assert_called_once_with(solicitud)
        m_wa.assert_called_once_with(solicitud)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'ENVIADA')
        self.assertEqual(solicitud.metodo_envio, 'EMAIL')
        self.assertIsNotNone(solicitud.fecha_envio)

    def test_no_se_dispara_antes_de_que_la_transaccion_confirme(self):
        """Sin captureOnCommitCallbacks (equivalente a que la transacción no
        haya confirmado todavía), el envío no debe intentarse."""
        with patch('facturacion.services.enviar_solicitud_por_email') as m_email, \
             patch('facturacion.services.enviar_solicitud_por_whatsapp') as m_wa:
            Pago.objects.create(
                cotizacion=self.cot, monto=Decimal('5000.00'),
                metodo='EFECTIVO', usuario=self.user,
            )
        m_email.assert_not_called()
        m_wa.assert_not_called()

    def test_email_falla_whatsapp_ok_igual_marca_enviada(self):
        with patch('facturacion.services.enviar_solicitud_por_email', return_value=(False, 'Brevo caído')), \
             patch('facturacion.services.enviar_solicitud_por_whatsapp', return_value=(True, '')):
            with self.captureOnCommitCallbacks(execute=True):
                pago = Pago.objects.create(
                    cotizacion=self.cot, monto=Decimal('5000.00'),
                    metodo='EFECTIVO', usuario=self.user,
                )
        solicitud = SolicitudFactura.objects.get(pago=pago)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'ENVIADA')
        self.assertEqual(solicitud.metodo_envio, 'WHATSAPP')

    def test_los_dos_canales_fallan_se_queda_pendiente(self):
        with patch('facturacion.services.enviar_solicitud_por_email', return_value=(False, 'Brevo caído')), \
             patch('facturacion.services.enviar_solicitud_por_whatsapp', return_value=(False, 'Meta caído')):
            with self.captureOnCommitCallbacks(execute=True):
                pago = Pago.objects.create(
                    cotizacion=self.cot, monto=Decimal('5000.00'),
                    metodo='EFECTIVO', usuario=self.user,
                )
        solicitud = SolicitudFactura.objects.get(pago=pago)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'PENDIENTE')
        self.assertIsNone(solicitud.fecha_envio)

    def test_una_excepcion_inesperada_no_tumba_el_guardado_del_pago(self):
        """El auto-envío nunca debe impedir que el Pago se haya guardado —
        aunque el envío truene con algo no contemplado por el try/except
        interno de servicios (aquí se fuerza en la orquestación misma)."""
        with patch('facturacion.services.enviar_solicitud_al_contador', side_effect=RuntimeError('boom')):
            with self.captureOnCommitCallbacks(execute=True):
                pago = Pago.objects.create(
                    cotizacion=self.cot, monto=Decimal('5000.00'),
                    metodo='EFECTIVO', usuario=self.user,
                )
        self.assertTrue(Pago.objects.filter(pk=pago.pk).exists())
        self.assertTrue(SolicitudFactura.objects.filter(pago=pago).exists())


class RecordatorioContadorCommandTest(TestCase):
    """enviar_recordatorios_contador: recuerda por email/WhatsApp solo lo
    ENVIADA y todavía sin facturar, en los días exactos de la cadencia, y
    no repite el mismo recordatorio si el comando corre dos veces el mismo día."""

    def setUp(self):
        self.user = User.objects.create_user('u3', password='x')
        ConfiguracionContador.objects.create(
            nombre='Contador Test', email='contador@example.com',
            telefono_whatsapp='529991234567',
        )

    def _crear_solicitud_enviada(self, dias_atras, ultimo_recordatorio=None):
        # Cliente/cotización propios por llamada: cada solicitud de prueba
        # necesita su propio saldo disponible de $5000, sin pisarse entre sí.
        cliente = Cliente.objects.create(nombre='Cliente recordatorio')
        cot = _crear_cotizacion(cliente, Decimal('5000.00'))
        with self.captureOnCommitCallbacks(execute=True):
            with patch('facturacion.services.enviar_solicitud_por_email', return_value=(True, '')), \
                 patch('facturacion.services.enviar_solicitud_por_whatsapp', return_value=(True, '')):
                pago = Pago.objects.create(
                    cotizacion=cot, monto=Decimal('5000.00'),
                    metodo='EFECTIVO', usuario=self.user,
                )
        solicitud = SolicitudFactura.objects.get(pago=pago)
        fecha_envio = timezone.now() - timedelta(days=dias_atras)
        SolicitudFactura.objects.filter(pk=solicitud.pk).update(
            fecha_envio=fecha_envio, ultimo_recordatorio_enviado=ultimo_recordatorio,
        )
        solicitud.refresh_from_db()
        return solicitud

    def test_recuerda_a_los_3_dias(self):
        solicitud = self._crear_solicitud_enviada(dias_atras=3)
        with patch('facturacion.management.commands.enviar_recordatorios_contador.enviar_solicitud_por_email',
                   return_value=(True, '')) as m_email, \
             patch('facturacion.management.commands.enviar_recordatorios_contador.enviar_solicitud_por_whatsapp',
                   return_value=(True, '')):
            call_command('enviar_recordatorios_contador')
        m_email.assert_called_once_with(solicitud)
        solicitud.refresh_from_db()
        self.assertIsNotNone(solicitud.ultimo_recordatorio_enviado)

    def test_no_recuerda_fuera_de_la_cadencia(self):
        self._crear_solicitud_enviada(dias_atras=5)
        with patch('facturacion.management.commands.enviar_recordatorios_contador.enviar_solicitud_por_email') as m_email, \
             patch('facturacion.management.commands.enviar_recordatorios_contador.enviar_solicitud_por_whatsapp') as m_wa:
            call_command('enviar_recordatorios_contador')
        m_email.assert_not_called()
        m_wa.assert_not_called()

    def test_no_repite_el_mismo_dia(self):
        self._crear_solicitud_enviada(dias_atras=3, ultimo_recordatorio=timezone.now())
        with patch('facturacion.management.commands.enviar_recordatorios_contador.enviar_solicitud_por_email') as m_email, \
             patch('facturacion.management.commands.enviar_recordatorios_contador.enviar_solicitud_por_whatsapp') as m_wa:
            call_command('enviar_recordatorios_contador')
        m_email.assert_not_called()
        m_wa.assert_not_called()

    def test_no_recuerda_pendiente_ni_facturada_ni_cancelada(self):
        pendiente = self._crear_solicitud_enviada(dias_atras=3)
        SolicitudFactura.objects.filter(pk=pendiente.pk).update(estado='PENDIENTE', fecha_envio=None)

        facturada = self._crear_solicitud_enviada(dias_atras=3)
        SolicitudFactura.objects.filter(pk=facturada.pk).update(estado='FACTURADA')

        cancelada = self._crear_solicitud_enviada(dias_atras=3)
        SolicitudFactura.objects.filter(pk=cancelada.pk).update(estado='CANCELADA')

        with patch('facturacion.management.commands.enviar_recordatorios_contador.enviar_solicitud_por_email') as m_email, \
             patch('facturacion.management.commands.enviar_recordatorios_contador.enviar_solicitud_por_whatsapp') as m_wa:
            call_command('enviar_recordatorios_contador')
        m_email.assert_not_called()
        m_wa.assert_not_called()

    def test_dry_run_no_manda_ni_registra(self):
        solicitud = self._crear_solicitud_enviada(dias_atras=7)
        with patch('facturacion.management.commands.enviar_recordatorios_contador.enviar_solicitud_por_email') as m_email, \
             patch('facturacion.management.commands.enviar_recordatorios_contador.enviar_solicitud_por_whatsapp') as m_wa:
            call_command('enviar_recordatorios_contador', '--dry-run')
        m_email.assert_not_called()
        m_wa.assert_not_called()
        solicitud.refresh_from_db()
        self.assertIsNone(solicitud.ultimo_recordatorio_enviado)
