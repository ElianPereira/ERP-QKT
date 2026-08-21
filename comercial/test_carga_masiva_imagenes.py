"""
Carga masiva de imágenes de la página web (`/admin/comercial/imagenlanding/
carga-masiva/`) y la acción que desactiva los registros cuyo archivo ya no
está en el storage.

El storage se sustituye por `InMemoryStorage`: los tests no deben subir nada
al bucket ni necesitar credenciales en CI, y así `exists()` responde de
verdad sobre lo que cada test guardó.
"""

from io import BytesIO

from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from comercial.models import EspacioLanding, ImagenLanding
from core_erp.test_utils import login_superuser_con_totp

STORAGES_PRUEBA = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def imagen_valida(nombre):
    """PNG real de 1x1: un SimpleUploadedFile con bytes arbitrarios y
    content_type 'image/png' NO pasa la validación de ImageField, que abre el
    archivo con Pillow."""
    from PIL import Image

    buffer = BytesIO()
    Image.new('RGB', (1, 1)).save(buffer, format='PNG')
    return SimpleUploadedFile(nombre, buffer.getvalue(), content_type='image/png')


@override_settings(STORAGES=STORAGES_PRUEBA)
class CargaMasivaImagenesTest(TestCase):
    def setUp(self):
        self.url = reverse('admin:imagenlanding_carga_masiva')
        self.admin = User.objects.create_superuser('jefa', 'jefa@qkt.mx', 'x')
        login_superuser_con_totp(self.client, self.admin)

    def _subir(self, archivos, **extra):
        datos = {'seccion': 'GALERIA', 'imagenes': archivos}
        datos.update(extra)
        return self.client.post(self.url, datos, follow=True)

    def test_crea_un_registro_por_archivo_con_orden_consecutivo(self):
        self._subir([imagen_valida('a.png'), imagen_valida('b.png'), imagen_valida('c.png')])

        creadas = ImagenLanding.objects.filter(seccion='GALERIA').order_by('orden')
        self.assertEqual(creadas.count(), 3)
        self.assertEqual([i.orden for i in creadas], [1, 2, 3])
        self.assertEqual([i.titulo for i in creadas], ['a', 'b', 'c'])
        # El alt_text lo escribe una persona, no se autogenera.
        self.assertEqual({i.alt_text for i in creadas}, {''})

    def test_el_orden_continua_desde_el_maximo_de_la_seccion(self):
        ImagenLanding.objects.create(seccion='GALERIA', imagen='landing/vieja.jpg', orden=7)

        self._subir([imagen_valida('nueva.png')])

        nueva = ImagenLanding.objects.get(titulo='nueva')
        self.assertEqual(nueva.orden, 8)

    def test_un_archivo_invalido_se_descarta_sin_frenar_a_los_validos(self):
        falso = SimpleUploadedFile('documento.png', b'esto no es un PNG', content_type='image/png')

        respuesta = self._subir([imagen_valida('buena.png'), falso])

        self.assertEqual(ImagenLanding.objects.count(), 1)
        self.assertEqual(ImagenLanding.objects.get().titulo, 'buena')
        self.assertContains(respuesta, 'documento.png')

    def test_post_sin_archivos_no_crea_nada(self):
        respuesta = self.client.post(self.url, {'seccion': 'GALERIA'}, follow=True)

        self.assertEqual(ImagenLanding.objects.count(), 0)
        self.assertContains(respuesta, 'No seleccionaste ninguna imagen.')

    def test_post_sin_seccion_valida_no_crea_nada(self):
        respuesta = self.client.post(
            self.url, {'seccion': 'INVENTADA', 'imagenes': [imagen_valida('a.png')]}, follow=True,
        )

        self.assertEqual(ImagenLanding.objects.count(), 0)
        self.assertContains(respuesta, 'Elige una sección válida.')

    def test_staff_sin_permiso_de_alta_recibe_403(self):
        operador = User.objects.create_user('operador', 'op@qkt.mx', 'x', is_staff=True)
        operador.user_permissions.add(Permission.objects.get(codename='view_imagenlanding'))
        self.client.force_login(operador)

        respuesta = self.client.get(self.url)

        self.assertEqual(respuesta.status_code, 403)


@override_settings(STORAGES=STORAGES_PRUEBA)
class DesactivarSinArchivoTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('jefa', 'jefa@qkt.mx', 'x')
        login_superuser_con_totp(self.client, self.admin)

    def _ejecutar(self, ruta, ids):
        return self.client.post(
            ruta,
            {'action': 'desactivar_sin_archivo', '_selected_action': ids},
            follow=True,
        )

    def test_desactiva_solo_las_que_perdieron_el_archivo(self):
        # `imagen=` con un name a secas simula el registro huérfano que dejó la
        # migración de Cloudinary a R2: apunta a un archivo que no existe.
        rota = ImagenLanding.objects.create(seccion='GALERIA', imagen='landing/perdida.jpg')
        buena = ImagenLanding.objects.create(seccion='GALERIA', imagen=imagen_valida('viva.png'))

        self._ejecutar('/admin/comercial/imagenlanding/', [rota.pk, buena.pk])

        rota.refresh_from_db()
        buena.refresh_from_db()
        self.assertFalse(rota.activo)
        self.assertTrue(buena.activo)

    def test_no_borra_ningun_registro_ni_toca_otros_campos(self):
        rota = ImagenLanding.objects.create(
            seccion='GALERIA', imagen='landing/perdida.jpg',
            titulo='Jardín al atardecer', alt_text='Jardín iluminado', orden=4,
        )

        self._ejecutar('/admin/comercial/imagenlanding/', [rota.pk])

        rota.refresh_from_db()
        self.assertEqual(ImagenLanding.objects.count(), 1)
        self.assertEqual(rota.titulo, 'Jardín al atardecer')
        self.assertEqual(rota.alt_text, 'Jardín iluminado')
        self.assertEqual(rota.orden, 4)

    def test_tambien_funciona_sobre_espacios(self):
        espacio = EspacioLanding.objects.create(
            nombre='Palapa', imagen='landing/palapa.jpg', capacidad='Hasta 200 invitados',
        )

        self._ejecutar('/admin/comercial/espaciolanding/', [espacio.pk])

        espacio.refresh_from_db()
        self.assertFalse(espacio.activo)
