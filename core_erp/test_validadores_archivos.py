"""
Orden 35 del backlog de seguridad (`SEC-FILE-002`): `FileExtensionValidator`
+ verificación de firma binaria en los `FileField`/`ImageField` que el admin
deja editar.

Dos niveles de prueba:

1. Unitario, contra las funciones de `core_erp/validadores_archivos.py` en
   aislado — cubre la lógica de firma y el candado de "solo carga nueva".
2. De cableado, contra `Model._meta.get_field(...).run_validators(...)` de
   cada uno de los 16 campos reales — no basta con que el validador exista,
   tiene que estar de verdad enchufado al campo del modelo (mismo criterio
   que ya aplicó la orden 51, `SEC-TEST-001`).
"""
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from comercial.models import (
    Compra,
    ContratoServicio,
    Cotizacion,
    EspacioLanding,
    Gasto,
    ImagenLanding,
    Producto,
)
from contabilidad.models import EstadoCuentaBancario
from core_erp.validadores_archivos import (
    validar_firma_pdf,
    validar_firma_pdf_o_xml,
    validar_firma_xml,
    validar_firma_zip,
)
from facturacion.models import SolicitudFactura
from legal.models import SolicitudARCO
from nomina.models import ReciboNomina

STORAGES_PRUEBA = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "privado": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

PDF_REAL = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<</Type/Catalog>>endobj'
XML_REAL = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"></cfdi:Comprobante>'
)
ZIP_REAL = b'PK\x03\x04' + b'\x00' * 20
# Un '&' suelto (no escapado como '&amp;') es HTML válido pero XML mal
# formado — necesario para que el disfraz falle también la validación de
# XML, no solo la de PDF/ZIP (un HTML bien formado sin ese detalle *sí*
# pasaría como XML válido, ya que XHTML-like también lo es).
HTML_DISFRAZADO = b'<html><body>Cotizaciones & Eventos <script>alert(1)</script></body></html>'


def _pdf(nombre='doc.pdf'):
    return SimpleUploadedFile(nombre, PDF_REAL, content_type='application/pdf')


def _xml(nombre='doc.xml'):
    return SimpleUploadedFile(nombre, XML_REAL, content_type='application/xml')


def _zip(nombre='doc.zip'):
    return SimpleUploadedFile(nombre, ZIP_REAL, content_type='application/zip')


def _html_como(nombre):
    return SimpleUploadedFile(nombre, HTML_DISFRAZADO, content_type='application/octet-stream')


class ValidarFirmaUnitarioTest(TestCase):
    """Las funciones de firma en aislado, sin tocar ningún modelo."""

    def test_pdf_real_no_lanza(self):
        validar_firma_pdf(_pdf())

    def test_html_renombrado_a_pdf_es_rechazado(self):
        with self.assertRaises(ValidationError):
            validar_firma_pdf(_html_como('doc.pdf'))

    def test_xml_real_no_lanza(self):
        validar_firma_xml(_xml())

    def test_html_renombrado_a_xml_es_rechazado(self):
        with self.assertRaises(ValidationError):
            validar_firma_xml(_html_como('doc.xml'))

    def test_zip_real_no_lanza(self):
        validar_firma_zip(_zip())

    def test_html_renombrado_a_zip_es_rechazado(self):
        with self.assertRaises(ValidationError):
            validar_firma_zip(_html_como('doc.zip'))

    def test_pdf_o_xml_acepta_cualquiera_de_los_dos(self):
        validar_firma_pdf_o_xml(_pdf())
        validar_firma_pdf_o_xml(_xml())

    def test_pdf_o_xml_rechaza_lo_que_no_es_ninguno(self):
        with self.assertRaises(ValidationError):
            validar_firma_pdf_o_xml(_html_como('estado.pdf'))

    def test_xml_vacio_es_rechazado(self):
        with self.assertRaises(ValidationError):
            validar_firma_xml(SimpleUploadedFile('vacio.xml', b'', content_type='application/xml'))

    def test_pdf_deja_el_puntero_al_inicio_para_que_django_pueda_guardarlo(self):
        archivo = _pdf()
        validar_firma_pdf(archivo)
        self.assertEqual(archivo.read(), PDF_REAL)


@override_settings(STORAGES=STORAGES_PRUEBA)
class CargaYaGuardadaNoSeRevalidaTest(TestCase):
    """Un `FieldFile` ya persistido (no una carga nueva de este request) no
    debe releerse del storage en cada `full_clean()` del registro: sería una
    llamada de red extra en cada guardado del modelo, cambie o no el archivo.
    Se comprueba guardando contenido que a propósito NO pasaría la firma, por
    la vía que se salta la validación (`.objects.create()`, igual que hacen
    las señales y los servicios internos), y confirmando que releerlo desde
    el campo no dispara ningún error."""

    def test_archivo_persistido_con_contenido_invalido_no_lanza_al_revalidar(self):
        compra = Compra.objects.create(
            archivo_xml=SimpleUploadedFile('factura.xml', HTML_DISFRAZADO, content_type='application/xml'),
        )
        recargada = Compra.objects.get(pk=compra.pk)
        # No debe lanzar: es un FieldFile ya guardado, no una carga nueva.
        validar_firma_xml(recargada.archivo_xml)


