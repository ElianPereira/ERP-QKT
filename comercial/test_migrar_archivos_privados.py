from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from comercial.services_migracion_privada import MigracionError, migrar_archivos_privados
from legal.models import SolicitudARCO, TipoARCO

# Dos InMemoryStorage distintos: `storages` instancia uno por alias, así que
# copiar de 'default' a 'privado' es una copia real entre backends.
STORAGES_PRUEBA = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "privado": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def ruta(sufijo):
    """Ruta propia por test.

    El InMemoryStorage del `override_settings` de clase es uno solo para todos
    los tests: lo que copia uno lo encuentra el siguiente y falsea el conteo.
    """
    return f"arco/identificaciones/ine-{sufijo}.jpg"


def crear_solicitud(nombre, titular="Titular de prueba"):
    return SolicitudARCO.objects.create(
        tipo=TipoARCO.ACCESO,
        titular_nombre=titular,
        correo="titular@example.com",
        descripcion="Solicitud de prueba",
        identificacion=nombre,
    )


def sembrar_en_publico(nombre, contenido=b"identificacion"):
    storages["default"].save(nombre, ContentFile(contenido))


# `bucket_name` no es argumento de InMemoryStorage, así que el guardia real no
# se puede satisfacer con override_settings; se prueba aparte, en
# MigrarSinBucketPrivadoTest.
def con_bucket_privado():
    return patch("comercial.services_migracion_privada._hay_bucket_privado", return_value=True)


@override_settings(STORAGES=STORAGES_PRUEBA)
class MigrarArchivosPrivadosServicioTest(TestCase):
    def test_copia_conservando_la_ruta_y_la_segunda_pasada_la_omite(self):
        camino = ruta("copia")
        solicitud = crear_solicitud(camino)
        sembrar_en_publico(camino)

        with con_bucket_privado():
            primera = migrar_archivos_privados(aplicar=True)
            segunda = migrar_archivos_privados(aplicar=True)

        self.assertEqual(primera.copiados, 1)
        self.assertEqual(segunda.copiados, 0)
        self.assertEqual(segunda.ya_estaban, 1)
        # La BD guarda la ruta: si el destino la cambiara, el registro apuntaría
        # a un archivo inexistente.
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.identificacion.name, camino)
        self.assertTrue(storages["privado"].exists(camino))
        with storages["privado"].open(camino, "rb") as archivo:
            self.assertEqual(archivo.read(), b"identificacion")
        # Copia, no mueve: el original sigue donde estaba.
        self.assertTrue(storages["default"].exists(camino))

    def test_simular_no_escribe_nada(self):
        camino = ruta("simulacion")
        crear_solicitud(camino)
        sembrar_en_publico(camino)

        with con_bucket_privado():
            resultado = migrar_archivos_privados()

        self.assertEqual(resultado.copiados, 1)
        self.assertFalse(storages["privado"].exists(camino))

    def test_reporta_los_que_no_estan_en_el_origen(self):
        # Referenciado en la BD pero ausente del bucket público: es el caso de
        # los heredados de Cloudinary.
        solicitud = crear_solicitud(ruta("sin-origen"))

        with con_bucket_privado():
            resultado = migrar_archivos_privados(aplicar=True)

        self.assertEqual(resultado.copiados, 0)
        self.assertEqual(len(resultado.sin_origen), 1)
        self.assertIn(f"pk={solicitud.pk}", resultado.sin_origen[0])

    def test_el_limite_corta_la_pasada_y_la_siguiente_continua(self):
        for indice in range(3):
            camino = ruta(f"limite-{indice}")
            crear_solicitud(camino, titular=f"Titular {indice}")
            sembrar_en_publico(camino)

        with con_bucket_privado():
            primera = migrar_archivos_privados(aplicar=True, limite=2)
            segunda = migrar_archivos_privados(aplicar=True, limite=2)

        self.assertTrue(primera.interrumpido)
        self.assertEqual(primera.copiados, 2)
        self.assertFalse(segunda.interrumpido)
        self.assertEqual(segunda.copiados, 1)
        self.assertEqual(segunda.ya_estaban, 2)

    def test_el_presupuesto_de_tiempo_corta_antes_de_tocar_el_storage(self):
        camino = ruta("tiempo")
        crear_solicitud(camino)
        sembrar_en_publico(camino)

        with con_bucket_privado():
            resultado = migrar_archivos_privados(aplicar=True, tiempo_maximo=0)

        self.assertTrue(resultado.interrumpido)
        self.assertEqual(resultado.copiados, 0)
        self.assertFalse(storages["privado"].exists(camino))


