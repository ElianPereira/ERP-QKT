"""
Utilidades compartidas para tests (no es un módulo de tests: el nombre no
empieza con `test_` a propósito, para que `manage.py test` no lo descubra).
"""
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice


def login_superuser_con_totp(client, usuario):
    """Autentica `usuario` (debe ser superusuario) con la sesión ya
    verificada por TOTP (SEC-AUTHN-002, orden 42) — evita repetir el flujo
    real de escaneo de QR en cada test que no está probando el login en sí,
    solo necesita pasarlo para llegar a la vista que sí le interesa."""
    dispositivo, creado = TOTPDevice.objects.get_or_create(
        user=usuario, name='default', defaults={'confirmed': True},
    )
    if not creado and not dispositivo.confirmed:
        dispositivo.confirmed = True
        dispositivo.save(update_fields=['confirmed'])

    client.force_login(usuario)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = dispositivo.persistent_id
    session.save()
