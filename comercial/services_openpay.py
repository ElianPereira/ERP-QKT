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
import logging
import uuid
import requests
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.db import transaction
from .models import Cotizacion, Pago, OpenpayTransaccion, ParcialidadPago

logger = logging.getLogger(__name__)

# Mensajes de error de Openpay traducidos al español. Openpay siempre regresa
# `description` en inglés; `error_code` es estable entre idiomas, así que se
# traduce por código (ver https://www.openpay.mx/docs/api/#errores).
#
# Aquí SOLO viven los códigos que el titular puede corregir por sí mismo
# (tarjeta vencida, sin fondos, CVV mal capturado): decírselo le ahorra un
# intento a ciegas y no revela nada que no tenga ya en la mano.
#
# Todo lo demás —robo, extravío, retención, antifraude, bloqueos del emisor—
# cae al mensaje genérico a propósito. Confirmarle a quien está capturando la
# tarjeta que "fue reportada como robada" o que "la rechazó el antifraude" le
# dice exactamente qué esquivar en el siguiente intento, y con tarjeta ajena
# ese aviso es para el defraudador, no para el cliente. El motivo real queda
# completo en los logs del servidor y en el panel de Openpay
# (ver MOTIVOS_LOG_OPENPAY y _loggear_rechazo_openpay).
MENSAJES_ERROR_TARJETA = {
    2002: "Tu tarjeta ha expirado.",
    2003: "Tu tarjeta no tiene fondos suficientes.",
    2010: "El código de seguridad (CVV) es inválido.",
    3002: "Tu tarjeta ha expirado.",
    3003: "Tu tarjeta no tiene fondos suficientes.",
}

# Motivo explícito por código para el log del servidor. NO se le muestra al
# cliente (ver _mensaje_error_openpay): el cliente sigue viendo un mensaje
# genérico por seguridad, mientras que el log guarda la causa real para poder
# diagnosticar y para la certificación de Openpay.
MOTIVOS_LOG_OPENPAY = {
    2001: "Rechazada por el banco emisor",
    2002: "Tarjeta VENCIDA",
    2003: "Fondos insuficientes",
    2004: "Tarjeta reportada como ROBADA por el emisor",
    2005: "Operación no permitida para esta tarjeta",
    2006: "Tarjeta no válida para pagos en línea (CVV requerido)",
    2007: "Rechazada por RIESGO ALTO (tarjeta de prueba en producción)",
    2008: "Tarjeta reportada como EXTRAVIADA por el emisor",
    2009: "CVV inválido según el emisor",
    2010: "CVV inválido",
    2011: "Tipo de tarjeta no soportado",
    2022: "Tarjeta en lista negra del emisor",
    2023: "Tarjeta requiere autenticación 3D Secure",
    2026: "Tarjeta no procesable (emisor no permite la operación)",
    3001: "El emisor NO AUTORIZÓ la operación",
    3002: "Tarjeta VENCIDA",
    3003: "Fondos insuficientes",
    3004: "Tarjeta RETENIDA (reportada como robada) por el emisor",
    3005: "Rechazada por el sistema ANTIFRAUDE de Openpay",
    3006: "Operación no permitida para el comercio o la tarjeta",
    3008: "Tarjeta no autorizada para pagos en línea",
    3009: "Tarjeta reportada como EXTRAVIADA por el emisor",
    3010: "El emisor BLOQUEÓ la tarjeta para pagos en línea",
    3011: "El emisor solicitó RETENER la tarjeta",
    3012: "Se requiere autorización del emisor para este monto",
}


def _mensaje_error_tarjeta(data: dict) -> str:
    return _mensaje_error_openpay(
        data,
        'No pudimos procesar el pago con esta tarjeta. Verifica los datos, '
        'intenta con otra tarjeta o comunícate con tu banco.',
    )


def _codigo_error(data: dict):
    try:
        return int(data.get('error_code'))
    except (TypeError, ValueError):
        return None


