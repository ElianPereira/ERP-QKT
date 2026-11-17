from django.apps import AppConfig


class VentasConfig(AppConfig):
    name = 'ventas'
    verbose_name = 'Ventas'

    def ready(self):
        from . import signals  # noqa: F401
