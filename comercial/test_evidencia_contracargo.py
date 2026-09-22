"""
Tests de `comercial/services_evidencia_contracargo.py` y del cron
`enviar_evidencia_contracargos_pendientes` (Issue #305): armado del PDF de
evidencia, envío manual/automático a soporte@openpay.mx y el umbral de
última instancia de 24h antes del plazo.
"""
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente, Contracargo, ContratoServicio, Cotizacion, ItemCotizacion, Pago
from comercial.services_evidencia_contracargo import armar_evidencia, enviar_evidencia_a_openpay
from comercial.services_openpay import procesar_webhook_contracargo
from contabilidad.tests import setup_contabilidad_minima
from core_erp.test_utils import login_superuser_con_totp

STORAGES_PRUEBA = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _cotizacion_con_pago(monto_items=Decimal('2000.00'), monto_pago=Decimal('1000.00')):
    cliente = Cliente.objects.create(nombre='Cliente Evidencia', email='cliente@test.com')
    cotizacion = Cotizacion.objects.create(
        cliente=cliente, nombre_evento='Evento Evidencia',
        fecha_evento=date.today() + timedelta(days=30),
    )
    ItemCotizacion.objects.create(
        cotizacion=cotizacion, descripcion='Servicio', cantidad=1, precio_unitario=monto_items,
    )
    Pago.objects.create(
        cotizacion=cotizacion, monto=monto_pago, metodo='PLATAFORMA',
        tipo='INGRESO', referencia='tx_original',
    )
    return cotizacion


def _contracargo(cotizacion=None, estado='EN_DISPUTA', dias_para_limite=2,
                  evidencia_enviada=False, openpay_id='cb_evidencia'):
    return Contracargo.objects.create(
        openpay_id=openpay_id, estado=estado, cotizacion=cotizacion,
        monto=Decimal('1000.00'),
        fecha_limite_evidencia=timezone.localdate() + timedelta(days=dias_para_limite),
        evidencia_enviada=evidencia_enviada,
        payload_crudo={'type': 'chargeback.created'},
    )


@override_settings(STORAGES=STORAGES_PRUEBA)
class ArmarEvidenciaTest(TestCase):
    def setUp(self):
        setup_contabilidad_minima()

    def test_arma_el_pdf_y_lo_guarda_en_el_contracargo(self):
        contracargo = _contracargo(_cotizacion_con_pago())
        resultado = armar_evidencia(contracargo)
        self.assertTrue(resultado)
        contracargo.refresh_from_db()
        self.assertTrue(contracargo.evidencia_pdf)
        self.assertTrue(contracargo.evidencia_pdf.name.endswith('.pdf'))

    def test_no_regenera_si_ya_existe(self):
        contracargo = _contracargo(_cotizacion_con_pago())
        armar_evidencia(contracargo)
        nombre_original = contracargo.evidencia_pdf.name
        with patch('comercial.services_evidencia_contracargo.render_to_string') as mock_render:
            armar_evidencia(contracargo)
            mock_render.assert_not_called()
        self.assertEqual(contracargo.evidencia_pdf.name, nombre_original)

    def test_funciona_sin_cotizacion_vinculada(self):
        contracargo = _contracargo(cotizacion=None)
        contracargo.requiere_vinculacion_manual = True
        contracargo.save()
        self.assertTrue(armar_evidencia(contracargo))


