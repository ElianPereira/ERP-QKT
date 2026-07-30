import base64
import hmac
import json
import logging
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from core_erp.ratelimit import rate_limit
from .models import PortalCliente
from .services_openpay import (
    procesar_cargo_tarjeta, procesar_cargo_efectivo, procesar_cargo_spei,
    procesar_webhook_openpay, consultar_y_confirmar_cargo,
)

logger = logging.getLogger(__name__)

# Candado corto para que dos envíos casi simultáneos del mismo checkout (doble
# clic, doble pestaña, red lenta con reintento) no generen dos cargos en
# Openpay. El botón ya se deshabilita en el JS al enviar, pero eso no cubre
# dos pestañas ni una petición que ya salió antes de que el JS reaccionara.
CANDADO_PAGO_SEGUNDOS = 12


# ==========================================
# CHECKOUT DEL PORTAL DE CLIENTE
# ==========================================
# Usa la misma autenticación que el resto del portal: el token de
# PortalCliente en la URL (/mi-evento/<token>/...), que el cliente obtuvo
# con su código de cotización + últimos 4 dígitos de su teléfono.

@rate_limit(key='portal_pago_openpay', limit=10, window=60)
@require_POST
def portal_procesar_pago_openpay(request, token):
    portal = get_object_or_404(PortalCliente, token=token, activo=True)
    cotizacion = portal.cotizacion

    metodo = request.POST.get('metodo')
    try:
        monto = Decimal(request.POST.get('monto', '0'))
    except InvalidOperation:
        return JsonResponse({'ok': False, 'mensaje': 'Monto inválido.'})

    if monto <= 0:
        return JsonResponse({'ok': False, 'mensaje': 'Monto inválido.'})

    saldo = cotizacion.saldo_pendiente()
    if monto > saldo + Decimal('0.50'):
        return JsonResponse({'ok': False, 'mensaje': f'El monto excede el saldo pendiente (${saldo:,.2f}).'})

    minimo = cotizacion.monto_minimo_pago()
    if monto < minimo - Decimal('0.50'):
        return JsonResponse({'ok': False, 'mensaje': f'El monto mínimo para este pago es ${minimo:,.2f}.'})

    candado = f'pago_openpay_en_curso:{cotizacion.id}'
    if not cache.add(candado, '1', timeout=CANDADO_PAGO_SEGUNDOS):
        return JsonResponse({
            'ok': False,
            'candado': True,
            'mensaje': 'Ya estamos procesando un pago para esta cotización. '
                       'Espera unos segundos — no es necesario que vuelvas a intentarlo.',
        })

    try:
        if metodo == 'card':
            token_id = request.POST.get('token_id', '')
            device_session_id = request.POST.get('device_session_id', '')
            if not token_id:
                return JsonResponse({'ok': False, 'mensaje': 'No se recibió el token de la tarjeta. Intenta de nuevo.'})
            # URL absoluta a la que Openpay regresa al cliente después de que
            # se autentique con su banco emisor (3D Secure).
            redirect_url = request.build_absolute_uri(
                reverse('portal_retorno_3ds', args=[token])
            )
            resultado = procesar_cargo_tarjeta(
                cotizacion, monto, token_id, device_session_id, redirect_url=redirect_url,
            )
        elif metodo == 'store':
            resultado = procesar_cargo_efectivo(cotizacion, monto)
        elif metodo == 'bank_account':
            resultado = procesar_cargo_spei(cotizacion, monto)
        else:
            resultado = {'ok': False, 'mensaje': 'Método de pago no reconocido.'}
    except Exception:
        logger.exception("Checkout Openpay: error inesperado procesando cargo (COT-%s, método %s).", cotizacion.id, metodo)
        resultado = {'ok': False, 'mensaje': 'Ocurrió un error al procesar el pago. Intenta de nuevo o contáctanos.'}
    finally:
        cache.delete(candado)

    return JsonResponse(resultado)


