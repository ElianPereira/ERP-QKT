"""Copia al bucket privado los documentos sensibles que viven en el público.

Lo comparten el comando `migrar_archivos_privados` y la vista del admin
`/admin/migrar-archivos-privados/`. Cierra `SEC-FILE-001a`: en R2 el acceso
público es por bucket, no por objeto, así que la única forma de que las fotos
de la landing sigan siendo públicas y los contratos no es separarlos en dos
buckets.

**Copia, no mueve**: no borra nada del origen, porque si algo sale mal el
archivo debe seguir estando en algún sitio. Borrar del bucket público es un
paso manual posterior, y solo tras verificar que las descargas funcionan.

Conserva el `name` exacto de cada archivo: la BD guarda esa ruta y no se toca,
así que copiar con el mismo nombre es lo que hace el cambio transparente.
"""

import time
from dataclasses import dataclass, field

from django.apps import apps
from django.core.files.base import ContentFile
from django.core.files.storage import storages

from core_erp.storages_qkt import _hay_bucket_privado

# Los mismos campos a los que models.py les puso storage=storage_privado.
CAMPOS_PRIVADOS = (
    ('legal', 'SolicitudARCO', 'identificacion'),
    ('nomina', 'ReciboNomina', 'archivo_pdf'),
    ('contabilidad', 'EstadoCuentaBancario', 'archivo'),
    ('comercial', 'ContratoServicio', 'archivo'),
)


class MigracionError(Exception):
    """Aborta la migración: falta configuración para siquiera empezar."""


@dataclass
class ResultadoMigracion:
    copiados: int = 0
    ya_estaban: int = 0
    sin_origen: list = field(default_factory=list)
    fallos: list = field(default_factory=list)
    interrumpido: bool = False


def _agotado(inicio, tiempo_maximo):
    return tiempo_maximo is not None and (time.monotonic() - inicio) >= tiempo_maximo


def migrar_archivos_privados(*, aplicar=False, limite=None, tiempo_maximo=None):
    """Copia del bucket público al privado los cuatro campos sensibles.

    Sin `aplicar` solo informa de qué haría. `limite` (copias por pasada) y
    `tiempo_maximo` (segundos) los necesita quien la llame desde una petición
    web: cada archivo son dos consultas al bucket y, al copiar, una descarga
    más una subida; gunicorn corta a los 120 s. Al cortar marca `interrumpido`
    y basta con volver a lanzarla, porque lo ya copiado se detecta y se omite.
    """
    if not _hay_bucket_privado():
        raise MigracionError(
            'No hay bucket privado configurado. Define '
            'CLOUDFLARE_R2_PRIVATE_BUCKET_NAME (y las credenciales si son '
            'distintas) antes de correr esto.'
        )

    resultado = ResultadoMigracion()
    origen = storages['default']
    destino = storages['privado']
    inicio = time.monotonic()
    copias = 0

    for app_label, model_name, campo in CAMPOS_PRIVADOS:
        modelo = apps.get_model(app_label, model_name)
        registros = (
            modelo.objects
            .exclude(**{campo: ''})
            .exclude(**{f'{campo}__isnull': True})
            .only('pk', campo)
            .order_by('pk')
            .iterator()
        )

        for registro in registros:
            archivo = getattr(registro, campo)
            if not archivo or not archivo.name:
                continue

            if _agotado(inicio, tiempo_maximo) or (limite is not None and copias >= limite):
                resultado.interrumpido = True
                return resultado

            referencia = f'{app_label}.{model_name} pk={registro.pk} {campo}={archivo.name}'

            try:
                if destino.exists(archivo.name):
                    resultado.ya_estaban += 1
                    continue
                if not origen.exists(archivo.name):
                    # Típico de los registros heredados de Cloudinary que nunca
                    # se migraron; los rescata otro comando.
                    resultado.sin_origen.append(referencia)
                    continue
            except Exception as exc:
                resultado.fallos.append((referencia, f'consulta al storage: {exc}'))
                continue

            if not aplicar:
                resultado.copiados += 1
                continue

            copias += 1
            try:
                with origen.open(archivo.name, 'rb') as f:
                    contenido = f.read()
                guardado = destino.save(archivo.name, ContentFile(contenido))
            except Exception as exc:
                resultado.fallos.append((referencia, str(exc)))
                continue

            if guardado != archivo.name:
                # Si el destino renombró, la BD apuntaría a una ruta que no
                # existe: es peor que no haber copiado. Se avisa y se limpia la
                # copia con nombre equivocado.
                resultado.fallos.append((
                    referencia,
                    f'el destino renombró a {guardado!r}; se descarta la copia',
                ))
                try:
                    destino.delete(guardado)
                except Exception:
                    pass
                continue

            resultado.copiados += 1

    return resultado
