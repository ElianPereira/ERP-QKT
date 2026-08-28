"""
Tests de la subida de identificación oficial (INE) desde el portal del
cliente — requisito previo al pago, decisión del propietario (2026-08-25).

Ver comercial/views_portal.py::portal_subir_identificacion,
Cotizacion.identificacion_completa() y el gate en
comercial/views_openpay.py::portal_procesar_pago_openpay.

Ejecutar: python manage.py test comercial.test_portal_identificacion --verbosity=2
"""
from datetime import date, timedelta

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from comercial.models import Cliente, Cotizacion, ItemCotizacion, PortalCliente

STORAGES_PRUEBA = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "privado": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _crear_cotizacion():
    cliente = Cliente.objects.create(nombre='Cliente Portal INE')
    cotizacion = Cotizacion.objects.create(
        cliente=cliente,
        nombre_evento='Evento de prueba',
        fecha_evento=date.today() + timedelta(days=90),
    )
    ItemCotizacion.objects.create(
        cotizacion=cotizacion, descripcion='Servicio', cantidad=1, precio_unitario=1000,
    )
    return cotizacion


@override_settings(STORAGES=STORAGES_PRUEBA)
class PortalSubirIdentificacionTest(TestCase):
    def setUp(self):
        cache.clear()
        self.cotizacion = _crear_cotizacion()
        self.portal = PortalCliente.objects.get(cotizacion=self.cotizacion)
        self.url = reverse('portal_subir_identificacion', args=[self.portal.token])

    def test_token_invalido_regresa_404(self):
        url = reverse('portal_subir_identificacion', args=['token-inexistente'])
        archivo = SimpleUploadedFile('ine.jpg', b'contenido', content_type='image/jpeg')
        response = self.client.post(url, {'identificacion': archivo})
        self.assertEqual(response.status_code, 404)

    def test_get_no_permitido(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_sin_archivo_rechazado(self):
        response = self.client.post(self.url, {})
        self.assertFalse(response.json()['ok'])
        self.cotizacion.refresh_from_db()
        self.assertFalse(self.cotizacion.identificacion_completa())

    def test_formato_no_permitido_rechazado(self):
        archivo = SimpleUploadedFile('ine.txt', b'contenido', content_type='text/plain')
        response = self.client.post(self.url, {'identificacion': archivo})
        self.assertFalse(response.json()['ok'])
        self.cotizacion.refresh_from_db()
        self.assertFalse(self.cotizacion.identificacion_completa())

    def test_archivo_demasiado_grande_rechazado(self):
        contenido = b'x' * (8 * 1024 * 1024 + 1)
        archivo = SimpleUploadedFile('ine.jpg', contenido, content_type='image/jpeg')
        response = self.client.post(self.url, {'identificacion': archivo})
        self.assertFalse(response.json()['ok'])
        self.assertIn('8 MB', response.json()['mensaje'])

    def test_jpg_valido_se_guarda(self):
        archivo = SimpleUploadedFile('ine.jpg', b'contenido-jpg', content_type='image/jpeg')
        response = self.client.post(self.url, {'identificacion': archivo})
        self.assertTrue(response.json()['ok'])
        self.cotizacion.refresh_from_db()
        self.assertTrue(self.cotizacion.identificacion_completa())

    def test_pdf_valido_se_guarda(self):
        archivo = SimpleUploadedFile('ine.pdf', b'%PDF-1.4', content_type='application/pdf')
        response = self.client.post(self.url, {'identificacion': archivo})
        self.assertTrue(response.json()['ok'])
        self.cotizacion.refresh_from_db()
        self.assertTrue(self.cotizacion.identificacion_completa())

    def test_once_intentos_activan_el_limite(self):
        for _ in range(10):
            self.client.post(self.url, {})
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, 429)
