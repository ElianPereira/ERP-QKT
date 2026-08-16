"""
Test del backlog de seguridad (Issue #190), orden 49 (SEC-CFG-005):
MEDIA_ROOT definido explícitamente en settings.py, en vez de caer al
default global de Django (`''`) que hacía que /media/ sirviera (o
fallara en servir) desde el directorio de trabajo actual.

Ejecutar: python manage.py test core_erp.test_media_root --verbosity=2
"""
from pathlib import Path

from django.conf import settings
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.views.static import serve


class MediaRootTest(TestCase):
    def test_media_root_es_una_ruta_real_del_proyecto(self):
        self.assertTrue(settings.MEDIA_ROOT)
        self.assertEqual(Path(settings.MEDIA_ROOT), settings.BASE_DIR / 'media')

    def test_servir_un_archivo_inexistente_da_404_no_un_error_de_configuracion(self):
        # Prueba directamente la vista que core_erp/urls.py conecta con
        # document_root=settings.MEDIA_ROOT cuando DEBUG=True (django.views.
        # static.serve), sin depender de si el test runner de Django fuerza
        # DEBUG=False durante la suite (lo hace, por defecto).
        request = RequestFactory().get('/media/algo-que-no-existe.jpg')
        with self.assertRaises(Http404):
            serve(request, 'algo-que-no-existe.jpg', document_root=settings.MEDIA_ROOT)
