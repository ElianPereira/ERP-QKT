"""Proxy de TOTPDevice (django_otp) para agruparlo en el admin dentro de
'Autenticación y Usuarios' (core_erp.apps.QktAuthConfig), en vez de su
propia sección 'Otp_Totp'. El registro real en el admin (unregister del
original + register de este proxy) vive en QktAuthConfig.ready() —
apps.py no puede definir este modelo a nivel de módulo porque se importa
antes de que el app registry tenga lista la app 'auth'."""
from django_otp.plugins.otp_totp.models import TOTPDevice


class TOTPDeviceAuth(TOTPDevice):
    class Meta:
        proxy = True
        app_label = 'auth'
        verbose_name = 'Dispositivo TOTP (2FA)'
        verbose_name_plural = 'Dispositivos TOTP (2FA)'
