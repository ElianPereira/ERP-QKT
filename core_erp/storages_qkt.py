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
CLOUDFLARE_R2_PRIVADO_BUCKET está vacío, `storage_privado()` devuelve el
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
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import Storage, storages
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.functional import LazyObject, empty


def _hay_bucket_privado():
    return bool(storages.backends.get('privado', {}).get('OPTIONS', {}).get('bucket_name'))


class _StoragePrivado(LazyObject):
    """Resuelve el backend en el primer uso, no al importar el modelo."""

    def _setup(self):
        if not _hay_bucket_privado():
            # Fail-open por diseño mientras el bucket no exista (ver docstring);
            # con ERP_R2_PRIVADO_OBLIGATORIO el servicio se niega a arrancar en vez
            # de guardar INE/nómina/estados de cuenta en el bucket público.
            if getattr(settings, 'ERP_R2_PRIVADO_OBLIGATORIO', False):
                raise ImproperlyConfigured(
                    'ERP_R2_PRIVADO_OBLIGATORIO está activo pero falta '
                    'CLOUDFLARE_R2_PRIVADO_BUCKET: los documentos sensibles '
                    'caerían en el bucket público.'
                )
            self._wrapped = storages['default']
            return
        self._wrapped = storages['privado']


_storage = _StoragePrivado()


def storage_privado():
    """Storage de los documentos sensibles.

    Devuelve el bucket privado si está configurado; si no, el default, para
    que el despliegue del código no dependa del cambio de infraestructura.
    """
    return _storage


class _StorageConRespaldo(Storage):
    """Escribe en el privado; lee del privado y, si no está, del público.

    Transición de los archivos que hoy viven solo en el bucket público
    (contratos, cotizaciones, CFDI, comprobantes): al cambiar `storage=` de esos
    campos, los registros existentes siguen abriendo mientras
    `migrar_archivos_privados` los copia. Se retira cuando el comando reporte 0
    pendientes. `exists` mira ambos para que `get_available_name` nunca reutilice
    un nombre que ya ocupa el público.
    """

    @property
    def _privado(self):
        return storages['privado'] if _hay_bucket_privado() else storages['default']

    @property
    def _respaldo(self):
        return storages['default']

    def _donde_esta(self, name):
        privado = self._privado
        if privado.exists(name):
            return privado
        return self._respaldo if self._respaldo.exists(name) else privado

    def _open(self, name, mode='rb'):
        if 'w' in mode or 'a' in mode or '+' in mode:
            return self._privado.open(name, mode)
        return self._donde_esta(name).open(name, mode)

    def _save(self, name, content):
        return self._privado.save(name, content)

    def exists(self, name):
        return self._privado.exists(name) or self._respaldo.exists(name)

    def size(self, name):
        return self._donde_esta(name).size(name)

    def url(self, name):
        return self._donde_esta(name).url(name)

    def delete(self, name):
        self._privado.delete(name)

    def get_modified_time(self, name):
        return self._donde_esta(name).get_modified_time(name)


class _StorageConRespaldoLazy(LazyObject):
    def _setup(self):
        self._wrapped = _StorageConRespaldo()


_storage_con_respaldo = _StorageConRespaldoLazy()


def storage_privado_con_respaldo():
    """Storage privado con lectura de respaldo en el público (transitorio)."""
    return _storage_con_respaldo


@receiver(setting_changed)
def _reiniciar_storage_privado(*, setting, **kwargs):
    """Sin esto, override_settings(STORAGES=...) no afectaría a estos campos."""
    if setting in ('STORAGES', 'ERP_R2_PRIVADO_OBLIGATORIO'):
        _storage._wrapped = empty
