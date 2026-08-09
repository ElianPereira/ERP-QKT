from io import BytesIO, StringIO
from unittest.mock import patch

import requests
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from comercial.management.commands.recuperar_archivos_cloudinary import ARCHIVOS_CLOUDINARY
from comercial.models import Producto

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

        with patch("comercial.management.commands.recuperar_archivos_cloudinary.requests.get") as get:
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
            "comercial.management.commands.recuperar_archivos_cloudinary.requests.get",
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
            "comercial.management.commands.recuperar_archivos_cloudinary.requests.get",
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