def _loggear_rechazo_openpay(data: dict, cotizacion, monto, metodo: str, http_status: int):
    """
    Deja en los logs del servidor el motivo EXPLÍCITO del rechazo (código,
    descripción cruda de Openpay, request_id). Requisito de la certificación
    de Openpay: al cliente se le muestra un error genérico por seguridad,
    pero el motivo real debe quedar registrado del lado del servidor.
    """
    codigo = _codigo_error(data)
    logger.warning(
        "Openpay RECHAZO [%s] COT-%s monto=%s http=%s error_code=%s motivo=%s "
        "description=%r category=%s request_id=%s",
        metodo,
        getattr(cotizacion, 'id', '?'),
        monto,
        http_status,
        codigo,
        MOTIVOS_LOG_OPENPAY.get(codigo, 'Ver description de Openpay'),
        data.get('description', ''),
        data.get('category', ''),
        data.get('request_id', ''),
    )


def _mensaje_error_openpay(data: dict, default: str) -> str:
    """
    Nunca regresa el `description` crudo de Openpay al cliente: siempre viene
    en inglés (ver nota arriba), así que solo se traduce por `error_code`
    conocido o se usa `default` en español — jamás el texto de Openpay tal
    cual, para no mostrarle inglés al cliente en un segundo intento.

    `MENSAJES_ERROR_TARJETA` solo cubre los rechazos que el titular puede
    corregir; cualquier otro código (robo, extravío, antifraude, bloqueos)
    cae a `default` a propósito.
    """
    codigo = data.get('error_code')
    try:
        codigo = int(codigo)
    except (TypeError, ValueError):
        codigo = None
    return MENSAJES_ERROR_TARJETA.get(codigo, default)


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


def _datos_customer(cliente):
    """Openpay exige un objeto customer en cada cargo ('Attribute customer is
    required'). requires_account=False evita que Openpay cree una cuenta de
    usuario para el cliente — solo usa los datos para el cargo."""
    nombre_completo = (cliente.nombre or '').strip()
    nombre, _, apellidos = nombre_completo.partition(' ')
    return {
        "name": nombre or 'Cliente',
        "last_name": apellidos.strip(),
        "email": cliente.email or settings.DEFAULT_FROM_EMAIL,
        "phone_number": cliente.telefono or '',
        "requires_account": False,
    }


# Vigencia por defecto de una referencia de efectivo/SPEI cuando la cotización
# no tiene un plan de pagos que marque la fecha límite. Openpay documenta
# `due_date` como opcional y no fija un máximo, así que el criterio es de
# negocio: dar margen suficiente para ir a la tienda, sin que la referencia
# siga viva después del evento.
VIGENCIA_REFERENCIA_HORAS = 72


def _due_date_referencia(cotizacion: Cotizacion):
    """
    Fecha de vencimiento de una referencia de efectivo/SPEI, en el formato
    ISO 8601 que espera Openpay ('2014-05-28T13:45:00').

    Criterio, en orden:
    1. La fecha límite de la parcialidad pendiente más próxima, si hay plan
       de pagos activo (así la referencia muere cuando vence el compromiso).
    2. Si no, VIGENCIA_REFERENCIA_HORAS a partir de ahora.
    En ambos casos se topa a la fecha del evento: una referencia que vence
    después del evento no tiene sentido para el negocio.
    """
    from django.utils import timezone

    ahora = timezone.localtime()
    vence = ahora + timedelta(hours=VIGENCIA_REFERENCIA_HORAS)

    parcialidad = (
        ParcialidadPago.objects
        .filter(plan__cotizacion=cotizacion, plan__activo=True, pagada=False)
        .order_by('fecha_limite')
        .first()
    )
    if parcialidad and parcialidad.fecha_limite:
        limite_plan = ahora.replace(
            year=parcialidad.fecha_limite.year,
            month=parcialidad.fecha_limite.month,
            day=parcialidad.fecha_limite.day,
            hour=23, minute=59, second=0, microsecond=0,
        )
        if limite_plan > ahora:
            vence = limite_plan

    if cotizacion.fecha_evento:
        fin_evento = ahora.replace(
            year=cotizacion.fecha_evento.year,
            month=cotizacion.fecha_evento.month,
            day=cotizacion.fecha_evento.day,
            hour=23, minute=59, second=0, microsecond=0,
        )
        if fin_evento > ahora:
            vence = min(vence, fin_evento)

    return vence.strftime('%Y-%m-%dT%H:%M:%S')


