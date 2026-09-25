"""
Cierre del flujo de facturación para el cliente que cotiza por la web:
forma de pago real de Openpay, uso de CFDI compatible con el régimen,
concepto por tipo de servicio, ISH en la solicitud y entrega de la factura
al cliente (email + portal).
"""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from comercial.models import Cliente, Cotizacion, OpenpayTransaccion, Pago, PortalCliente
from comunicacion.models import ComunicacionCliente
from facturacion.choices import uso_cfdi_compatible
from facturacion.models import SolicitudFactura
from facturacion.services import generar_pdf_solicitud

STORAGES_PRUEBA = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "privado": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _cliente_fiscal(**extra):
    datos = dict(
        nombre='Juan Pérez', email='juan@example.com', es_cliente_fiscal=True,
        tipo_persona='FISICA', rfc='PEGJ800101AB1', razon_social='JUAN PEREZ GOMEZ',
        codigo_postal_fiscal='97000', regimen_fiscal='612', uso_cfdi='G03',
    )
    datos.update(extra)
    return Cliente.objects.create(**datos)


def _cotizacion(cliente, precio=Decimal('11600.00'), **campos):
    cot = Cotizacion.objects.create(
        cliente=cliente, nombre_evento='Evento Test',
        fecha_evento=date.today() + timedelta(days=30),
    )
    Cotizacion.objects.filter(pk=cot.pk).update(precio_final=precio, **campos)
    cot.refresh_from_db()
    return cot


class UsoCfdiCompatibleTest(TestCase):
    def test_616_no_admite_g03(self):
        self.assertEqual(uso_cfdi_compatible('616', 'G03'), 'S01')
        self.assertEqual(uso_cfdi_compatible('605', None), 'S01')

    def test_regimenes_con_actividad_conservan_g03(self):
        for regimen in ('601', '612', '626'):
            self.assertEqual(uso_cfdi_compatible(regimen, 'G03'), 'G03')

    def test_respeta_un_uso_capturado_a_mano(self):
        self.assertEqual(uso_cfdi_compatible('612', 'I02'), 'I02')


class FormaPagoOpenpayTest(TestCase):
    """Todo cobro de Openpay es Pago(metodo='PLATAFORMA'), que antes salía
    siempre como 03 Transferencia."""

    def setUp(self):
        self.cot = _cotizacion(_cliente_fiscal())

    def _solicitud(self, metodo, payload, openpay_id='trx_1'):
        OpenpayTransaccion.objects.create(
            openpay_id=openpay_id, metodo=metodo, payload_crudo=payload, cotizacion=self.cot,
        )
        pago = Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('1000.00'), metodo='PLATAFORMA', referencia=openpay_id,
        )
        return SolicitudFactura.objects.get(pago=pago)

    def test_tarjeta_de_debito(self):
        self.assertEqual(self._solicitud('card', {'card': {'type': 'debit'}}).forma_pago, '28')

    def test_tarjeta_de_credito(self):
        self.assertEqual(self._solicitud('card', {'card': {'type': 'credit'}}).forma_pago, '04')

    def test_tarjeta_por_webhook_lee_el_cargo_dentro_de_transaction(self):
        payload = {'type': 'charge.succeeded', 'transaction': {'card': {'type': 'debit'}}}
        self.assertEqual(self._solicitud('card', payload).forma_pago, '28')

    def test_efectivo_en_tienda(self):
        self.assertEqual(self._solicitud('store', {}).forma_pago, '01')

    def test_spei(self):
        self.assertEqual(self._solicitud('bank_account', {}).forma_pago, '03')

    def test_pago_manual_no_cambia(self):
        pago = Pago.objects.create(cotizacion=self.cot, monto=Decimal('1000.00'), metodo='EFECTIVO')
        self.assertEqual(SolicitudFactura.objects.get(pago=pago).forma_pago, '01')


class DatosDeLaSolicitudTest(TestCase):
    def test_fisica_616_sale_con_uso_s01(self):
        """616 + G03 (los defaults de Cliente) es rechazado por el PAC."""
        cliente = _cliente_fiscal(regimen_fiscal='616', uso_cfdi='G03')
        pago = Pago.objects.create(cotizacion=_cotizacion(cliente), monto=Decimal('500.00'), metodo='EFECTIVO')
        solicitud = SolicitudFactura.objects.get(pago=pago)
        self.assertEqual((solicitud.regimen_fiscal, solicitud.uso_cfdi), ('616', 'S01'))

    def test_concepto_por_tipo_de_servicio(self):
        cliente = _cliente_fiscal()
        esperados = {
            'EVENTO': 'Servicio De Evento En General',
            'PASADIA': 'Servicio De Pasadía',
            'HOSPEDAJE': 'Servicio De Hospedaje',
            'ARRENDAMIENTO': 'Arrendamiento De Mobiliario',
        }
        for tipo, concepto in esperados.items():
            with self.subTest(tipo=tipo):
                cot = _cotizacion(cliente, tipo_servicio=tipo)
                pago = Pago.objects.create(cotizacion=cot, monto=Decimal('500.00'), metodo='EFECTIVO')
                solicitud = SolicitudFactura.objects.get(pago=pago)
                self.assertEqual(solicitud.concepto, f"COT-{cot.pk:04d} {concepto}")


