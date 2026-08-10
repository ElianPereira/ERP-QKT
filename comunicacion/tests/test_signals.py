"""Tests de los signals: cotización COTIZADA y pagos."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import TestCase

from comercial.models import Cliente, Cotizacion, Pago
from comunicacion.models import ComunicacionCliente

from .utils import TEL_CLIENTE, TEL_EMISOR, RespuestaFalsa, limpiar_cache_emisor, wa_settings


@wa_settings()
class ComunicacionSignalsTest(TestCase):
    """
    Los envíos van dentro de `transaction.on_commit`, que en `TestCase` nunca se
    ejecuta porque la transacción se revierte. Por eso cada bloque que guarda va
    envuelto en `captureOnCommitCallbacks(execute=True)`.
    """

    def setUp(self):
        limpiar_cache_emisor()
        self.cliente = Cliente.objects.create(
            nombre='Cliente Test',
            email='cliente@example.com',
            telefono=TEL_CLIENTE,
            tipo_persona='FISICA',
        )
        self.cot = self._crear_cotizacion('Boda Test')

    def _crear_cotizacion(self, nombre):
        cot = Cotizacion.objects.create(
            cliente=self.cliente,
            nombre_evento=nombre,
            fecha_evento=date.today() + timedelta(days=60),
            num_personas=100,
            precio_final=Decimal('50000.00'),
        )
        Cotizacion.objects.filter(pk=cot.pk).update(precio_final=Decimal('50000.00'))
        cot.refresh_from_db()
        return cot

    def _wa_ok(self):
        return patch('comunicacion.services.requests.post', return_value=RespuestaFalsa())

    def _emisor(self):
        return patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR)

    def _guardar(self, funcion):
        with self.captureOnCommitCallbacks(execute=True):
            return funcion()

    # ───────────────────────────────── Pagos ────────────────────────────────

    def test_pago_ingreso_envia_email_y_whatsapp(self):
        mail.outbox = []
        with self._wa_ok(), self._emisor():
            self._guardar(lambda: Pago.objects.create(
                cotizacion=self.cot, monto=Decimal('10000.00'),
                metodo='TRANSFERENCIA', tipo='INGRESO',
            ))
        comms = ComunicacionCliente.objects.filter(
            cotizacion=self.cot, tipo='CONFIRMACION_PAGO'
        )
        self.assertEqual(comms.filter(canal='EMAIL').count(), 1)
        self.assertEqual(comms.filter(canal='WHATSAPP').count(), 1)
        self.assertEqual(set(comms.values_list('estado', flat=True)), {'ENVIADO'})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('cliente@example.com', mail.outbox[0].to)

    def test_el_whatsapp_de_pago_lleva_monto_fecha_saldo_y_portal(self):
        with self._wa_ok() as post, self._emisor():
            self._guardar(lambda: Pago.objects.create(
                cotizacion=self.cot, monto=Decimal('20000.00'),
                metodo='TRANSFERENCIA', tipo='INGRESO',
            ))
        parametros = [
            p['text']
            for p in post.call_args.kwargs['json']['template']['components'][0]['parameters']
        ]
        self.assertEqual(parametros[1], '20,000.00')
        self.assertEqual(parametros[3], '30,000.00')       # saldo restante
        self.assertTrue(parametros[4].startswith('https://portal.test/mi-evento/'))

    def test_pago_que_salda_el_total_muestra_saldo_cero(self):
        with self._wa_ok() as post, self._emisor():
            self._guardar(lambda: Pago.objects.create(
                cotizacion=self.cot, monto=Decimal('50000.00'),
                metodo='TRANSFERENCIA', tipo='INGRESO',
            ))
        parametros = [
            p['text']
            for p in post.call_args.kwargs['json']['template']['components'][0]['parameters']
        ]
        self.assertIn('0.00', parametros[3])
        self.assertIn('totalmente pagado', parametros[3])
        # El '$' vive en el cuerpo aprobado de la plantilla, no en el parámetro.
        self.assertNotIn('$', parametros[3])

    def test_reprocesar_el_mismo_pago_no_duplica(self):
        with self._wa_ok(), self._emisor():
            pago = self._guardar(lambda: Pago.objects.create(
                cotizacion=self.cot, monto=Decimal('10000.00'),
                metodo='TRANSFERENCIA', tipo='INGRESO',
            ))
            # Re-disparar el servicio a mano imita un reintento de Openpay o un
            # reinicio de Railway a media ejecución.
            from comunicacion.services_notificaciones import notificar_pago
            notificar_pago(pago)
        self.assertEqual(
            ComunicacionCliente.objects.filter(
                cotizacion=self.cot, tipo='CONFIRMACION_PAGO'
            ).count(),
            2,  # un email + un WhatsApp, sin repetirse
        )

    def test_pago_reembolso_solo_manda_email(self):
        with self._wa_ok(), self._emisor():
            self._guardar(lambda: Pago.objects.create(
                cotizacion=self.cot, monto=Decimal('10000.00'),
                metodo='TRANSFERENCIA', tipo='INGRESO',
            ))
            mail.outbox = []
            self._guardar(lambda: Pago.objects.create(
                cotizacion=self.cot, monto=Decimal('5000.00'),
                metodo='TRANSFERENCIA', tipo='REEMBOLSO',
            ))
        reembolsos = ComunicacionCliente.objects.filter(cotizacion=self.cot, tipo='REEMBOLSO')
        self.assertEqual(reembolsos.count(), 1)
        self.assertEqual(reembolsos.first().canal, 'EMAIL')

    def test_cliente_sin_contacto_no_crea_comunicacion(self):
        self.cliente.email = ''
        self.cliente.telefono = ''
        self.cliente.save()
        with self._wa_ok(), self._emisor():
            self._guardar(lambda: Pago.objects.create(
                cotizacion=self.cot, monto=Decimal('1000.00'),
                metodo='EFECTIVO', tipo='INGRESO',
            ))
        self.assertEqual(ComunicacionCliente.objects.filter(cotizacion=self.cot).count(), 0)

    # ──────────────────────────── Cotización COTIZADA ───────────────────────

    def test_borrador_no_notifica(self):
        with self._wa_ok(), self._emisor():
            self._guardar(lambda: self.cot.save())
        self.assertEqual(
            ComunicacionCliente.objects.filter(cotizacion=self.cot, tipo='COTIZACION').count(),
            0,
        )

    def test_pasar_a_cotizada_manda_email_y_whatsapp(self):
        mail.outbox = []
        with self._wa_ok(), self._emisor():
            self._guardar(lambda: self._cotizar(self.cot))
        comms = ComunicacionCliente.objects.filter(cotizacion=self.cot, tipo='COTIZACION')
        self.assertEqual(comms.filter(canal='EMAIL').count(), 1)
        self.assertEqual(comms.filter(canal='WHATSAPP').count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_guardar_de_nuevo_una_cotizada_no_duplica(self):
        with self._wa_ok(), self._emisor():
            self._guardar(lambda: self._cotizar(self.cot))
            self._guardar(lambda: self.cot.save())
            self._guardar(lambda: self.cot.save())
        self.assertEqual(
            ComunicacionCliente.objects.filter(cotizacion=self.cot, tipo='COTIZACION').count(),
            2,  # email + WhatsApp, una sola vez
        )

    def test_una_segunda_cotizacion_del_mismo_cliente_si_se_notifica(self):
        """
        Regresión: la idempotencia anterior era
        `filter(cotizacion=cot, tipo='COTIZACION').exists()` sobre el cliente,
        de modo que la segunda cotización se quedaba sin aviso.
        """
        otra = self._crear_cotizacion('XV Años Test')
        with self._wa_ok(), self._emisor():
            self._guardar(lambda: self._cotizar(self.cot))
            self._guardar(lambda: self._cotizar(otra))
        self.assertEqual(
            ComunicacionCliente.objects.filter(cotizacion=otra, tipo='COTIZACION').count(),
            2,
        )

    def test_fallo_de_whatsapp_no_impide_el_email(self):
        mail.outbox = []
        with patch('comunicacion.services.requests.post', side_effect=RuntimeError('Meta caído')), \
             self._emisor():
            self._guardar(lambda: self._cotizar(self.cot))
        comms = ComunicacionCliente.objects.filter(cotizacion=self.cot, tipo='COTIZACION')
        self.assertEqual(comms.get(canal='EMAIL').estado, 'ENVIADO')
        self.assertEqual(comms.get(canal='WHATSAPP').estado, 'FALLIDO')
        self.assertEqual(len(mail.outbox), 1)
        self.cot.refresh_from_db()
        self.assertEqual(self.cot.estado, 'COTIZADA')

    def test_fallo_del_email_no_impide_el_whatsapp(self):
        with self._wa_ok(), self._emisor(), \
             patch('comunicacion.services.EmailMultiAlternatives.send',
                   side_effect=RuntimeError('Brevo caído')):
            self._guardar(lambda: self._cotizar(self.cot))
        comms = ComunicacionCliente.objects.filter(cotizacion=self.cot, tipo='COTIZACION')
        self.assertEqual(comms.get(canal='EMAIL').estado, 'FALLIDO')
        self.assertEqual(comms.get(canal='WHATSAPP').estado, 'ENVIADO')

    @wa_settings(WA_TEMPLATE_COTIZACION='')
    def test_cotizada_sin_plantilla_no_manda_texto_libre(self):
        """Fuera del cotizador la ventana de 24 h puede estar cerrada."""
        with patch('comunicacion.services.requests.post') as post, self._emisor():
            self._guardar(lambda: self._cotizar(self.cot))
        post.assert_not_called()
        wa = ComunicacionCliente.objects.get(
            cotizacion=self.cot, tipo='COTIZACION', canal='WHATSAPP'
        )
        self.assertEqual(wa.estado, 'FALLIDO')
        self.assertIn('WA_TEMPLATE_COTIZACION', wa.error)

    @staticmethod
    def _cotizar(cot):
        cot.estado = 'COTIZADA'
        cot.save()
