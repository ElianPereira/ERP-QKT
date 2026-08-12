"""Recuperación de los archivos históricos que quedaron solo en Cloudinary.

Lo comparten el comando `recuperar_archivos_cloudinary` y la vista del admin
`/admin/recuperar-archivos/`: el inventario de campos, el criterio de "qué
falta" y la descarga viven aquí para no tenerlos por duplicado.

La operación es idempotente: lo que ya está en el storage destino se omite,
así que interrumpirla y volver a lanzarla es seguro.
"""

import time
from dataclasses import dataclass, field
from urllib.parse import quote

import requests
from django.apps import apps
from django.conf import settings
from django.core.files.base import ContentFile

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


class RecuperacionError(Exception):
    """Aborta la recuperación: el destino no sirve o el storage falló."""


@dataclass
class Resultado:
    """Contadores de una pasada. `pendientes` solo se llena al simular."""

    procesados: int = 0
    recuperados: int = 0
    omitidos: int = 0
    pendientes: list = field(default_factory=list)
    no_encontrados: list = field(default_factory=list)
    interrumpido: bool = False


def validar_storage_destino():
    backend = settings.STORAGES.get("default", {}).get("BACKEND", "")
    if "cloudinary" in backend.lower():
        raise RecuperacionError(
            "El storage default todavía es Cloudinary. Despliega primero la migración a R2 "
            "(Issue #143) y vuelve a ejecutar el comando."
        )


def construir_url(cloud_name, resource_type, name):
    cloud_name_url = quote(cloud_name, safe="")
    name_url = quote(name, safe="/")
    return f"https://res.cloudinary.com/{cloud_name_url}/{resource_type}/upload/{name_url}"


def descargar(cloud_name, name, resource_type):
    alternativo = "image" if resource_type == "raw" else "raw"
    intentos = []

    for tipo in (resource_type, alternativo):
        url = construir_url(cloud_name, tipo, name)
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


def _agotado(inicio, tiempo_maximo):
    return tiempo_maximo is not None and (time.monotonic() - inicio) >= tiempo_maximo


def recuperar_archivos(cloud_name=CLOUD_NAME_DEFAULT, *, simular=False, limite=None,
                       tiempo_maximo=None):
    """Copia al storage actual los archivos que solo existen en Cloudinary.

    `simular` consulta el storage y no descarga ni escribe nada. `limite`
    tope de descargas por pasada y `tiempo_maximo` (segundos) presupuesto de
    reloj: los necesita quien la llame desde una petición web, porque cada
    registro cuesta al menos una consulta de red al bucket y gunicorn corta
    a los 120 s. Al cortar marca `interrumpido` y basta con volver a lanzarla.
    """
    cloud_name = (cloud_name or "").strip()
    if not cloud_name:
        raise RecuperacionError("El cloud name de origen no puede estar vacío.")
    validar_storage_destino()

    resultado = Resultado()
    inicio = time.monotonic()
    descargas = 0

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

            if _agotado(inicio, tiempo_maximo) or (limite is not None and descargas >= limite):
                resultado.interrumpido = True
                return resultado

            resultado.procesados += 1
            referencia = f"{app_label}.{model_name} pk={registro.pk} campo={field_name}"
            try:
                ya_existe = archivo.storage.exists(archivo.name)
            except Exception as exc:
                raise RecuperacionError(
                    f"No se pudo consultar el storage destino para {referencia}: {exc}"
                ) from exc

            if ya_existe:
                resultado.omitidos += 1
                continue

            if simular:
                resultado.pendientes.append(referencia)
                continue

            descargas += 1
            contenido, intentos = descargar(cloud_name, archivo.name, resource_type)
            if contenido is None:
                resultado.no_encontrados.append((referencia, intentos))
                continue

            try:
                nombre_guardado = archivo.storage.save(archivo.name, ContentFile(contenido))
            except Exception as exc:
                raise RecuperacionError(
                    f"No se pudo guardar en el storage destino {referencia}: {exc}"
                ) from exc
            if nombre_guardado != archivo.name:
                raise RecuperacionError(
                    f"El storage cambió el nombre de {referencia}: "
                    f"esperado={archivo.name!r}, guardado={nombre_guardado!r}."
                )
            resultado.recuperados += 1

    return resultado