class IshEnLaSolicitudTest(TestCase):
    def setUp(self):
        # 1,000 de base + 160 de IVA + 30 de ISH = 1,190.
        self.cot = _cotizacion(
            _cliente_fiscal(), precio=Decimal('1190.00'), tipo_servicio='HOSPEDAJE',
            subtotal=Decimal('1000.00'), iva=Decimal('160.00'), impuesto_hospedaje=Decimal('30.00'),
        )
        pago = Pago.objects.create(cotizacion=self.cot, monto=Decimal('1190.00'), metodo='EFECTIVO')
        self.solicitud = SolicitudFactura.objects.get(pago=pago)

    def test_la_solicitud_guarda_el_ish_y_el_desglose_cuadra(self):
        self.assertEqual(self.solicitud.impuesto_hospedaje, Decimal('30.00'))
        self.assertEqual(self.solicitud.subtotal, Decimal('1000.00'))
        self.assertTrue(self.solicitud.desglose_cuadra)
        self.assertIn('ISH (impuesto local): $30.00', self.solicitud.get_datos_para_contador())

    def test_el_pdf_usa_el_desglose_guardado_no_trata_el_ish_como_base(self):
        with patch('facturacion.services.render_to_string', return_value='') as render, \
             patch('facturacion.services.HTML'):
            generar_pdf_solicitud(self.solicitud)
        contexto = render.call_args.args[1]
        self.assertEqual(contexto['calc_subtotal'], Decimal('1000.00'))
        self.assertEqual(contexto['calc_iva'], Decimal('160.00'))
        self.assertEqual(contexto['calc_ish'], Decimal('30.00'))

    def test_monto_editado_a_mano_recalcula_desde_el_monto(self):
        self.solicitud.monto = Decimal('1160.00')
        self.assertFalse(self.solicitud.desglose_cuadra)
        with patch('facturacion.services.render_to_string', return_value='') as render, \
             patch('facturacion.services.HTML'):
            generar_pdf_solicitud(self.solicitud)
        contexto = render.call_args.args[1]
        self.assertEqual(contexto['calc_subtotal'], Decimal('1000.00'))
        self.assertEqual(contexto['calc_ish'], Decimal('0.00'))


@override_settings(STORAGES=STORAGES_PRUEBA)
class EntregaDeLaFacturaAlClienteTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('u', password='x')
        self.cot = _cotizacion(_cliente_fiscal())
        with patch('facturacion.services.enviar_solicitud_al_contador'):
            pago = Pago.objects.create(cotizacion=self.cot, monto=Decimal('11600.00'), metodo='EFECTIVO')
        self.solicitud = SolicitudFactura.objects.get(pago=pago)
        mail.outbox = []

    def _subir_factura(self, solicitud=None):
        solicitud = solicitud or self.solicitud
        solicitud.archivo_pdf = SimpleUploadedFile('f.pdf', b'%PDF-factura', content_type='application/pdf')
        solicitud.archivo_xml = SimpleUploadedFile('f.xml', b'<cfdi/>', content_type='application/xml')
        solicitud.uuid_factura = 'ABCD-1234'
        with self.captureOnCommitCallbacks(execute=True):
            solicitud.save()

    def test_al_subir_la_factura_se_le_manda_al_cliente(self):
        self._subir_factura()
        self.assertEqual(self.solicitud.estado, 'FACTURADA')
        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertEqual(correo.to, ['juan@example.com'])
        self.assertEqual(
            sorted(nombre for nombre, _, _ in correo.attachments),
            ['Factura_ABCD-1234.pdf', 'Factura_ABCD-1234.xml'],
        )
        self.assertTrue(ComunicacionCliente.objects.filter(
            clave_idempotencia=f"factura:{self.solicitud.pk}:email", tipo='FACTURA', estado='ENVIADO',
        ).exists())

    def test_reguardar_no_reenvia(self):
        self._subir_factura()
        self.solicitud.notas = 'revisada'
        with self.captureOnCommitCallbacks(execute=True):
            self.solicitud.save()
        self.assertEqual(len(mail.outbox), 1)

    def test_publico_en_general_no_se_envia(self):
        cliente = Cliente.objects.create(nombre='Sin factura', email='x@example.com')
        with patch('facturacion.services.enviar_solicitud_al_contador'):
            pago = Pago.objects.create(cotizacion=_cotizacion(cliente), monto=Decimal('100.00'), metodo='EFECTIVO')
        mail.outbox = []
        self._subir_factura(SolicitudFactura.objects.get(pago=pago))
        self.assertEqual(len(mail.outbox), 0)

    def test_el_portal_lista_y_sirve_la_factura(self):
        self._subir_factura()
        portal = PortalCliente.objects.get(cotizacion=self.cot)
        url_pdf = reverse('portal_descargar_factura', args=[portal.token, self.solicitud.pk, 'pdf'])
        self.assertContains(self.client.get(reverse('portal_evento', args=[portal.token])), url_pdf)

        respuesta = self.client.get(url_pdf)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(b''.join(respuesta.streaming_content), b'%PDF-factura')
        self.assertEqual(respuesta['Cache-Control'], 'private, no-store')

    def test_el_portal_no_sirve_facturas_de_otra_cotizacion(self):
        otra = _cotizacion(_cliente_fiscal(rfc='OTRO800101AB1'))
        with patch('facturacion.services.enviar_solicitud_al_contador'):
            pago = Pago.objects.create(cotizacion=otra, monto=Decimal('100.00'), metodo='EFECTIVO')
        ajena = SolicitudFactura.objects.get(pago=pago)
        self._subir_factura(ajena)
        portal = PortalCliente.objects.get(cotizacion=self.cot)
        url = reverse('portal_descargar_factura', args=[portal.token, ajena.pk, 'pdf'])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_el_portal_no_sirve_una_solicitud_sin_facturar(self):
        portal = PortalCliente.objects.get(cotizacion=self.cot)
        url = reverse('portal_descargar_factura', args=[portal.token, self.solicitud.pk, 'pdf'])
        self.assertEqual(self.client.get(url).status_code, 404)