@rate_limit(key='portal_retorno_3ds', limit=20, window=60)
def portal_retorno_3ds(request, token):
    """
    Openpay regresa aquí al cliente después de la autenticación 3D Secure.

    Hasta este punto el cargo estaba en 'charge_pending': solo consultándolo
    se sabe si el banco emisor lo autorizó. Es un GET disparado por la
    redirección del banco, así que no lleva CSRF ni se puede exigir POST.
    """
    portal = get_object_or_404(PortalCliente, token=token, activo=True)
    cotizacion = portal.cotizacion

    # Openpay manda el id del cargo de vuelta; el nombre del parámetro puede
    # variar, así que se aceptan las variantes documentadas.
    openpay_id = (
        request.GET.get('id')
        or request.GET.get('transaction_id')
        or request.GET.get('charge_id')
        or ''
    ).strip()

    destino = reverse('portal_evento', args=[token])
    if not openpay_id:
        logger.warning(
            "Retorno 3DS sin id de cargo (COT-%s). Query: %s",
            cotizacion.id, dict(request.GET),
        )
        return redirect(f"{destino}?pago=error")

    try:
        resultado = consultar_y_confirmar_cargo(cotizacion, openpay_id)
    except Exception:
        logger.exception(
            "Retorno 3DS: error inesperado confirmando el cargo %s (COT-%s).",
            openpay_id, cotizacion.id,
        )
        return redirect(f"{destino}?pago=error")

    if resultado.get('ok'):
        return redirect(f"{destino}?pago=exitoso")
    if resultado.get('pendiente'):
        return redirect(f"{destino}?pago=pendiente")
    return redirect(f"{destino}?pago=rechazado")


# ==========================================
# WEBHOOK
# ==========================================


def _validar_basic_auth(request):
    """
    Openpay solo soporta HTTP Basic Auth en sus webhooks. El usuario/password
    se configuran en el Dashboard de Openpay al registrar el webhook, y deben
    coincidir con OPENPAY_WEBHOOK_USER / OPENPAY_WEBHOOK_PASSWORD en Railway.
    """
    if not settings.OPENPAY_WEBHOOK_USER or not settings.OPENPAY_WEBHOOK_PASSWORD:
        logger.error("Webhook Openpay: OPENPAY_WEBHOOK_USER/PASSWORD sin configurar — se rechaza toda petición.")
        return False
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header.startswith('Basic '):
        return False
    try:
        decoded = base64.b64decode(auth_header[6:]).decode('utf-8')
        user, password = decoded.split(':', 1)
    except Exception:
        return False
    return (
        hmac.compare_digest(user, settings.OPENPAY_WEBHOOK_USER)
        and hmac.compare_digest(password, settings.OPENPAY_WEBHOOK_PASSWORD)
    )


@csrf_exempt
@require_POST
def openpay_webhook_view(request):
    if not _validar_basic_auth(request):
        logger.warning("Webhook Openpay: intento con credenciales inválidas.")
        return HttpResponse(status=401)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.error("Webhook Openpay: payload no es JSON válido.")
        return HttpResponse(status=200)  # 200 igual, para que Openpay no reintente algo irrecuperable

    if not isinstance(payload, dict):
        logger.error("Webhook Openpay: payload JSON no es un objeto: %r", payload)
        return HttpResponse(status=200)

    # Openpay manda un evento especial de verificación al registrar el webhook.
    # Forma real confirmada contra producción (difiere de la documentación
    # genérica): {'type': 'VERIFICATION', 'verificationCode': 'XXXXXXXX'}.
    # Se aceptan también las variantes en snake_case por si cambia de versión.
    codigo = payload.get('verificationCode') or payload.get('verification_code')
    if str(payload.get('type', '')).upper() == 'VERIFICATION' or codigo:
        # warning para que el código siempre sea visible en los Deploy Logs
        logger.warning("Webhook Openpay: código de verificación recibido: %s", codigo)
        return HttpResponse(status=200)

    try:
        procesar_webhook_openpay(payload)
    except Exception:
        logger.exception("Webhook Openpay: error inesperado procesando el payload.")
        # Aun así, 200 — cualquier reintento de Openpay va a repetir el mismo error;
        # mejor loggearlo y revisarlo en OpenpayTransaccion/logs que atorar la cola de Openpay.

    return HttpResponse(status=200)
