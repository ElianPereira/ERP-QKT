"""
Orden 42 del backlog de seguridad (`SEC-AUTHN-002`): TOTP obligatorio para
superusuarios. `SuperuserTOTPGateMiddleware` (core_erp/middleware.py)
redirige aquí a cualquier superusuario autenticado que no haya verificado un
segundo factor en la sesión actual — a `totp_activar_view` si no tiene
ningún dispositivo confirmado, a `totp_verificar_view` si ya lo tiene.

Ejecutar: python manage.py test core_erp.test_totp --verbosity=2
"""
import base64
from io import BytesIO

import django_otp
import qrcode
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import redirect, render
from django_otp.plugins.otp_totp.models import TOTPDevice

from core_erp.ratelimit import rate_limit

_es_superusuario = user_passes_test(lambda u: u.is_superuser, login_url='admin_login_limitado')


def _qr_data_uri(config_url):
    imagen = qrcode.make(config_url)
    buffer = BytesIO()
    imagen.save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


@login_required(login_url='admin_login_limitado')
@_es_superusuario
@rate_limit('totp_activar', limit=10, window=900)
def totp_activar_view(request):
    """Alta de un dispositivo TOTP nuevo. Si el superusuario ya tiene uno
    confirmado, no hay nada que activar — se manda a verificar con ese."""
    if any(django_otp.devices_for_user(request.user)):
        return redirect('totp_verificar')

    dispositivo, _creado = TOTPDevice.objects.get_or_create(
        user=request.user, confirmed=False, defaults={'name': 'default'},
    )

    if request.method == 'POST':
        codigo = request.POST.get('codigo', '').strip()
        if dispositivo.verify_token(codigo):
            dispositivo.confirmed = True
            dispositivo.save(update_fields=['confirmed'])
            django_otp.login(request, dispositivo)
            messages.success(request, 'Autenticación en dos pasos activada correctamente.')
            return redirect('admin_dashboard')
        messages.error(request, 'Código incorrecto. Verifica la hora de tu dispositivo e inténtalo de nuevo.')

    return render(request, 'admin/totp_activar.html', {
        'qr_data_uri': _qr_data_uri(dispositivo.config_url),
        'secreto_manual': dispositivo.key,
    })


@login_required(login_url='admin_login_limitado')
@_es_superusuario
@rate_limit('totp_verificar', limit=10, window=900)
def totp_verificar_view(request):
    """Segundo paso del login para un superusuario que ya tiene un
    dispositivo TOTP confirmado de una sesión anterior."""
    if not any(django_otp.devices_for_user(request.user)):
        return redirect('totp_activar')

    if request.method == 'POST':
        codigo = request.POST.get('codigo', '').strip()
        dispositivo = django_otp.match_token(request.user, codigo)
        if dispositivo:
            django_otp.login(request, dispositivo)
            return redirect('admin_dashboard')
        messages.error(request, 'Código incorrecto.')

    return render(request, 'admin/totp_verificar.html')
