from django.apps import AppConfig


class ComercialConfig(AppConfig):
    name = 'comercial'
    verbose_name = "Eventos"

    def ready(self):
        from . import signals  # noqa: F401