def _payload_cargo_base(cotizacion: Cotizacion, monto: Decimal, metodo: str):
    return {
        "method": metodo,
        "amount": float(monto),
        "currency": "MXN",
        "description": f"COT-{cotizacion.id:03d} - {cotizacion.nombre_evento}",
        # uuid4 en vez de un contador basado en OpenpayTransaccion: los cargos
        # de efectivo/SPEI que Openpay rechaza (400) no dejan registro local,
        # así que un contador reintenta el mismo order_id ya usado y Openpay
        # responde "the order_id has already been processed".
        "order_id": f"COT-{cotizacion.id}-{uuid.uuid4().hex[:12]}",
        "customer": _datos_customer(cotizacion.cliente),
    }


def _crear_pago_desde_cargo(cotizacion, monto, openpay_id, metodo):
    """Crea el Pago que dispara la póliza automática existente."""
    return Pago.objects.create(
        cotizacion=cotizacion, tipo='INGRESO', concepto='VENTA',
        monto=Decimal(str(monto)), metodo='PLATAFORMA',
        referencia=openpay_id,
        notas=f"Registrado automáticamente vía Openpay ({metodo}).",
    )


def _registrar_comision_openpay(registro, fee):
    """Póliza automática de la comisión de Openpay (fee.amount + fee.tax).
    Nunca debe tumbar el registro del pago: cualquier falla solo se loggea."""
    if not fee:
        return
    try:
        from contabilidad.signals import crear_poliza_comision_openpay
        crear_poliza_comision_openpay(registro, fee)
    except Exception:
        logger.exception("No se pudo registrar la póliza de comisión Openpay para %s.", registro.openpay_id)


def _confirmar_cargo_completado(registro, cotizacion, monto, data, metodo):
    """
    Marca un cargo ya autorizado como pagado: crea el Pago (que dispara la
    póliza contable) y registra la comisión. Compartido por el flujo síncrono
    de tarjeta y por el retorno de 3D Secure, para que ambos terminen igual.
    Idempotente: si el registro ya venía procesado, no duplica el Pago.
    """
    if registro.procesado and registro.pago_id:
        return {'ok': True, 'mensaje': 'Pago realizado con éxito.'}
    try:
        with transaction.atomic():
            pago = _crear_pago_desde_cargo(cotizacion, monto, registro.openpay_id, metodo)
            registro.pago = pago
            registro.procesado = True
            registro.estado_openpay = 'completed'
            registro.error_detalle = ''
            registro.save(update_fields=['pago', 'procesado', 'estado_openpay', 'error_detalle'])
        _registrar_comision_openpay(registro, data.get('fee'))
    except Exception as e:
        # El cargo YA se cobró en Openpay; si el registro interno falla, queda
        # el detalle en la transacción para regularizarlo a mano.
        logger.exception(
            "Openpay: cargo %s cobrado pero falló el registro del Pago (COT-%s).",
            registro.openpay_id, getattr(cotizacion, 'id', '?'),
        )
        registro.error_detalle = f"Cargo cobrado en Openpay pero falló el registro del Pago: {e}"
        registro.save(update_fields=['error_detalle'])
        return {'ok': True, 'mensaje': 'Pago recibido. El registro interno quedó pendiente; el equipo lo verá reflejado en breve.'}
    return {'ok': True, 'mensaje': 'Pago realizado con éxito.'}


# --- TARJETA (con 3D Secure: el resultado final llega tras la autenticación) ---