class CampoTieneValidadoresDeArchivoTest(TestCase):
    """Cada uno de los 16 `FileField`/`ImageField` de la orden 35 tiene sus
    validadores realmente enchufados — no basta con que la función exista en
    `validadores_archivos.py`, cada campo debe declararla en `validators=`."""

    def _assert_rechaza_extension(self, modelo, campo, nombre_malo):
        validadores = modelo._meta.get_field(campo).validators
        archivo = SimpleUploadedFile(nombre_malo, b'contenido', content_type='application/octet-stream')
        with self.assertRaises(ValidationError, msg=f'{modelo.__name__}.{campo}'):
            for validador in validadores:
                validador(archivo)

    def _assert_rechaza_firma(self, modelo, campo, nombre_bueno):
        validadores = modelo._meta.get_field(campo).validators
        archivo = _html_como(nombre_bueno)
        with self.assertRaises(ValidationError, msg=f'{modelo.__name__}.{campo}'):
            for validador in validadores:
                validador(archivo)

    def test_facturacion_solicitudfactura(self):
        self._assert_rechaza_extension(SolicitudFactura, 'archivo_zip', 'x.exe')
        self._assert_rechaza_extension(SolicitudFactura, 'archivo_pdf', 'x.exe')
        self._assert_rechaza_extension(SolicitudFactura, 'archivo_xml', 'x.exe')
        self._assert_rechaza_firma(SolicitudFactura, 'archivo_zip', 'x.zip')
        self._assert_rechaza_firma(SolicitudFactura, 'archivo_pdf', 'x.pdf')
        self._assert_rechaza_firma(SolicitudFactura, 'archivo_xml', 'x.xml')

    def test_nomina_recibonomina(self):
        self._assert_rechaza_extension(ReciboNomina, 'archivo_pdf', 'x.exe')
        self._assert_rechaza_firma(ReciboNomina, 'archivo_pdf', 'x.pdf')

    def test_legal_solicitudarco(self):
        self._assert_rechaza_extension(SolicitudARCO, 'identificacion', 'x.exe')

    def test_contabilidad_estadocuentabancario(self):
        self._assert_rechaza_extension(EstadoCuentaBancario, 'archivo', 'x.exe')
        self._assert_rechaza_firma(EstadoCuentaBancario, 'archivo', 'x.pdf')

    def test_comercial_producto_imagen_promocional(self):
        self._assert_rechaza_extension(Producto, 'imagen_promocional', 'x.exe')

    def test_comercial_cotizacion(self):
        self._assert_rechaza_extension(Cotizacion, 'archivo_pdf', 'x.exe')
        self._assert_rechaza_extension(Cotizacion, 'archivo_contrato', 'x.exe')
        self._assert_rechaza_firma(Cotizacion, 'archivo_pdf', 'x.pdf')
        self._assert_rechaza_firma(Cotizacion, 'archivo_contrato', 'x.pdf')

    def test_comercial_contratoservicio(self):
        self._assert_rechaza_extension(ContratoServicio, 'archivo', 'x.exe')
        self._assert_rechaza_firma(ContratoServicio, 'archivo', 'x.pdf')

    def test_comercial_compra(self):
        self._assert_rechaza_extension(Compra, 'archivo_xml', 'x.exe')
        self._assert_rechaza_extension(Compra, 'archivo_pdf', 'x.exe')
        self._assert_rechaza_firma(Compra, 'archivo_xml', 'x.xml')
        self._assert_rechaza_firma(Compra, 'archivo_pdf', 'x.pdf')

    def test_comercial_gasto(self):
        self._assert_rechaza_extension(Gasto, 'archivo_xml', 'x.exe')
        self._assert_rechaza_extension(Gasto, 'archivo_pdf', 'x.exe')
        self._assert_rechaza_firma(Gasto, 'archivo_xml', 'x.xml')
        self._assert_rechaza_firma(Gasto, 'archivo_pdf', 'x.pdf')

    def test_comercial_imagenlanding(self):
        self._assert_rechaza_extension(ImagenLanding, 'imagen', 'x.exe')

    def test_comercial_espaciolanding(self):
        self._assert_rechaza_extension(EspacioLanding, 'imagen', 'x.exe')


class CompraFullCleanRechazaHtmlDisfrazadoTest(TestCase):
    """El criterio de aceptación del backlog ('un .html renombrado a .pdf es
    rechazado') probado contra `full_clean()` de un `Compra` real — no solo
    contra la función de firma en aislado, sino contra el mismo camino que
    recorre el `ModelForm` del admin al guardar."""

    def test_full_clean_rechaza_html_disfrazado_de_pdf(self):
        compra = Compra(archivo_pdf=_html_como('factura.pdf'))
        with self.assertRaises(ValidationError) as excepcion:
            compra.full_clean()
        self.assertIn('archivo_pdf', excepcion.exception.message_dict)

    def test_full_clean_acepta_pdf_real(self):
        compra = Compra(archivo_pdf=_pdf('factura.pdf'))
        compra.full_clean(exclude=[f.name for f in Compra._meta.fields if f.name != 'archivo_pdf'])
