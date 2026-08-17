"""
Validadores para los `FileField`/`ImageField` que aceptan archivos subidos
por staff desde el admin (facturas, XML de CFDI, estados de cuenta,
imágenes de la landing, etc.).

Dos capas, orden 35 del backlog de seguridad (`SEC-FILE-002`):

1. Extensión declarada (`FileExtensionValidator`, de Django) — barata,
   solo mira el nombre del archivo.
2. Firma binaria real, para PDF y XML — un `.html` renombrado a `.pdf`
   pasa la validación de extensión pero no la de firma.

La firma solo se revisa si el archivo es una carga nueva en esta
petición (`isinstance(archivo.file, UploadedFile)`): un `FieldFile` ya
guardado (p. ej. porque se editó otro campo del mismo registro) no debe
releerse desde el storage remoto en cada `full_clean()`.
"""
import defusedxml.ElementTree as ET
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import FileExtensionValidator

extension_pdf = FileExtensionValidator(allowed_extensions=['pdf'])
extension_xml = FileExtensionValidator(allowed_extensions=['xml'])
extension_zip = FileExtensionValidator(allowed_extensions=['zip'])
extension_pdf_o_xml = FileExtensionValidator(allowed_extensions=['pdf', 'xml'])
extension_imagen = FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'webp'])
extension_identificacion = FileExtensionValidator(allowed_extensions=['pdf', 'jpg', 'jpeg', 'png'])

_FIRMA_PDF = b'%PDF-'
_FIRMAS_ZIP = (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')


def _es_carga_nueva(archivo):
    """True solo si `archivo` es contenido subido en esta petición, no un
    `FieldFile` ya persistido (que releerlo dispararía una descarga del
    storage remoto en cada guardado del registro, cambie o no el archivo).

    Durante `Model.full_clean()`, Django envuelve un `UploadedFile` recién
    asignado en un `FieldFile` cuyo `.file` es ese mismo `UploadedFile`
    (`FileDescriptor.__get__`) — por eso se comprueban las dos formas: la
    envuelta (el caso real de un `ModelForm`) y la directa (un validador
    invocado a mano contra el `UploadedFile`, sin pasar por el descriptor)."""
    if isinstance(archivo, UploadedFile):
        return True
    return isinstance(getattr(archivo, 'file', None), UploadedFile)


def _inicio(archivo, n):
    posicion = archivo.tell()
    archivo.seek(0)
    datos = archivo.read(n)
    archivo.seek(posicion)
    return datos


def validar_firma_pdf(archivo):
    if not _es_carga_nueva(archivo):
        return
    if not _inicio(archivo, len(_FIRMA_PDF)).startswith(_FIRMA_PDF):
        raise ValidationError(
            'El archivo no es un PDF válido: su contenido no coincide con '
            'la firma de un PDF, aunque el nombre termine en .pdf.',
            code='firma_invalida',
        )


def validar_firma_zip(archivo):
    if not _es_carga_nueva(archivo):
        return
    if not _inicio(archivo, 4).startswith(_FIRMAS_ZIP):
        raise ValidationError(
            'El archivo no es un ZIP válido: su contenido no coincide con '
            'la firma de un ZIP, aunque el nombre termine en .zip.',
            code='firma_invalida',
        )


def validar_firma_xml(archivo):
    if not _es_carga_nueva(archivo):
        return
    _validar_xml_bien_formado(archivo)


def validar_firma_pdf_o_xml(archivo):
    """Para campos que aceptan indistintamente PDF o XML (estados de
    cuenta bancarios: `formato` puede ser cualquiera de los dos)."""
    if not _es_carga_nueva(archivo):
        return
    if _inicio(archivo, len(_FIRMA_PDF)).startswith(_FIRMA_PDF):
        return
    _validar_xml_bien_formado(archivo)


def _validar_xml_bien_formado(archivo):
    posicion = archivo.tell()
    archivo.seek(0)
    contenido = archivo.read()
    archivo.seek(posicion)
    try:
        ET.fromstring(contenido)
    except ET.ParseError as exc:
        raise ValidationError(
            'El archivo no es un XML válido: no se pudo interpretar su '
            'contenido, aunque el nombre termine en .xml.',
            code='firma_invalida',
        ) from exc