class MigrarSinBucketPrivadoTest(TestCase):
    def test_el_comando_aborta_si_no_hay_bucket_privado(self):
        # Sin CLOUDFLARE_R2_PRIVATE_BUCKET_NAME el storage privado cae al
        # default, y copiar de un bucket a sí mismo no tendría sentido.
        with self.assertRaisesMessage(CommandError, "No hay bucket privado"):
            call_command("migrar_archivos_privados")

    def test_el_servicio_lanza_su_propio_error(self):
        with self.assertRaisesMessage(MigracionError, "No hay bucket privado"):
            migrar_archivos_privados()


@override_settings(STORAGES=STORAGES_PRUEBA)
class MigrarArchivosPrivadosComandoTest(TestCase):
    def test_simula_por_defecto_y_avisa_de_como_aplicar(self):
        camino = ruta("comando")
        crear_solicitud(camino)
        sembrar_en_publico(camino)
        out = StringIO()

        with con_bucket_privado():
            call_command("migrar_archivos_privados", stdout=out)

        salida = out.getvalue()
        self.assertIn("Se copiarían: 1", salida)
        self.assertIn("--aplicar", salida)
        self.assertFalse(storages["privado"].exists(camino))


@override_settings(STORAGES=STORAGES_PRUEBA)
class MigrarArchivosPrivadosVistaTest(TestCase):
    url = "/admin/migrar-archivos-privados/"

    def setUp(self):
        self.superusuario = User.objects.create_superuser(
            "jefa", "jefa@quintakooxtanil.com", "clave-de-prueba"
        )
        self.staff = User.objects.create_user(
            "empleado", "empleado@quintakooxtanil.com", "clave-de-prueba", is_staff=True
        )

    def test_anonimo_va_al_login(self):
        respuesta = self.client.get(self.url)

        self.assertEqual(respuesta.status_code, 302)
        self.assertIn("/admin/login/", respuesta["Location"])

    def test_staff_sin_superusuario_no_entra(self):
        self.client.force_login(self.staff)

        respuesta = self.client.get(self.url)

        self.assertRedirects(respuesta, "/admin/", fetch_redirect_response=False)

    def test_get_no_ejecuta_nada(self):
        self.client.force_login(self.superusuario)
        camino = ruta("vista-get")
        crear_solicitud(camino)
        sembrar_en_publico(camino)

        respuesta = self.client.get(self.url)

        self.assertEqual(respuesta.status_code, 200)
        self.assertIsNone(respuesta.context["resultado"])
        self.assertFalse(storages["privado"].exists(camino))

    def test_post_simula_por_defecto(self):
        self.client.force_login(self.superusuario)
        camino = ruta("vista-simular")
        crear_solicitud(camino)
        sembrar_en_publico(camino)

        with con_bucket_privado():
            respuesta = self.client.post(self.url, {"accion": "simular"})

        self.assertFalse(respuesta.context["aplicado"])
        self.assertEqual(respuesta.context["resultado"].copiados, 1)
        self.assertFalse(storages["privado"].exists(camino))

    def test_post_con_accion_aplicar_copia(self):
        self.client.force_login(self.superusuario)
        camino = ruta("vista-aplicar")
        crear_solicitud(camino)
        sembrar_en_publico(camino)

        with con_bucket_privado():
            respuesta = self.client.post(self.url, {"accion": "aplicar"})

        self.assertTrue(respuesta.context["aplicado"])
        self.assertEqual(respuesta.context["resultado"].copiados, 1)
        self.assertTrue(storages["privado"].exists(camino))

    def test_muestra_el_error_en_vez_de_reventar(self):
        self.client.force_login(self.superusuario)

        # Sin bucket privado configurado: la página lo explica, no da un 500.
        respuesta = self.client.post(self.url, {"accion": "aplicar"})

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("No hay bucket privado", respuesta.context["error"])
