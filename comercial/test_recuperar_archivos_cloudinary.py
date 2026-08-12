from io import BytesIO, StringIO
from unittest.mock import patch

import requests
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from comercial.models import Producto
from comercial.services_recuperacion import (
    ARCHIVOS_CLOUDINARY,
    RecuperacionError,
    recuperar_archivos,
)

STORAGES_PRUEBA = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def respuesta_http(status, contenido=b""):
    respuesta = requests.Response()
    respuesta.status_code = status
    respuesta._content = contenido
    respuesta.raw = BytesIO(contenido)
    return respuesta


@override_settings(STORAGES=STORAGES_PRUEBA)
class RecuperarArchivosCloudinaryTest(TestCase):
    def test_base_vacia_termina_con_resumen_en_ceros(self):
        out = StringIO()

        with patch("comercial.services_recuperacion.requests.get") as get:
            call_command("recuperar_archivos_cloudinary", stdout=out)

        get.assert_not_called()
        self.assertIn("Total procesados: 0", out.getvalue())
        self.assertIn("Recuperados: 0", out.getvalue())
        self.assertIn("No encontrados: 0", out.getvalue())

    def test_recupera_con_el_mismo_nombre_y_la_segunda_ejecucion_lo_omite(self):
        producto = Producto.objects.create(
            nombre="Producto histórico",
            imagen_promocional="productos/historica.jpg",
        )
        out_primera = StringIO()
        out_segunda = StringIO()

        with patch(
            "comercial.services_recuperacion.requests.get",
            return_value=respuesta_http(200, b"imagen-historica"),
        ) as get:
            call_command("recuperar_archivos_cloudinary", stdout=out_primera)
            call_command("recuperar_archivos_cloudinary", stdout=out_segunda)

        self.assertEqual(get.call_count, 1)
        self.assertTrue(default_storage.exists("productos/historica.jpg"))
        with default_storage.open("productos/historica.jpg", "rb") as archivo:
            self.assertEqual(archivo.read(), b"imagen-historica")
        producto.refresh_from_db()
        self.assertEqual(producto.imagen_promocional.name, "productos/historica.jpg")
        self.assertIn("Recuperados: 1", out_primera.getvalue())
        self.assertIn("Ya existentes (omitidos): 1", out_segunda.getvalue())

    def test_reintenta_resource_type_y_reporta_cada_url_no_encontrada(self):
        producto = Producto.objects.create(
            nombre="Producto perdido",
            imagen_promocional="productos/foto histórica.jpg",
        )
        out = StringIO()

        with patch(
            "comercial.services_recuperacion.requests.get",
            side_effect=[respuesta_http(404), respuesta_http(404)],
        ) as get:
            call_command("recuperar_archivos_cloudinary", "--cloud-name", "cuenta-prueba", stdout=out)

        urls = [llamada.args[0] for llamada in get.call_args_list]
        self.assertEqual(
            urls,
            [
                "https://res.cloudinary.com/cuenta-prueba/image/upload/productos/foto%20hist%C3%B3rica.jpg",
                "https://res.cloudinary.com/cuenta-prueba/raw/upload/productos/foto%20hist%C3%B3rica.jpg",
            ],
        )
        salida = out.getvalue()
        self.assertIn("No encontrados: 1", salida)
        self.assertIn(f"comercial.Producto pk={producto.pk} campo=imagen_promocional", salida)
        self.assertEqual(salida.count("HTTP 404"), 2)

    def test_inventario_contiene_los_16_campos_del_issue(self):
        self.assertEqual(len(ARCHIVOS_CLOUDINARY), 16)
        self.assertIn(("comercial", "Cotizacion", "archivo_pdf", "raw"), ARCHIVOS_CLOUDINARY)
        self.assertIn(("legal", "SolicitudARCO", "identificacion", "image"), ARCHIVOS_CLOUDINARY)


