"""
Storage privado para documentos sensibles.

El storage `default` apunta a un bucket de R2 con `querystring_auth: False` y
dominio público: sirve lectura anónima, que es lo que necesita la landing para
mostrar sus imágenes. Pero ahí conviven identificaciones de solicitudes ARCO,
recibos de nómina, contratos y estados de cuenta, y para esos la URL acaba
siendo el único control de acceso (SEC-FILE-001).

La separación correcta es por bucket: uno público para las imágenes del sitio y
uno privado para los documentos. Este módulo resuelve cuál usar.

**Es inerte mientras no se configure el bucket privado.** Si
CLOUDFLARE_R2_PRIVATE_BUCKET_NAME está vacío, `storage_privado()` devuelve el
storage por defecto y todo se comporta igual que antes — así el código se puede
desplegar sin coordinar con el cambio de infraestructura, y la activación es
solo cuestión de definir variables en Railway.

Se expone como *callable* a propósito: `FileField(storage=...)` acepta una
función y así la migración no congela la configuración del storage, que es la
recomendación de Django para storages que dependen del entorno.

El callable devuelve un `LazyObject` en vez del storage ya resuelto porque
Django evalúa `storage=` **una sola vez**, al construir el campo. Con el storage
resuelto ahí, `override_settings(STORAGES=...)` dejaría de tener efecto sobre
estos campos y cualquier test que sustituya el storage —como los de `legal`—
se rompería sin motivo aparente. Es el mismo patrón que Django usa para
`default_storage`, señal `setting_changed` incluida.

Orden de activación (ver docs/security/BACKLOG_SEGURIDAD.md, orden 7):
  1. Crear el bucket privado en Cloudflare R2, **sin** dominio público.
  2. Definir las variables CLOUDFLARE_R2_PRIVATE_* en Railway.
  3. `manage.py migrar_archivos_privados --aplicar` para copiar lo que ya existe.
  4. Verificar que las descargas funcionan.
  5. Borrar del bucket público solo entonces.
"""
from django.core.files.storage import storages
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.functional import LazyObject, empty


def _hay_bucket_privado():
    return bool(storages.backends.get('privado', {}).get('OPTIONS', {}).get('bucket_name'))


class _StoragePrivado(LazyObject):
    """Resuelve el backend en el primer uso, no al importar el modelo."""

    def _setup(self):
        self._wrapped = storages['privado'] if _hay_bucket_privado() else storages['default']


_storage = _StoragePrivado()


def storage_privado():
    """Storage de los documentos sensibles.

    Devuelve el bucket privado si está configurado; si no, el default, para
    que el despliegue del código no dependa del cambio de infraestructura.
    """
    return _storage


@receiver(setting_changed)
def _reiniciar_storage_privado(*, setting, **kwargs):
    """Sin esto, override_settings(STORAGES=...) no afectaría a estos campos."""
    if setting == 'STORAGES':
        _storage._wrapped = empty
