from django.contrib.auth.apps import AuthConfig


class QktAuthConfig(AuthConfig):
    """Renombra el grupo 'Authentication and Authorization' del admin."""
    verbose_name = "Autenticación y Usuarios"