class RecuperarArchivosCloudinaryStorageTest(TestCase):
    @override_settings(
        STORAGES={
            "default": {"BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
    )
    def test_aborta_si_cloudinary_sigue_siendo_el_storage_default(self):
        with self.assertRaisesMessage(CommandError, "todavía es Cloudinary"):
            call_command("recuperar_archivos_cloudinary")


@override_settings(STORAGES=STORAGES_PRUEBA)
class RecuperarArchivosServicioTest(TestCase):
    def test_simular_no_descarga_ni_escribe_y_lista_lo_que_falta(self):
        producto = Producto.objects.create(
            nombre="Producto histórico",
            imagen_promocional="productos/historica.jpg",
        )

        with patch("comercial.services_recuperacion.requests.get") as get:
            resultado = recuperar_archivos(simular=True)

        get.assert_not_called()
        self.assertFalse(default_storage.exists("productos/historica.jpg"))
        self.assertEqual(resultado.recuperados, 0)
        self.assertEqual(
            resultado.pendientes,
            [f"comercial.Producto pk={producto.pk} campo=imagen_promocional"],
        )

    def test_el_limite_corta_la_pasada_y_la_siguiente_continua(self):
        for indice in range(3):
            Producto.objects.create(
                nombre=f"Producto {indice}",
                imagen_promocional=f"productos/historica-{indice}.jpg",
            )

        with patch(
            "comercial.services_recuperacion.requests.get",
            return_value=respuesta_http(200, b"imagen-historica"),
        ):
            primera = recuperar_archivos(limite=2)
            segunda = recuperar_archivos(limite=2)

        self.assertTrue(primera.interrumpido)
        self.assertEqual(primera.recuperados, 2)
        self.assertFalse(segunda.interrumpido)
        self.assertEqual(segunda.recuperados, 1)
        self.assertEqual(segunda.omitidos, 2)

    def test_el_presupuesto_de_tiempo_corta_antes_de_tocar_el_storage(self):
        Producto.objects.create(
            nombre="Producto histórico",
            imagen_promocional="productos/historica.jpg",
        )

        # tiempo_maximo=0 se agota en la primera vuelta: no debe consultar el
        # storage ni salir a la red.
        with patch("comercial.services_recuperacion.requests.get") as get:
            resultado = recuperar_archivos(tiempo_maximo=0)

        get.assert_not_called()
        self.assertTrue(resultado.interrumpido)
        self.assertEqual(resultado.procesados, 0)

    def test_rechaza_un_cloud_name_vacio(self):
        with self.assertRaisesMessage(RecuperacionError, "no puede estar vacío"):
            recuperar_archivos("   ")


@override_settings(STORAGES=STORAGES_PRUEBA)
class RecuperarArchivosVistaTest(TestCase):
    url = "/admin/recuperar-archivos/"

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
        Producto.objects.create(
            nombre="Producto histórico",
            imagen_promocional="productos/historica.jpg",
        )

        with patch("comercial.services_recuperacion.requests.get") as get:
            respuesta = self.client.get(self.url)

        get.assert_not_called()
        self.assertEqual(respuesta.status_code, 200)
        self.assertIsNone(respuesta.context["resultado"])

    def test_post_simula_por_defecto(self):
        self.client.force_login(self.superusuario)
        # Nombre propio: el InMemoryStorage del override_settings de clase es
        # uno solo para todos los tests, y lo que guarda uno lo ve el siguiente.
        Producto.objects.create(
            nombre="Producto histórico",
            imagen_promocional="productos/simulacion.jpg",
        )

        with patch("comercial.services_recuperacion.requests.get") as get:
            respuesta = self.client.post(self.url, {"accion": "simular"})

        get.assert_not_called()
        self.assertFalse(default_storage.exists("productos/simulacion.jpg"))
        self.assertTrue(respuesta.context["simulado"])
        self.assertEqual(len(respuesta.context["resultado"].pendientes), 1)

    def test_post_con_accion_recuperar_descarga_y_guarda(self):
        self.client.force_login(self.superusuario)
        Producto.objects.create(
            nombre="Producto histórico",
            imagen_promocional="productos/historica.jpg",
        )

        with patch(
            "comercial.services_recuperacion.requests.get",
            return_value=respuesta_http(200, b"imagen-historica"),
        ):
            respuesta = self.client.post(
                self.url, {"accion": "recuperar", "cloud_name": "cuenta-prueba"}
            )

        self.assertTrue(default_storage.exists("productos/historica.jpg"))
        self.assertFalse(respuesta.context["simulado"])
        self.assertEqual(respuesta.context["resultado"].recuperados, 1)

    def test_muestra_el_error_en_vez_de_reventar(self):
        self.client.force_login(self.superusuario)

        with override_settings(
            STORAGES={
                "default": {"BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage"},
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            }
        ):
            respuesta = self.client.post(self.url, {"accion": "recuperar"})

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("todavía es Cloudinary", respuesta.context["error"])