def procesar_cargo_tarjeta(cotizacion: Cotizacion, monto: Decimal, token_id: str,
                           device_session_id: str, redirect_url: str = ''):
    """
    Crea el cargo con tarjeta usando 3D Secure.

    Con `use_3d_secure` el cargo NO se cobra de inmediato: Openpay responde
    status='charge_pending' y un `payment_method.url` al que hay que redirigir
    al cliente para que se autentique con su banco emisor. Terminada la
    autenticación, Openpay regresa al cliente a `redirect_url` y ahí se
    consulta el cargo para conocer el resultado final (ver
    `consultar_y_confirmar_cargo`).
    """
    payload = _payload_cargo_base(cotizacion, monto, 'card')
    payload["source_id"] = token_id
    payload["device_session_id"] = device_session_id
    if redirect_url:
        payload["use_3d_secure"] = True
        payload["redirect_url"] = redirect_url

    response = requests.post(_charges_url(), json=payload, auth=_auth(), timeout=20)
    data = response.json()

    if response.status_code >= 400:
        _loggear_rechazo_openpay(data, cotizacion, monto, 'card', response.status_code)
        codigo = _codigo_error(data)
        OpenpayTransaccion.objects.create(
            openpay_id=data.get('id') or f"error-{cotizacion.id}-{data.get('request_id', monto)}",
            metodo='card', estado_openpay=str(data.get('error_code', 'error')),
            monto=monto, cotizacion=cotizacion, payload_crudo=data,
            autorizacion=data.get('authorization') or '',
            error_detalle="[{}] {} | {}".format(
                codigo,
                MOTIVOS_LOG_OPENPAY.get(codigo, 'Rechazo de Openpay'),
                data.get('description', 'Error desconocido de Openpay'),
            ),
        )
        return {'ok': False, 'mensaje': _mensaje_error_tarjeta(data)}

    estado = data.get('status')
    registro = OpenpayTransaccion.objects.create(
        openpay_id=data['id'], metodo='card', estado_openpay=estado or '',
        monto=monto, cotizacion=cotizacion, payload_crudo=data,
        autorizacion=data.get('authorization') or '',
        procesado=(estado == 'completed'),
    )

    # 3D Secure: el cargo queda pendiente hasta que el cliente se autentique
    # con su banco. Se le manda al portal la URL de redirección de Openpay.
    if estado == 'charge_pending':
        url_3ds = (data.get('payment_method') or {}).get('url', '')
        if not url_3ds:
            logger.error(
                "Openpay 3DS: cargo %s quedó en charge_pending pero sin payment_method.url (COT-%s).",
                data['id'], cotizacion.id,
            )
            registro.error_detalle = "3D Secure sin URL de redirección en la respuesta de Openpay."
            registro.save(update_fields=['error_detalle'])
            return {'ok': False, 'mensaje': 'No se pudo iniciar la validación de tu banco. Intenta de nuevo o contáctanos.'}
        logger.info(
            "Openpay 3DS: redirigiendo COT-%s (cargo %s, monto %s) a autenticación del emisor.",
            cotizacion.id, data['id'], monto,
        )
        return {'ok': True, 'redirect_3ds': url_3ds, 'openpay_id': data['id']}

    if estado == 'completed':
        return _confirmar_cargo_completado(registro, cotizacion, monto, data, 'card')

    # Openpay puede rechazar con HTTP 200 y status 'failed' (ej. rechazo del
    # emisor tras autorizar el token). El motivo explícito va al log; al
    # cliente se le da el mensaje genérico, sin filtrar el estado interno.
    logger.warning(
        "Openpay NO COMPLETADO [card] COT-%s monto=%s openpay_id=%s status=%s "
        "error_code=%s description=%r authorization=%s",
        cotizacion.id, monto, data.get('id'), data.get('status'),
        data.get('error_code'), data.get('description', ''),
        data.get('authorization', ''),
    )
    registro.error_detalle = "Cargo no completado. status={} error_code={} description={}".format(
        data.get('status'), data.get('error_code'), data.get('description', ''),
    )
    registro.save(update_fields=['error_detalle'])
    return {'ok': False, 'mensaje': _mensaje_error_tarjeta(data)}


