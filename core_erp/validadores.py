"""Validación de archivos subidos por firma (magic bytes), no por lo que declara el navegador."""
from django.core.exceptions import ValidationError

FIRMAS_IMAGEN_O_PDF = {
    b'\xff\xd8\xff': 'jpg',
    b'\x89PNG': 'png',
    b'%PDF': 'pdf',
}
FIRMAS_EXCEL = {
    b'PK\x03\x04': 'xlsx',
    b'\xd0\xcf\x11\xe0': 'xls',
}


def validar_archivo(archivo, firmas_permitidas, max_bytes):
    """Devuelve la extensión que corresponde a la firma del archivo.

    Lee los primeros bytes y deja el puntero donde estaba. Lanza
    ValidationError si pesa más de `max_bytes` o su firma no está permitida.
    """
    if archivo.size > max_bytes:
        raise ValidationError(f'El archivo pesa demasiado (máximo {max_bytes // (1024 * 1024)} MB).')
    posicion = archivo.tell()
    archivo.seek(0)
    cabecera = archivo.read(8)
    archivo.seek(posicion)
    for firma, extension in firmas_permitidas.items():
        if cabecera.startswith(firma):
            return extension
    raise ValidationError('Formato no válido.')
