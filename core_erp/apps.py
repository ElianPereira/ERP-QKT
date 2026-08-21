from django.apps import apps
from django.contrib.auth.apps import AuthConfig


class QktAuthConfig(AuthConfig):
    """Renombra el grupo 'Authentication and Authorization' del admin."""
    verbose_name = "Autenticación y Usuarios"

    def ready(self):
        super().ready()
        from django.contrib.auth.signals import user_logged_in, user_login_failed

        from core_erp import ratelimit

        def _fallo(sender, credentials=None, request=None, **kwargs):
            ratelimit.registrar_login_fallido(request, (credentials or {}).get('username'))

        def _exito(sender, request=None, user=None, **kwargs):
            ratelimit.limpiar_intentos_login(request, getattr(user, 'username', None))

        user_login_failed.connect(_fallo, dispatch_uid='qkt_login_fallido', weak=False)
        user_logged_in.connect(_exito, dispatch_uid='qkt_login_exitoso', weak=False)

        # Renombra la sección del admin de django_otp (2FA, orden 42/
        # SEC-AUTHN-002) para que se lea junto a "Autenticación y Usuarios"
        # en vez de su nombre autogenerado "Otp_Totp". Solo cambia el título
        # visible del grupo (Jazzmin lee app_config.verbose_name al
        # renderizar el menú) — el modelo sigue siendo TOTPDevice normal de
        # django_otp, sin proxy ni migración nueva: un proxy con
        # app_label='auth' obligaría a una migración dentro del propio
        # paquete django.contrib.auth (fuera del repo), inviable.
        apps.get_app_config('otp_totp').verbose_name = 'Autenticación (2FA)'
