from django.contrib import admin
from django.contrib.auth.apps import AuthConfig


class QktAuthConfig(AuthConfig):
    """Renombra el grupo 'Authentication and Authorization' del admin."""
    verbose_name = "Autenticación y Usuarios"

    def ready(self):
        super().ready()
        from django.contrib.auth.signals import user_logged_in, user_login_failed
        from django_otp.plugins.otp_totp.admin import TOTPDeviceAdmin
        from django_otp.plugins.otp_totp.models import TOTPDevice

        from core_erp import ratelimit
        from core_erp.models_totp import TOTPDeviceAuth

        def _fallo(sender, credentials=None, request=None, **kwargs):
            ratelimit.registrar_login_fallido(request, (credentials or {}).get('username'))

        def _exito(sender, request=None, user=None, **kwargs):
            ratelimit.limpiar_intentos_login(request, getattr(user, 'username', None))

        user_login_failed.connect(_fallo, dispatch_uid='qkt_login_fallido', weak=False)
        user_logged_in.connect(_exito, dispatch_uid='qkt_login_exitoso', weak=False)

        # Mueve el admin de TOTPDevice (2FA, orden 42/SEC-AUTHN-002) al grupo
        # "Autenticación y Usuarios", junto a Usuarios y Grupos, en vez de su
        # propia sección "Otp_Totp" — es la misma pantalla que registra
        # django_otp por default, solo bajo un app_label distinto.
        admin.site.unregister(TOTPDevice)
        admin.site.register(TOTPDeviceAuth, TOTPDeviceAdmin)
