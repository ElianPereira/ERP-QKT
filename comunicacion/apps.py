from django.apps import AppConfig


class ComunicacionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'comunicacion'
    verbose_name = 'Comunicación con clientes'

    def ready(self):
        from . import signals  # noqa: F401
