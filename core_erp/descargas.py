"""
Descarga de archivos sensibles a través del ERP, no del storage.

El bucket de R2 sirve lectura anónima (`querystring_auth: False` +
`custom_domain` en settings.py), así que cualquier `archivo.url` que el ERP
publique es una URL permanente y sin credencial: quien la tenga —por historial,
por un correo reenviado, por la cabecera Referer— entra al documento sin pasar
por ninguna comprobación y sin dejar rastro.

Estas vistas cierran esa vía por el lado del ERP: sirven el contenido con
`FileResponse` en vez de revelar la ruta del storage, exigen sesión y el
permiso `view` del modelo correspondiente, y marcan la respuesta como no
cacheable. Es el mismo patrón que `legal/views.py::descargar_identificacion_arco`
ya usaba para las identificaciones ARCO.

**Esto no sustituye a poner el bucket en privado** (SEC-FILE-001a en el backlog):
las URLs que ya circulan seguirán funcionando mientras el bucket permita lectura
anónima. Lo que se consigue aquí es dejar de emitir URLs nuevas y tener control
de acceso sobre las descargas que pasan por el ERP.

Para añadir un modelo hay que registrarlo en ARCHIVOS_PROTEGIDOS: la lista
blanca es deliberada, sin ella la vista sería un lector arbitrario de cualquier
campo de cualquier modelo.
"""
import mimetypes
from pathlib import Path

from django.contrib.admin.views.decorators import staff_member_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse

# (app_label, model_name, campo) en minúsculas. Solo lo registrado aquí se
# puede descargar por esta vía.
ARCHIVOS_PROTEGIDOS = {
    ('comercial', 'contratoservicio', 'archivo'),
    ('comercial', 'cotizacion', 'archivo_contrato'),
    ('comercial', 'cotizacion', 'archivo_pdf'),
    ('comercial', 'compra', 'archivo_pdf'),
    ('comercial', 'compra', 'archivo_xml'),
    ('comercial', 'gasto', 'archivo_pdf'),
    ('comercial', 'gasto', 'archivo_xml'),
    ('nomina', 'recibonomina', 'archivo_pdf'),
    ('facturacion', 'solicitudfactura', 'archivo_pdf'),
    ('facturacion', 'solicitudfactura', 'archivo_xml'),
    ('facturacion', 'solicitudfactura', 'archivo_zip'),
    ('contabilidad', 'estadocuentabancario', 'archivo'),
}


def url_descarga(obj, campo):
    """URL de descarga protegida para un FileField ya registrado.

    Devuelve None si el campo está vacío, para que quien llame decida qué
    pintar. Lanza ValueError si el modelo no está en la lista blanca: es un
    error de programación, no una condición de ejecución.
    """
    meta = obj._meta
    clave = (meta.app_label, meta.model_name, campo)
    if clave not in ARCHIVOS_PROTEGIDOS:
        raise ValueError(
            f'{meta.app_label}.{meta.model_name}.{campo} no está en '
            'ARCHIVOS_PROTEGIDOS (core_erp/descargas.py).'
        )
    if not getattr(obj, campo, None):
        return None
    return reverse('descargar_archivo_privado', args=[meta.app_label, meta.model_name, campo, obj.pk])


@staff_member_required
def descargar_archivo_privado(request, app_label, model_name, campo, pk):
    """Sirve un archivo registrado, sin revelar la URL del storage."""
    from django.apps import apps

    clave = (app_label.lower(), model_name.lower(), campo.lower())
    if clave not in ARCHIVOS_PROTEGIDOS:
        raise Http404

    try:
        modelo = apps.get_model(app_label, model_name)
    except LookupError:
        raise Http404 from None

    # El permiso de lectura del modelo, no solo is_staff: si mañana se separan
    # los grupos por área (SEC-AUTHZ-001), esta vista queda alineada sola.
    if not request.user.has_perm(f'{app_label}.view_{model_name}'):
        raise Http404

    objeto = get_object_or_404(modelo, pk=pk)
    archivo = getattr(objeto, campo, None)
    if not archivo:
        raise Http404

    try:
        contenido = archivo.open('rb')
    except (FileNotFoundError, OSError):
        # El archivo está en la BD pero no en el storage: pasa con los
        # registros heredados de Cloudinary que nunca se migraron
        # (ver manage.py recuperar_archivos_cloudinary).
        raise Http404 from None

    nombre = Path(archivo.name).name
    content_type, _ = mimetypes.guess_type(nombre)
    respuesta = FileResponse(
        contenido,
        as_attachment=False,
        filename=nombre,
        content_type=content_type or 'application/octet-stream',
    )
    respuesta['Cache-Control'] = 'private, no-store'
    return respuesta
