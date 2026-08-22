"""
Tests de la guía pre-evento (Issue #234): modelo `GuiaTipoServicio` y la
descarga protegida `portal_descargar_guia`.

El envío automático (email + WhatsApp) se prueba en
`comunicacion/tests/test_guias.py`, junto al comando `enviar_guias`.
"""
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from comercial.models import Cliente, Cotizacion, GuiaTipoServicio, ItemCotizacion, PortalCliente

STORAGES_PRUEBA = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _crear_cotizacion(tipo_servicio='EVENTO'):
    cliente = Cliente.objects.create(nombre='Cliente Guía', telefono='9990001122')
    cotizacion = Cotizacion.objects.create(
        cliente=cliente,
        nombre_evento='Evento Guía',
        tipo_servicio=tipo_servicio,
        fecha_evento=date.today() + timedelta(days=10),
    )
    # Cotizacion.save() ya crea el PortalCliente automáticamente.
    ItemCotizacion.objects.create(
        cotizacion=cotizacion, descripcion='Servicio', cantidad=1, precio_unitario=1000,
    )
    return cotizacion


class GuiaTipoServicioModelTest(TestCase):
    def test_arrendamiento_no_esta_entre_los_tipos_con_guia(self):
        codigos = {c[0] for c in GuiaTipoServicio.TIPOS_CON_GUIA}
        self.assertEqual(codigos, {'EVENTO', 'PASADIA', 'HOSPEDAJE'})

    @override_settings(STORAGES=STORAGES_PRUEBA)
    def test_tipo_servicio_es_unico(self):
        GuiaTipoServicio.objects.create(
            tipo_servicio='EVENTO',
            archivo_pdf=SimpleUploadedFile('guia.pdf', b'%PDF-1', content_type='application/pdf'),
        )
        duplicado = GuiaTipoServicio(
            tipo_servicio='EVENTO',
            archivo_pdf=SimpleUploadedFile('otra.pdf', b'%PDF-2', content_type='application/pdf'),
        )
        with self.assertRaises(ValidationError):
            duplicado.full_clean()

    @override_settings(STORAGES=STORAGES_PRUEBA)
    def test_str_incluye_el_tipo_de_servicio(self):
        guia = GuiaTipoServicio.objects.create(
            tipo_servicio='PASADIA',
            archivo_pdf=SimpleUploadedFile('guia.pdf', b'%PDF-1', content_type='application/pdf'),
        )
        self.assertIn('Pasadía', str(guia))


@override_settings(STORAGES=STORAGES_PRUEBA)
class PortalDescargarGuiaTest(TestCase):
    def setUp(self):
        self.cotizacion = _crear_cotizacion(tipo_servicio='PASADIA')
        self.portal = PortalCliente.objects.get(cotizacion=self.cotizacion)
        self.url = reverse('portal_descargar_guia', args=[self.portal.token])

    def test_sirve_el_pdf_del_tipo_de_servicio_correcto(self):
        GuiaTipoServicio.objects.create(
            tipo_servicio='PASADIA',
            archivo_pdf=SimpleUploadedFile('guia_pasadia.pdf', b'%PDF-pasadia', content_type='application/pdf'),
        )
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        self.assertEqual(b''.join(respuesta.streaming_content), b'%PDF-pasadia')

    def test_no_mezcla_el_pdf_de_otro_tipo_de_servicio(self):
        GuiaTipoServicio.objects.create(
            tipo_servicio='EVENTO',
            archivo_pdf=SimpleUploadedFile('guia_evento.pdf', b'%PDF-evento', content_type='application/pdf'),
        )
        # La cotización es PASADIA; solo existe guía de EVENTO configurada.
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 404)

    def test_404_si_no_hay_guia_configurada(self):
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 404)

    def test_respuesta_no_cacheable(self):
        GuiaTipoServicio.objects.create(
            tipo_servicio='PASADIA',
            archivo_pdf=SimpleUploadedFile('guia.pdf', b'%PDF-1', content_type='application/pdf'),
        )
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta['Cache-Control'], 'private, no-store')
