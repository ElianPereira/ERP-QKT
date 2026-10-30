from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from core_erp.validadores import FIRMAS_EXCEL, FIRMAS_IMAGEN_O_PDF, validar_archivo


class ValidarArchivoTest(SimpleTestCase):
    def test_reconoce_firmas_y_devuelve_el_puntero(self):
        for cuerpo, ext in ((b'\xff\xd8\xff\xe0abc', 'jpg'), (b'\x89PNG\r\n\x1a\n', 'png'), (b'%PDF-1.7', 'pdf')):
            f = SimpleUploadedFile('x', cuerpo)
            self.assertEqual(validar_archivo(f, FIRMAS_IMAGEN_O_PDF, 1024), ext)
            self.assertEqual(f.tell(), 0)

    def test_html_disfrazado_y_heic_se_rechazan(self):
        for cuerpo in (b'<html><script>', b'\x00\x00\x00\x18ftypheic'):
            with self.assertRaises(ValidationError):
                validar_archivo(SimpleUploadedFile('a.jpg', cuerpo, 'image/jpeg'), FIRMAS_IMAGEN_O_PDF, 1024)

    def test_tope_de_tamano(self):
        with self.assertRaises(ValidationError):
            validar_archivo(SimpleUploadedFile('a.pdf', b'%PDF' + b'0' * 2000), FIRMAS_IMAGEN_O_PDF, 1024)

    def test_excel(self):
        self.assertEqual(validar_archivo(SimpleUploadedFile('a', b'PK\x03\x04zz'), FIRMAS_EXCEL, 1024), 'xlsx')
        self.assertEqual(validar_archivo(SimpleUploadedFile('a', b'\xd0\xcf\x11\xe0zz'), FIRMAS_EXCEL, 1024), 'xls')