def consultar_y_confirmar_cargo(cotizacion: Cotizacion, openpay_id: str):
    """
    Consulta el estado final de un cargo en Openpay y, si quedó autorizado,
    registra el Pago. Se llama cuando el cliente regresa de la autenticación
    3D Secure: hasta ese momento el cargo estaba en 'charge_pending' y solo
    Openpay sabe si el banco emisor autorizó.

    Idempotente: si el cargo ya se había confirmado (por webhook o por una
    recarga de la página de retorno), no duplica el Pago.
    """
    registro = OpenpayTransaccion.objects.filter(openpay_id=openpay_id).first()
    if registro and registro.procesado and registro.pago_id:
        return {'ok': True, 'mensaje': 'Pago realizado con éxito.'}

    url = f"{_charges_url()}/{openpay_id}"
    response = requests.get(url, auth=_auth(), timeout=20)
    data = response.json()

    if response.status_code >= 400:
        _loggear_rechazo_openpay(data, cotizacion, getattr(registro, 'monto', None), 'card-3ds', response.status_code)
        return {'ok': False, 'mensaje': 'No pudimos confirmar tu pago. Intenta de nuevo o contáctanos.'}

    estado = data.get('status')
    monto = _decimal_o_none(data.get('amount'))
    if monto is None:
        monto = getattr(registro, 'monto', None)

    if registro is None:
        # El cargo existe en Openpay pero no localmente (ej. se perdió el
        # registro): se reconstruye para no dejar el pago sin rastro.
        registro = OpenpayTransaccion.objects.create(
            openpay_id=openpay_id, metodo='card', estado_openpay=estado or '',
            monto=monto, cotizacion=cotizacion, payload_crudo=data,
            autorizacion=data.get('authorization') or '',
        )
    else:
        registro.estado_openpay = estado or registro.estado_openpay
        registro.payload_crudo = data
        registro.autorizacion = data.get('authorization') or registro.autorizacion
        registro.save(update_fields=['estado_openpay', 'payload_crudo', 'autorizacion'])

    if estado == 'completed':
        logger.info(
            "Openpay 3DS: autenticación exitosa, cargo %s COMPLETADO (COT-%s, monto %s).",
            openpay_id, getattr(cotizacion, 'id', '?'), monto,
        )
        return _confirmar_cargo_completado(registro, cotizacion, monto, data, 'card (3D Secure)')

    if estado == 'charge_pending':
        logger.warning(
            "Openpay 3DS: el cliente regresó pero el cargo %s sigue en charge_pending (COT-%s).",
            openpay_id, getattr(cotizacion, 'id', '?'),
        )
        return {'ok': False, 'pendiente': True,
                'mensaje': 'Tu pago sigue en validación con tu banco. Si ya lo autorizaste, '
                           'se reflejará en unos minutos en este portal.'}

    # Rechazado tras la autenticación 3DS: el motivo explícito va solo al log.
    logger.warning(
        "Openpay 3DS RECHAZO: cargo %s COT-%s status=%s error_code=%s description=%r",
        openpay_id, getattr(cotizacion, 'id', '?'), estado,
        data.get('error_code'), data.get('description', ''),
    )
    registro.error_detalle = "3D Secure no autorizado. status={} error_code={} description={}".format(
        estado, data.get('error_code'), data.get('description', ''),
    )
    registro.save(update_fields=['error_detalle'])
    return {'ok': False, 'mensaje': _mensaje_error_tarjeta(data)}


# --- EFECTIVO (asíncrono: se muestra referencia, se confirma por webhook) ---

def procesar_cargo_efectivo(cotizacion: Cotizacion, monto: Decimal):
    payload = _payload_cargo_base(cotizacion, monto, 'store')
    payload["due_date"] = _due_date_referencia(cotizacion)
    response = requests.post(_charges_url(), json=payload, auth=_auth(), timeout=20)
    data = response.json()

    if response.status_code >= 400:
        _loggear_rechazo_openpay(data, cotizacion, monto, 'store', response.status_code)
        return {'ok': False, 'mensaje': _mensaje_error_openpay(data, 'No se pudo generar la referencia de pago. Intenta de nuevo o contáctanos.')}

    store = data.get('payment_method', {}) or data.get('store', {})
    # update_or_create en vez de create: el sandbox de Openpay reutiliza el
    # mismo id de cargo fijo para 'store' (no simula estados reales como
    # tarjeta), así que un segundo cargo de prueba pisaría el unique de
    # openpay_id y tumbaría el pago con un IntegrityError.
    OpenpayTransaccion.objects.update_or_create(
        openpay_id=data['id'],
        defaults=dict(
            metodo='store', estado_openpay=data.get('status', ''),
            monto=monto, cotizacion=cotizacion, payload_crudo=data,
            referencia_pago=store.get('reference', ''),
            autorizacion=data.get('authorization') or '',
        ),
    )
    return {
        'ok': True, 'referencia': True, 'metodo': 'store',
        'reference': store.get('reference', ''),
        'barcode_url': store.get('barcode_url', ''),
        'monto': f"{monto:,.2f}",
        'due_date': store.get('due_date') or data.get('due_date') or payload.get('due_date', ''),
        'order_id': data.get('order_id', ''),
        'comercio': 'Quinta Ko\'ox Tanil',
    }


