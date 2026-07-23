"""
Integración con Openpay: checkout propio (tarjeta + efectivo + SPEI) y
procesamiento del webhook de notificaciones.

- Tarjeta: Openpay.js tokeniza en el navegador (la tarjeta nunca toca el
  servidor); aquí solo llega el token y se crea el cargo síncrono.
- Efectivo (store) y SPEI (bank_account): el cargo se crea con estado
  'in_progress', al cliente se le muestra la referencia/CLABE, y el webhook
  confirma cuando el dinero realmente llegó.

El `Pago` creado aquí dispara la póliza automática existente — este módulo
no toca la lógica de contabilidad.
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


def _charges_url():
    return f"{OPENPAY_BASE_URL}/{settings.OPENPAY_MERCHANT_ID}/charges"


def _decimal_o_none(valor):
    if valor in (None, ''):
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None


def _payload_cargo_base(cotizacion: Cotizacion, monto: Decimal, metodo: str):
    return {
        "method": metodo,
        "amount": float(monto),
        "currency": "MXN",
        "description": f"COT-{cotizacion.id:03d} - {cotizacion.nombre_evento}",
        "order_id": f"COT-{cotizacion.id}-{cotizacion.transacciones_openpay.count() + 1}",
    }


def _crear_pago_desde_cargo(cotizacion, monto, openpay_id, metodo):
    """Crea el Pago que dispara la póliza automática existente."""
    return Pago.objects.create(
        cotizacion=cotizacion, tipo='INGRESO', concepto='VENTA',
        monto=Decimal(str(monto)), metodo='PLATAFORMA',
        referencia=openpay_id,
        notas=f"Registrado automáticamente vía Openpay ({metodo}).",
    )


# --- TARJETA (síncrono: se sabe el resultado de inmediato) ---

def procesar_cargo_tarjeta(cotizacion: Cotizacion, monto: Decimal, token_id: str, device_session_id: str):
    payload = _payload_cargo_base(cotizacion, monto, 'card')
    payload["source_id"] = token_id
    payload["device_session_id"] = device_session_id

    response = requests.post(_charges_url(), json=payload, auth=_auth(), timeout=20)
    data = response.json()

    if response.status_code >= 400:
        OpenpayTransaccion.objects.create(
            openpay_id=data.get('id') or f"error-{cotizacion.id}-{data.get('request_id', monto)}",
            metodo='card', estado_openpay=str(data.get('error_code', 'error')),
            monto=monto, cotizacion=cotizacion, payload_crudo=data,
            error_detalle=data.get('description', 'Error desconocido de Openpay'),
        )
        return {'ok': False, 'mensaje': data.get('description', 'La tarjeta fue rechazada. Verifica los datos e intenta de nuevo.')}

    registro = OpenpayTransaccion.objects.create(
        openpay_id=data['id'], metodo='card', estado_openpay=data.get('status', ''),
        monto=monto, cotizacion=cotizacion, payload_crudo=data,
        procesado=(data.get('status') == 'completed'),
    )
    if data.get('status') == 'completed':
        try:
            with transaction.atomic():
                pago = _crear_pago_desde_cargo(cotizacion, monto, data['id'], 'card')
                registro.pago = pago
                registro.save(update_fields=['pago'])
        except Exception as e:
            # El cargo YA se hizo en Openpay; si el registro interno del Pago
            # falla, queda el error en la transacción para regularizarlo a mano.
            registro.error_detalle = f"Cargo cobrado en Openpay pero falló el registro del Pago: {e}"
            registro.save(update_fields=['error_detalle'])
            return {'ok': True, 'mensaje': 'Pago recibido. El registro interno quedó pendiente; el equipo lo verá reflejado en breve.'}
        return {'ok': True, 'mensaje': 'Pago realizado con éxito.'}

    return {'ok': False, 'mensaje': f"El pago quedó en estado '{data.get('status')}', no se completó."}


# --- EFECTIVO (asíncrono: se muestra referencia, se confirma por webhook) ---

def procesar_cargo_efectivo(cotizacion: Cotizacion, monto: Decimal):
    payload = _payload_cargo_base(cotizacion, monto, 'store')
    response = requests.post(_charges_url(), json=payload, auth=_auth(), timeout=20)
    data = response.json()

    if response.status_code >= 400:
        return {'ok': False, 'mensaje': data.get('description', 'No se pudo generar la referencia de pago.')}

    store = data.get('payment_method', {}) or data.get('store', {})
    OpenpayTransaccion.objects.create(
        openpay_id=data['id'], metodo='store', estado_openpay=data.get('status', ''),
        monto=monto, cotizacion=cotizacion, payload_crudo=data,
        referencia_pago=store.get('reference', ''),
    )
    return {
        'ok': True, 'referencia': True,
        'reference': store.get('reference', ''),
        'barcode_url': store.get('barcode_url', ''),
    }


# --- SPEI / TRANSFERENCIA (asíncrono, igual que efectivo) ---

def procesar_cargo_spei(cotizacion: Cotizacion, monto: Decimal):
    payload = _payload_cargo_base(cotizacion, monto, 'bank_account')
    response = requests.post(_charges_url(), json=payload, auth=_auth(), timeout=20)
    data = response.json()

    if response.status_code >= 400:
        return {'ok': False, 'mensaje': data.get('description', 'No se pudieron generar los datos de transferencia.')}

    pm = data.get('payment_method', {})
    OpenpayTransaccion.objects.create(
        openpay_id=data['id'], metodo='bank_account', estado_openpay=data.get('status', ''),
        monto=monto, cotizacion=cotizacion, payload_crudo=data,
        referencia_pago=pm.get('clabe', ''),
    )
    return {
        'ok': True, 'referencia': True,
        'bank': pm.get('bank', ''), 'clabe': pm.get('clabe', ''),
        'reference': pm.get('name', ''),
    }


# --- REEMBOLSOS (llama al refund real de Openpay, no solo el registro interno) ---

def reembolsar_cargo_openpay(pago: Pago):
    """
    Reembolsa un cargo ya cobrado por Openpay. Se debe llamar ADEMÁS de crear
    el Pago tipo REEMBOLSO en el admin (acción 'registrar_reembolso' ya
    existente) — ese registro es interno; esto es lo que efectivamente regresa
    el dinero al cliente en Openpay.
    """
    try:
        transaccion = pago.transaccion_openpay
    except OpenpayTransaccion.DoesNotExist:
        return {'ok': False, 'mensaje': 'Este pago no viene de Openpay, no se puede reembolsar por esta vía.'}

    url = f"{_charges_url()}/{transaccion.openpay_id}/refund"
    response = requests.post(url, json={'description': 'Reembolso solicitado'}, auth=_auth(), timeout=20)
    if response.status_code >= 400:
        try:
            detalle = response.json().get('description', '')
        except ValueError:
            detalle = ''
        return {'ok': False, 'mensaje': detalle or 'No se pudo procesar el reembolso en Openpay.'}
    return {'ok': True, 'mensaje': 'Reembolso procesado en Openpay.'}


# --- WEBHOOK (confirma cargos asíncronos: efectivo y SPEI) ---

def procesar_webhook_openpay(payload: dict):
    """
    Procesa una notificación de webhook ya autenticada (la vista valida el
    Basic Auth antes de llamar a esta función).

    Para tarjeta, el resultado ya se supo síncronamente en procesar_cargo_tarjeta.
    El webhook es indispensable para efectivo/SPEI, donde el cliente paga
    después y Openpay avisa cuando el dinero realmente llegó.

    Idempotente por openpay_id: si ya está procesado, no vuelve a crear el
    Pago. Nunca lanza excepción hacia afuera sin registrar el error — la
    vista siempre debe poder regresar 200 OK a Openpay.
    """
    event_type = payload.get('type', '')
    transaction_data = payload.get('transaction', payload)
    if not isinstance(transaction_data, dict):
        return None
    openpay_id = transaction_data.get('id')
    if not openpay_id:
        return None  # notificación sin id de transacción (ej. verification_code) — se ignora aquí

    registro, creado = OpenpayTransaccion.objects.get_or_create(
        openpay_id=openpay_id,
        defaults={
            'event_type': event_type,
            'metodo': transaction_data.get('method', ''),
            'estado_openpay': transaction_data.get('status', ''),
            'monto': _decimal_o_none(transaction_data.get('amount')),
            'payload_crudo': payload,
        }
    )

    if registro.procesado:
        return registro  # ya se procesó antes, no hacer nada (idempotencia)

    if not creado and not registro.event_type:
        registro.event_type = event_type

    if event_type != 'charge.succeeded' or transaction_data.get('status') != 'completed':
        registro.estado_openpay = transaction_data.get('status', '') or registro.estado_openpay
        registro.save(update_fields=['event_type', 'estado_openpay'])
        return registro

    if not registro.cotizacion:
        # Cargo que no nació en el ERP (o registro creado por este mismo
        # webhook): intentar resolver la cotización desde el order_id.
        registro.cotizacion = _resolver_cotizacion_desde_order_id(transaction_data.get('order_id', '') or '')

    if not registro.cotizacion:
        registro.error_detalle = "Webhook confirmó el pago pero no hay cotización ligada al registro."
        registro.save(update_fields=['event_type', 'error_detalle'])
        return registro

    monto = registro.monto if registro.monto is not None else _decimal_o_none(transaction_data.get('amount'))
    if monto is None:
        registro.error_detalle = f"Monto inválido en la notificación: {transaction_data.get('amount')!r}."
        registro.save(update_fields=['event_type', 'error_detalle'])
        return registro

    try:
        with transaction.atomic():
            pago = _crear_pago_desde_cargo(registro.cotizacion, monto, openpay_id, registro.metodo or 'webhook')
            registro.pago = pago
            registro.monto = monto
            registro.procesado = True
            registro.estado_openpay = 'completed'
            registro.error_detalle = ''
            registro.save(update_fields=['event_type', 'cotizacion', 'pago', 'monto', 'procesado', 'estado_openpay', 'error_detalle'])
    except Exception as e:
        registro.error_detalle = f"Error al crear Pago: {e}"
        registro.save(update_fields=['event_type', 'cotizacion', 'error_detalle'])

    return registro


def _resolver_cotizacion_desde_order_id(order_id: str):
    if not order_id.startswith('COT-'):
        return None
    try:
        return Cotizacion.objects.get(pk=int(order_id.split('-')[1]))
    except (ValueError, IndexError, Cotizacion.DoesNotExist):
        return None
