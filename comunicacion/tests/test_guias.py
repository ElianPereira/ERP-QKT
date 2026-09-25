"""Tests de la guía pre-evento (Issue #234): `notificar_guia_evento` y el comando `enviar_guias`."""
from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, GuiaTipoServicio, ItemCotizacion
from comunicacion.models import ComunicacionCliente
from comunicacion.services_notificaciones import notificar_guia_evento

from .utils import TEL_CLIENTE, TEL_EMISOR, RespuestaFalsa, limpiar_cache_emisor, wa_settings

STORAGES_PRUEBA = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _pdf(nombre='guia.pdf', contenido=b'%PDF-1'):
    return SimpleUploadedFile(nombre, contenido, content_type='application/pdf')


def _crear_cotizacion(cliente, tipo_servicio, fecha_evento, estado='CONFIRMADA'):
    cot = Cotizacion.objects.create(
        cliente=cliente, nombre_evento=f'{tipo_servicio} Test',
        tipo_servicio=tipo_servicio, fecha_evento=fecha_evento,
    )
    ItemCotizacion.objects.create(
        cotizacion=cot, descripcion='Servicio', cantidad=1, precio_unitario=1000,
    )
    # .update() en vez de pasar `estado=` a create(): evita depender de si
    # algún signal ligado al alta de una CONFIRMADA hace algo más pesado que
    # lo que este test necesita (mismo patrón que test_hospedaje.py).
    Cotizacion.objects.filter(pk=cot.pk).update(estado=estado)
    cot.refresh_from_db()
    return cot


@wa_settings(WA_TEMPLATE_GUIA='qkt_guia_evento', EMAIL_FROM_RESERVAS='reservas@qkt.mx')
@override_settings(STORAGES=STORAGES_PRUEBA)
class NotificarGuiaEventoTest(TestCase):
    def setUp(self):
        limpiar_cache_emisor()
        self.cliente = Cliente.objects.create(
            nombre='Ana Ruiz', email='ana@example.com', telefono=TEL_CLIENTE,
        )
        self.cot = _crear_cotizacion(self.cliente, 'PASADIA', date.today() + timedelta(days=3))

    def _correr(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()) as post, \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            notificar_guia_evento(self.cot)
        return post

    def test_sin_guia_configurada_no_manda_nada_al_cliente_y_avisa_al_equipo(self):
        mail.outbox = []
        self._correr()
        self.assertEqual(ComunicacionCliente.objects.filter(tipo='EVENTO_PROXIMO').count(), 0)
        alerta = ComunicacionCliente.objects.get(
            clave_idempotencia=f'guia:{self.cot.pk}:falta_config'
        )
        self.assertEqual(alerta.estado, 'ENVIADO')
        self.assertEqual(len(mail.outbox), 1)

    def test_email_sale_de_reservas_con_el_pdf_adjunto(self):
        GuiaTipoServicio.objects.create(tipo_servicio='PASADIA', archivo_pdf=_pdf(contenido=b'%PDF-pasadia'))
        mail.outbox = []
        self._correr()
        self.assertEqual(len(mail.outbox), 1)
        enviado = mail.outbox[0]
        self.assertEqual(enviado.from_email, 'reservas@qkt.mx')
        self.assertEqual(enviado.to, ['ana@example.com'])
        self.assertEqual(len(enviado.attachments), 1)
        nombre, contenido, mime = enviado.attachments[0]
        self.assertEqual(contenido, b'%PDF-pasadia')
        self.assertEqual(mime, 'application/pdf')

    def test_el_whatsapp_lleva_nombre_fecha_y_enlace_de_descarga(self):
        GuiaTipoServicio.objects.create(tipo_servicio='PASADIA', archivo_pdf=_pdf())
        post = self._correr()
        parametros = [
            p['text']
            for p in post.call_args.kwargs['json']['template']['components'][0]['parameters']
        ]
        self.assertEqual(parametros[0], 'Ana')
        self.assertEqual(parametros[1], self.cot.fecha_evento.strftime('%d/%m/%Y'))
        self.assertTrue(parametros[2].startswith('https://portal.test/mi-evento/'))
        self.assertTrue(parametros[2].endswith('guia.pdf'))

    def test_correr_dos_veces_no_duplica(self):
        GuiaTipoServicio.objects.create(tipo_servicio='PASADIA', archivo_pdf=_pdf())
        self._correr()
        self._correr()
        self.assertEqual(ComunicacionCliente.objects.filter(tipo='EVENTO_PROXIMO').count(), 2)

    def test_sin_whatsapp_configurado_el_email_igual_se_manda(self):
        GuiaTipoServicio.objects.create(tipo_servicio='PASADIA', archivo_pdf=_pdf())
        mail.outbox = []
        with override_settings(WA_TEMPLATE_GUIA=''):
            self._correr()
        self.assertEqual(len(mail.outbox), 1)
        comm_wa = ComunicacionCliente.objects.get(tipo='EVENTO_PROXIMO', canal='WHATSAPP')
        self.assertEqual(comm_wa.estado, 'FALLIDO')
        comm_email = ComunicacionCliente.objects.get(tipo='EVENTO_PROXIMO', canal='EMAIL')
        self.assertEqual(comm_email.estado, 'ENVIADO')