@override_settings(STORAGES=STORAGES_PRUEBA)
class EnviarEvidenciaAOpenpayTest(TestCase):
    def setUp(self):
        setup_contabilidad_minima()
        self.cotizacion = _cotizacion_con_pago()
        self.contracargo = _contracargo(self.cotizacion)
        self.usuario = User.objects.create_user('staff_evidencia', password='x')

    def test_manda_el_correo_con_el_pdf_adjunto_y_marca_enviada(self):
        ok, mensaje = enviar_evidencia_a_openpay(self.contracargo, usuario=self.usuario)
        self.assertTrue(ok, mensaje)
        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertEqual(correo.to, ['soporte@openpay.mx'])
        self.assertEqual(len(correo.attachments), 1)

        self.contracargo.refresh_from_db()
        self.assertTrue(self.contracargo.evidencia_enviada)
        self.assertEqual(self.contracargo.evidencia_enviada_por, self.usuario)
        self.assertIsNotNone(self.contracargo.fecha_evidencia_enviada)

    def test_envio_automatico_no_deja_usuario(self):
        ok, _ = enviar_evidencia_a_openpay(self.contracargo, usuario=None)
        self.assertTrue(ok)
        self.contracargo.refresh_from_db()
        self.assertIsNone(self.contracargo.evidencia_enviada_por)

    def test_no_reenvia_si_ya_estaba_marcada(self):
        self.contracargo.evidencia_enviada = True
        self.contracargo.save()
        ok, mensaje = enviar_evidencia_a_openpay(self.contracargo)
        self.assertFalse(ok)
        self.assertIn('ya se había enviado', mensaje)
        self.assertEqual(len(mail.outbox), 0)

    def test_adjunta_el_contrato_si_existe(self):
        ContratoServicio.objects.create(
            cotizacion=self.cotizacion, numero='CT-EVID-001',
            archivo=SimpleUploadedFile('contrato.pdf', b'%PDF-contrato', content_type='application/pdf'),
        )
        ok, _ = enviar_evidencia_a_openpay(self.contracargo)
        self.assertTrue(ok)
        self.assertEqual(len(mail.outbox[0].attachments), 2)


@override_settings(STORAGES=STORAGES_PRUEBA)
class EnviarEvidenciaContracargosPendientesTest(TestCase):
    """Cron de última instancia — umbral confirmado con el propietario: 24h."""

    def setUp(self):
        setup_contabilidad_minima()

    def _correr(self, dry_run=False):
        out = StringIO()
        call_command('enviar_evidencia_contracargos_pendientes', dry_run=dry_run, stdout=out)
        return out.getvalue()

    def test_no_dispara_si_faltan_mas_de_24h(self):
        _contracargo(_cotizacion_con_pago(), dias_para_limite=5)
        self._correr()
        self.assertEqual(len(mail.outbox), 0)

    def test_dispara_si_falta_menos_de_24h(self):
        contracargo = _contracargo(_cotizacion_con_pago(), dias_para_limite=0)
        self._correr()
        contracargo.refresh_from_db()
        self.assertTrue(contracargo.evidencia_enviada)
        self.assertIsNone(contracargo.evidencia_enviada_por)
        # 1 correo a soporte@openpay.mx + 1 alerta interna de "lo mandé yo solo".
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[0].to, ['soporte@openpay.mx'])

    def test_no_dispara_si_ya_se_envio_manualmente(self):
        _contracargo(_cotizacion_con_pago(), dias_para_limite=0, evidencia_enviada=True)
        self._correr()
        self.assertEqual(len(mail.outbox), 0)

    def test_no_dispara_para_un_contracargo_ya_resuelto(self):
        _contracargo(_cotizacion_con_pago(), estado='GANADO', dias_para_limite=0)
        self._correr()
        self.assertEqual(len(mail.outbox), 0)

    def test_dry_run_no_manda_ni_marca(self):
        contracargo = _contracargo(_cotizacion_con_pago(), dias_para_limite=0)
        salida = self._correr(dry_run=True)
        contracargo.refresh_from_db()
        self.assertFalse(contracargo.evidencia_enviada)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn(contracargo.openpay_id, salida)


