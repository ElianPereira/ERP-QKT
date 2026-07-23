import base64
import hmac
import json
import logging
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .services_openpay import procesar_webhook_openpay

logger = logging.getLogger(__name__)


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
    # Hay que regresar 200 y loggear el código — Openpay lo pide para confirmar el alta.
    if payload.get('type') == 'verification_code' or 'verification_code' in payload:
        codigo = payload.get('verification_code')
        logger.info("Webhook Openpay: código de verificación recibido: %s", codigo)
        return HttpResponse(status=200)

    try:
        procesar_webhook_openpay(payload)
    except Exception:
        logger.exception("Webhook Openpay: error inesperado procesando el payload.")
        # Aun así, 200 — cualquier reintento de Openpay va a repetir el mismo error;
        # mejor loggearlo y revisarlo en OpenpayTransaccion/logs que atorar la cola de Openpay.

    return HttpResponse(status=200)
