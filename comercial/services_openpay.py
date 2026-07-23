"""
Integración con Openpay: generación de links de pago y procesamiento del
webhook de notificaciones. El `Pago` creado aquí dispara la póliza automática
existente — este módulo no toca la lógica de contabilidad.
"""
import requests
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.db import transaction
from .models import Cotizacion, Pago, OpenpayTransaccion

OPENPAY_BASE_URL = (
    "https://sandbox-api.openpay.mx/v1"
    if settings.OPENPAY_MODE == 'sandbox'
    else "https://api.openpay.mx/v1"
)


def _auth():
    """Openpay usa HTTP Basic Auth con la llave privada como usuario, sin password."""
    return (settings.OPENPAY_PRIVATE_KEY, '')


def generar_link_pago(cotizacion: Cotizacion, monto: Decimal, concepto: str = 'VENTA'):
    """
    Genera un link de pago en Openpay para una cotización específica.

    PENDIENTE DE VERIFICAR CONTRA LA DOCUMENTACIÓN REAL: el endpoint y el
    payload exactos deben confirmarse en el Dashboard de Openpay (sección
    Documentación/API Reference, donde los ejemplos usan el merchant_id real)
    antes de dar esto por terminado. Este código es un ESQUELETO con la forma
    general (auth, manejo de respuesta, guardado de referencia), no una
    implementación verificada.
    """
    url = f"{OPENPAY_BASE_URL}/{settings.OPENPAY_MERCHANT_ID}/charges"  # CONFIRMAR endpoint real

    payload = {
        "method": "card",  # o el método correcto para checkout/hosted-link — CONFIRMAR
        "amount": float(monto),
        "currency": "MXN",
        "description": f"COT-{cotizacion.id:03d} - {cotizacion.nombre_evento}",
        "order_id": f"COT-{cotizacion.id}-{concepto}",
        "redirect_url": "https://quintakooxtanil.com/pago-confirmado/",  # ajustar a la real
    }

    response = requests.post(url, json=payload, auth=_auth(), timeout=15)
    response.raise_for_status()
    data = response.json()

    OpenpayTransaccion.objects.create(
        openpay_id=data['id'],
        event_type='charge.created',
        estado_openpay=data.get('status', ''),
        monto=monto,
        cotizacion=cotizacion,
        payload_crudo=data,
    )

    return data.get('payment_method', {}).get('url') or data.get('checkout_link')  # CONFIRMAR el campo real


def _decimal_o_none(valor):
    if valor in (None, ''):
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None


def procesar_webhook_openpay(payload: dict):
    """
    Procesa una notificación de webhook ya autenticada (la vista valida el
    Basic Auth antes de llamar a esta función).

    Idempotente: si el openpay_id ya fue procesado, no vuelve a crear el Pago.
    Nunca lanza excepción hacia afuera sin registrar el error — la vista
    siempre debe poder regresar 200 OK a Openpay, incluso si algo salió mal
    internamente (evita que Openpay reintente en bucle indefinidamente).
    """
    event_type = payload.get('type', '')
    transaction_data = payload.get('transaction', payload)  # la forma exacta puede variar, ajustar según payload real
    if not isinstance(transaction_data, dict):
        return None
    openpay_id = transaction_data.get('id')

    if not openpay_id:
        return None  # notificación sin id de transacción (ej. verification_code) — se ignora aquí

    registro, creado = OpenpayTransaccion.objects.get_or_create(
        openpay_id=openpay_id,
        defaults={
            'event_type': event_type,
            'estado_openpay': transaction_data.get('status', ''),
            'monto': _decimal_o_none(transaction_data.get('amount')),
            'payload_crudo': payload,
        }
    )

    if not creado and registro.procesado:
        return registro  # ya se procesó antes, no hacer nada (idempotencia)

    if event_type != 'charge.succeeded' or transaction_data.get('status') != 'completed':
        registro.error_detalle = f"Evento '{event_type}' con estado '{transaction_data.get('status')}' — no genera Pago."
        registro.save(update_fields=['error_detalle'])
        return registro

    order_id = transaction_data.get('order_id', '') or ''
    cotizacion = None
    if order_id.startswith('COT-'):
        try:
            cotizacion_id = int(order_id.split('-')[1])
            cotizacion = Cotizacion.objects.get(pk=cotizacion_id)
        except (ValueError, IndexError, Cotizacion.DoesNotExist):
            pass

    if not cotizacion:
        registro.error_detalle = f"No se pudo identificar la cotización desde order_id='{order_id}'."
        registro.save(update_fields=['error_detalle'])
        return registro

    monto = _decimal_o_none(transaction_data.get('amount'))
    if monto is None:
        registro.error_detalle = f"Monto inválido en la notificación: {transaction_data.get('amount')!r}."
        registro.save(update_fields=['error_detalle'])
        return registro

    try:
        with transaction.atomic():
            datos_pago = {
                'cotizacion': cotizacion,
                'tipo': 'INGRESO',
                'concepto': 'VENTA',
                'monto': monto,
                'metodo': 'PLATAFORMA',
                'referencia': openpay_id,
                'notas': 'Registrado automáticamente vía webhook de Openpay.',
            }
            fecha_pago = (transaction_data.get('creation_date') or '')[:10]
            if fecha_pago:
                datos_pago['fecha_pago'] = fecha_pago
            pago = Pago.objects.create(**datos_pago)
            registro.cotizacion = cotizacion
            registro.pago = pago
            registro.procesado = True
            registro.error_detalle = ''
            registro.save(update_fields=['cotizacion', 'pago', 'procesado', 'error_detalle'])
    except Exception as e:
        registro.error_detalle = f"Error al crear Pago: {e}"
        registro.save(update_fields=['error_detalle'])

    return registro
