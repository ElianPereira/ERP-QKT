from urllib.parse import quote

import requests
from django.apps import apps
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

CLOUD_NAME_DEFAULT = "dtb83lcvv"
TIMEOUT_HTTP = (10, 60)

# (app, modelo, campo, resource_type original esperado)
ARCHIVOS_CLOUDINARY = (
    ("comercial", "Cotizacion", "archivo_pdf", "raw"),
    ("comercial", "Cotizacion", "archivo_contrato", "raw"),
    ("comercial", "ContratoServicio", "archivo", "raw"),
    ("comercial", "Compra", "archivo_xml", "raw"),
    ("comercial", "Compra", "archivo_pdf", "raw"),
    ("comercial", "Gasto", "archivo_xml", "raw"),
    ("comercial", "Gasto", "archivo_pdf", "raw"),
    ("facturacion", "SolicitudFactura", "archivo_zip", "raw"),
    ("facturacion", "SolicitudFactura", "archivo_pdf", "raw"),
    ("facturacion", "SolicitudFactura", "archivo_xml", "raw"),
    ("comercial", "Producto", "imagen_promocional", "image"),
    ("comercial", "ImagenLanding", "imagen", "image"),
    ("comercial", "EspacioLanding", "imagen", "image"),
    ("nomina", "ReciboNomina", "archivo_pdf", "image"),
    ("contabilidad", "EstadoCuentaBancario", "archivo", "image"),
    ("legal", "SolicitudARCO", "identificacion", "image"),
)


class Command(BaseCommand):
    help = "Recupera archivos históricos desde las URLs públicas de Cloudinary al storage actual."

    def add_arguments(self, parser):
        parser.add_argument(
            "--cloud-name",
            default=CLOUD_NAME_DEFAULT,
            help=f"Cloud name de origen (default: {CLOUD_NAME_DEFAULT}).",
        )

    def handle(self, *args, **options):
        cloud_name = options["cloud_name"].strip()
        if not cloud_name:
            raise CommandError("--cloud-name no puede estar vacío.")
        self._validar_storage_destino()

        procesados = 0
        recuperados = 0
        omitidos = 0
        no_encontrados = []

        for app_label, model_name, field_name, resource_type in ARCHIVOS_CLOUDINARY:
            model = apps.get_model(app_label, model_name)
            registros = (
                model.objects.filter(**{f"{field_name}__isnull": False})
                .exclude(**{field_name: ""})
                .only("pk", field_name)
                .order_by("pk")
                .iterator()
            )

            for registro in registros:
                archivo = getattr(registro, field_name)
                if not archivo or not archivo.name:
                    continue

                procesados += 1
                referencia = f"{app_label}.{model_name} pk={registro.pk} campo={field_name}"
                try:
                    ya_existe = archivo.storage.exists(archivo.name)
                except Exception as exc:
                    raise CommandError(
                        f"No se pudo consultar el storage destino para {referencia}: {exc}"
                    ) from exc

                if ya_existe:
                    omitidos += 1
                    continue

                contenido, intentos = self._descargar(cloud_name, archivo.name, resource_type)
                if contenido is None:
                    no_encontrados.append((referencia, intentos))
                    continue

                try:
                    nombre_guardado = archivo.storage.save(archivo.name, ContentFile(contenido))
                except Exception as exc:
                    raise CommandError(
                        f"No se pudo guardar en el storage destino {referencia}: {exc}"
                    ) from exc
                if nombre_guardado != archivo.name:
                    raise CommandError(
                        f"El storage cambió el nombre de {referencia}: "
                        f"esperado={archivo.name!r}, guardado={nombre_guardado!r}."
                    )
                recuperados += 1

        self.stdout.write("")
        self.stdout.write("Resumen de recuperación:")
        self.stdout.write(f"  Total procesados: {procesados}")
        self.stdout.write(self.style.SUCCESS(f"  Recuperados: {recuperados}"))
        self.stdout.write(f"  Ya existentes (omitidos): {omitidos}")
        self.stdout.write(self.style.WARNING(f"  No encontrados: {len(no_encontrados)}"))

        for referencia, intentos in no_encontrados:
            self.stdout.write(self.style.WARNING(f"  - {referencia}"))
            for url, resultado in intentos:
                self.stdout.write(f"      {url} -> {resultado}")

    @staticmethod
    def _validar_storage_destino():
        backend = settings.STORAGES.get("default", {}).get("BACKEND", "")
        if "cloudinary" in backend.lower():
            raise CommandError(
                "El storage default todavía es Cloudinary. Despliega primero la migración a R2 "
                "(Issue #143) y vuelve a ejecutar el comando."
            )

    @staticmethod
    def _construir_url(cloud_name, resource_type, name):
        cloud_name_url = quote(cloud_name, safe="")
        name_url = quote(name, safe="/")
        return f"https://res.cloudinary.com/{cloud_name_url}/{resource_type}/upload/{name_url}"

    def _descargar(self, cloud_name, name, resource_type):
        alternativo = "image" if resource_type == "raw" else "raw"
        intentos = []

        for tipo in (resource_type, alternativo):
            url = self._construir_url(cloud_name, tipo, name)
            try:
                respuesta = requests.get(url, timeout=TIMEOUT_HTTP)
            except requests.RequestException as exc:
                intentos.append((url, f"error de red: {exc}"))
                continue

            try:
                if respuesta.status_code == requests.codes.ok:
                    return respuesta.content, intentos + [(url, "HTTP 200")]
                intentos.append((url, f"HTTP {respuesta.status_code}"))
            finally:
                respuesta.close()

        return None, intentos