@wa_settings(WA_TEMPLATE_GUIA='qkt_guia_evento', EMAIL_FROM_RESERVAS='reservas@qkt.mx')
@override_settings(STORAGES=STORAGES_PRUEBA)
class EnviarGuiasCommandTest(TestCase):
    def setUp(self):
        limpiar_cache_emisor()
        self.hoy = timezone.localdate()
        self.cliente = Cliente.objects.create(
            nombre='Ana Ruiz', email='ana@example.com', telefono=TEL_CLIENTE,
        )
        GuiaTipoServicio.objects.create(tipo_servicio='EVENTO', archivo_pdf=_pdf('g_evento.pdf'))
        GuiaTipoServicio.objects.create(tipo_servicio='PASADIA', archivo_pdf=_pdf('g_pasadia.pdf'))
        GuiaTipoServicio.objects.create(tipo_servicio='HOSPEDAJE', archivo_pdf=_pdf('g_hospedaje.pdf'))

    def _correr(self, **kwargs):
        salida = StringIO()
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()) as post, \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR):
            call_command('enviar_guias', stdout=salida, stderr=StringIO(), **kwargs)
        return salida.getvalue(), post

    def test_avisa_a_los_tres_dias_antes(self):
        _crear_cotizacion(self.cliente, 'EVENTO', self.hoy + timedelta(days=3))
        self._correr()
        self.assertEqual(ComunicacionCliente.objects.filter(tipo='EVENTO_PROXIMO').count(), 2)

    def test_no_avisa_en_dias_fuera_del_calendario(self):
        _crear_cotizacion(self.cliente, 'EVENTO', self.hoy + timedelta(days=4))
        _crear_cotizacion(self.cliente, 'EVENTO', self.hoy - timedelta(days=1))
        self._correr()
        self.assertEqual(ComunicacionCliente.objects.filter(tipo='EVENTO_PROXIMO').count(), 0)

    def test_recupera_la_confirmada_despues_de_su_dia_menos_tres(self):
        # Caso real: pasadía del 26/09 confirmada después del 23/09 — el filtro
        # de día exacto la dejaba sin guía para siempre.
        for dias in (0, 1, 2):
            _crear_cotizacion(self.cliente, 'PASADIA', self.hoy + timedelta(days=dias))
        salida, _ = self._correr()
        self.assertIn('Guías procesadas: 3', salida)
        self.assertEqual(ComunicacionCliente.objects.filter(tipo='EVENTO_PROXIMO').count(), 6)

    def test_no_reenvia_en_los_dias_siguientes_de_la_ventana(self):
        cot = _crear_cotizacion(self.cliente, 'PASADIA', self.hoy + timedelta(days=3))
        self._correr()
        mail.outbox = []
        with patch('django.utils.timezone.localdate', return_value=self.hoy + timedelta(days=1)):
            salida, post = self._correr()
        self.assertIn('Guías procesadas: 0', salida)
        post.assert_not_called()
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(ComunicacionCliente.objects.filter(cotizacion=cot, tipo='EVENTO_PROXIMO').count(), 2)

    def test_cotizacion_no_confirmada_no_recibe_guia(self):
        _crear_cotizacion(self.cliente, 'EVENTO', self.hoy + timedelta(days=3), estado='COTIZADA')
        self._correr()
        self.assertEqual(ComunicacionCliente.objects.filter(tipo='EVENTO_PROXIMO').count(), 0)

    def test_arrendamiento_no_recibe_guia_aunque_este_confirmada_y_en_fecha(self):
        _crear_cotizacion(self.cliente, 'ARRENDAMIENTO', self.hoy + timedelta(days=3))
        self._correr()
        self.assertEqual(ComunicacionCliente.objects.filter(tipo='EVENTO_PROXIMO').count(), 0)

    def test_hospedaje_tambien_recibe_guia(self):
        _crear_cotizacion(self.cliente, 'HOSPEDAJE', self.hoy + timedelta(days=3))
        self._correr()
        self.assertEqual(ComunicacionCliente.objects.filter(tipo='EVENTO_PROXIMO').count(), 2)

    def test_correr_dos_veces_el_mismo_dia_no_duplica(self):
        _crear_cotizacion(self.cliente, 'PASADIA', self.hoy + timedelta(days=3))
        self._correr()
        self._correr()
        self.assertEqual(ComunicacionCliente.objects.filter(tipo='EVENTO_PROXIMO').count(), 2)

    def test_dry_run_no_envia_ni_registra(self):
        _crear_cotizacion(self.cliente, 'EVENTO', self.hoy + timedelta(days=3))
        mail.outbox = []
        salida, post = self._correr(dry_run=True)
        post.assert_not_called()
        self.assertEqual(ComunicacionCliente.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('DRY RUN', salida)

    def test_sin_guia_configurada_no_rompe_el_comando(self):
        GuiaTipoServicio.objects.all().delete()
        _crear_cotizacion(self.cliente, 'EVENTO', self.hoy + timedelta(days=3))
        mail.outbox = []
        salida, _ = self._correr()
        self.assertIn('Guías procesadas: 1', salida)
        # Sin PDF configurado, solo se registra la alerta interna al equipo,
        # nunca ninguna comunicación tipo EVENTO_PROXIMO al cliente.
        self.assertEqual(ComunicacionCliente.objects.filter(tipo='EVENTO_PROXIMO').count(), 0)
        self.assertEqual(len(mail.outbox), 1)


@wa_settings(WA_TEMPLATE_GUIA='qkt_guia_evento', EMAIL_FROM_RESERVAS='reservas@qkt.mx')
@override_settings(STORAGES=STORAGES_PRUEBA)
class GuiaAlConfirmarTest(TestCase):
    """Confirmar con el evento a ≤ 3 días manda la guía de inmediato, sin esperar al cron."""

    def setUp(self):
        limpiar_cache_emisor()
        self.hoy = timezone.localdate()
        self.cliente = Cliente.objects.create(
            nombre='Ana Ruiz', email='ana@example.com', telefono=TEL_CLIENTE,
        )
        GuiaTipoServicio.objects.create(tipo_servicio='PASADIA', archivo_pdf=_pdf())

    def _confirmar(self, cot):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()) as post, \
             patch('comunicacion.services.numero_emisor_wa', return_value=TEL_EMISOR), \
             self.captureOnCommitCallbacks(execute=True):
            cot.estado = 'CONFIRMADA'
            cot.save(update_fields=['estado', 'updated_at'])
        return post

    def _guias(self, cot):
        return ComunicacionCliente.objects.filter(cotizacion=cot, tipo='EVENTO_PROXIMO')

    def test_confirmar_dentro_de_la_ventana_manda_la_guia(self):
        cot = _crear_cotizacion(self.cliente, 'PASADIA', self.hoy + timedelta(days=1), estado='BORRADOR')
        mail.outbox = []
        self._confirmar(cot)
        self.assertEqual(self._guias(cot).count(), 2)
        self.assertTrue(any(m.attachments for m in mail.outbox))

    def test_confirmar_fuera_de_la_ventana_espera_al_cron(self):
        cot = _crear_cotizacion(self.cliente, 'PASADIA', self.hoy + timedelta(days=10), estado='BORRADOR')
        self._confirmar(cot)
        self.assertEqual(self._guias(cot).count(), 0)

    def test_reguardar_una_confirmada_no_reenvia(self):
        cot = _crear_cotizacion(self.cliente, 'PASADIA', self.hoy + timedelta(days=2), estado='BORRADOR')
        self._confirmar(cot)
        mail.outbox = []
        post = self._confirmar(cot)
        post.assert_not_called()
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self._guias(cot).count(), 2)

    def test_arrendamiento_no_recibe_guia_al_confirmar(self):
        cot = _crear_cotizacion(self.cliente, 'ARRENDAMIENTO', self.hoy + timedelta(days=1), estado='BORRADOR')
        self._confirmar(cot)
        self.assertEqual(self._guias(cot).count(), 0)