# --- SPEI / TRANSFERENCIA (asíncrono, igual que efectivo) ---

def procesar_cargo_spei(cotizacion: Cotizacion, monto: Decimal):
    payload = _payload_cargo_base(cotizacion, monto, 'bank_account')
    payload["due_date"] = _due_date_referencia(cotizacion)
    response = requests.post(_charges_url(), json=payload, auth=_auth(), timeout=20)
    data = response.json()

    if response.status_code >= 400:
        _loggear_rechazo_openpay(data, cotizacion, monto, 'bank_account', response.status_code)
        return {'ok': False, 'mensaje': _mensaje_error_openpay(data, 'No se pudieron generar los datos de transferencia. Intenta de nuevo o contáctanos.')}

    pm = data.get('payment_method', {})
    # update_or_create por la misma razón que en procesar_cargo_efectivo: el
    # sandbox de Openpay reutiliza un id de cargo fijo para 'bank_account'.
    OpenpayTransaccion.objects.update_or_create(
        openpay_id=data['id'],
        defaults=dict(
            metodo='bank_account', estado_openpay=data.get('status', ''),
            monto=monto, cotizacion=cotizacion, payload_crudo=data,
            referencia_pago=pm.get('clabe', ''),
            autorizacion=data.get('authorization') or '',
        ),
    )
    return {
        'ok': True, 'referencia': True, 'metodo': 'bank_account',
        'bank': pm.get('bank', ''), 'clabe': pm.get('clabe', ''),
        'reference': pm.get('name', ''),
        'agreement': pm.get('agreement', ''),
        'monto': f"{monto:,.2f}",
        'due_date': pm.get('due_date') or data.get('due_date') or payload.get('due_date', ''),
        'order_id': data.get('order_id', ''),
        'comercio': 'Quinta Ko\'ox Tanil',
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


# --- LIMPIEZA DE TRANSACCIONES DE PRUEBA (sandbox) ---

def borrar_transacciones_openpay_prueba(registros):
    """
    Borra cada OpenpayTransaccion junto con su Pago y las pólizas contables
    que generó (pago + comisión, con sus movimientos vía CASCADE). NO toca
    la Cotizacion/Cliente — el saldo pendiente vuelve a su valor original,
    como si el pago nunca se hubiera hecho.

    Se niega si OPENPAY_MODE ya es 'production', para no borrar transacciones
    reales por error después de salir en vivo. Usado tanto por el comando
    `limpiar_transacciones_openpay_prueba` como por la acción del admin.

    Devuelve (n_transacciones, n_pagos) borrados.
    """
    if settings.OPENPAY_MODE == 'production':
        raise ValueError(
            "OPENPAY_MODE es 'production' — esta limpieza es solo para datos de "
            "sandbox y se niega a correr para no borrar transacciones reales."
        )

    from django.contrib.contenttypes.models import ContentType
    from contabilidad.models import Poliza

    registros = list(registros)
    ct_pago = ContentType.objects.get_for_model(Pago)
    ct_transaccion = ContentType.objects.get_for_model(OpenpayTransaccion)

    borrados_pagos = 0
    with transaction.atomic():
        for r in registros:
            Poliza.objects.filter(content_type=ct_transaccion, object_id=r.pk).delete()
            if r.pago_id:
                Poliza.objects.filter(content_type=ct_pago, object_id=r.pago_id).delete()
                Pago.objects.filter(pk=r.pago_id).delete()
                borrados_pagos += 1
        ids = [r.pk for r in registros]
        OpenpayTransaccion.objects.filter(pk__in=ids).delete()

    return len(registros), borrados_pagos


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
            'autorizacion': transaction_data.get('authorization') or '',
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
            registro.autorizacion = transaction_data.get('authorization', '') or registro.autorizacion
            registro.save(update_fields=['event_type', 'cotizacion', 'pago', 'monto', 'procesado', 'estado_openpay', 'error_detalle', 'autorizacion'])
        _registrar_comision_openpay(registro, transaction_data.get('fee'))
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