@override_settings(STORAGES=STORAGES_PRUEBA)
class EnviarEvidenciaOpenpayAdminAccionTest(TestCase):
    """El botón "Enviar evidencia a Openpay" es un correo real hacia Openpay
    para disputar el contracargo — mismo gate de confirmación que
    `reembolsar_en_openpay` (orden 48 del backlog de seguridad, SEC-BIZ-002)."""

    def setUp(self):
        setup_contabilidad_minima()
        self.superusuario = User.objects.create_superuser(
            'jefa_contracargos', 'jefa_contracargos@quintakooxtanil.com', 'clave-de-prueba',
        )
        login_superuser_con_totp(self.client, self.superusuario)
        self.contracargo = _contracargo(_cotizacion_con_pago())
        self.url = reverse('admin:comercial_contracargo_changelist')

    def test_sin_confirmar_no_manda_nada(self):
        respuesta = self.client.post(self.url, {
            'action': 'enviar_evidencia_openpay',
            '_selected_action': [str(self.contracargo.pk)],
        }, follow=True)

        self.contracargo.refresh_from_db()
        self.assertFalse(self.contracargo.evidencia_enviada)
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(respuesta, '¿Confirmar esta acción?')

    def test_la_pagina_de_confirmacion_lleva_un_segundo_popup_del_navegador(self):
        """Doble confirmación pedida por el propietario: además de esta página,
        un `confirm()` del navegador vuelve a preguntar antes del POST real —
        plantilla propia (`confirmar_envio_evidencia_contracargo.html`), no la
        compartida con el resto de las acciones destructivas del admin."""
        respuesta = self.client.post(self.url, {
            'action': 'enviar_evidencia_openpay',
            '_selected_action': [str(self.contracargo.pk)],
        }, follow=True)
        self.assertContains(respuesta, 'onsubmit="return confirm(')
        self.assertContains(respuesta, 'soporte@openpay.mx')

    def test_con_confirmar_si_manda_y_marca_quien_lo_envio(self):
        self.client.post(self.url, {
            'action': 'enviar_evidencia_openpay',
            '_selected_action': [str(self.contracargo.pk)],
            'confirmar': 'si',
        }, follow=True)

        self.contracargo.refresh_from_db()
        self.assertTrue(self.contracargo.evidencia_enviada)
        self.assertEqual(self.contracargo.evidencia_enviada_por, self.superusuario)
        self.assertEqual(mail.outbox[0].to, ['soporte@openpay.mx'])


@override_settings(STORAGES=STORAGES_PRUEBA)
class ArmarEvidenciaViaWebhookTest(TestCase):
    """
    El webhook de Openpay (`services_openpay.procesar_webhook_contracargo`)
    debe dejar la evidencia armada sola al entrar en disputa — diferido a
    `on_commit`, así que hace falta `captureOnCommitCallbacks(execute=True)`
    para que corra dentro del test (mismo patrón que
    `comunicacion/tests/test_signals.py`).
    """

    def setUp(self):
        setup_contabilidad_minima()

    def _payload(self, event_type, chargeback_id, amount, cotizacion):
        return {
            'type': event_type,
            'transaction': {'id': chargeback_id, 'amount': amount, 'order_id': f'COT-{cotizacion.id}-VENTA'},
        }

    def test_al_entrar_en_disputa_arma_la_evidencia_sola(self):
        cotizacion = _cotizacion_con_pago()
        with self.captureOnCommitCallbacks(execute=True):
            contracargo = procesar_webhook_contracargo(
                self._payload('chargeback.created', 'cb_webhook_evidencia', 1000.00, cotizacion)
            )
        contracargo.refresh_from_db()
        self.assertTrue(contracargo.evidencia_pdf)

    def test_un_reintento_del_mismo_evento_no_rearma_la_evidencia(self):
        cotizacion = _cotizacion_con_pago()
        payload = self._payload('chargeback.created', 'cb_webhook_evidencia_2', 1000.00, cotizacion)
        with self.captureOnCommitCallbacks(execute=True):
            procesar_webhook_contracargo(payload)
        contracargo = Contracargo.objects.get(openpay_id='cb_webhook_evidencia_2')
        nombre_original = contracargo.evidencia_pdf.name

        with self.captureOnCommitCallbacks(execute=True):
            procesar_webhook_contracargo(payload)  # simula reintento de Openpay
        contracargo.refresh_from_db()
        self.assertEqual(contracargo.evidencia_pdf.name, nombre_original)
