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
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.db import transaction

from .models import Contracargo, Cotizacion, OpenpayTransaccion, Pago, ParcialidadPago

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


# El recibo genérico de Openpay vive en el dashboard, no en la API: es una
# ruta armada a mano, no un campo que venga en la respuesta del cargo. Solo
# responde mientras la transacción está pendiente; una vez pagada o cancelada
# deja de mostrarse.
#
# OJO con el último segmento, que NO es el mismo en los dos métodos:
#   /spei-pdf/{merchant}/{id de la transacción}
#   /paynet-pdf/{merchant}/{payment_method.reference}
# Usar el id de la transacción en la ruta de paynet devuelve un error; es
# exactamente lo que rompía la ficha de pago en tiendas.
OPENPAY_DASHBOARD_URL = (
    "https://sandbox-dashboard.openpay.mx"
    if settings.OPENPAY_MODE == 'sandbox'
    else "https://dashboard.openpay.mx"
)

# Tope de Openpay para cargos en tienda. Rebasarlo hace que la API rechace el
# cargo, así que conviene avisarlo antes de salir a la red.
MONTO_MAXIMO_EFECTIVO = Decimal('29999.99')


def _recibo_pdf_url(tipo: str, identificador: str) -> str:
    """
    `tipo` es 'spei' o 'paynet'. El identificador que espera cada ruta es
    distinto (ver nota de arriba): la transacción para SPEI, la referencia
    para Paynet.
    """
    if not identificador:
        return ''
    return (f"{OPENPAY_DASHBOARD_URL}/{tipo}-pdf/"
            f"{settings.OPENPAY_MERCHANT_ID}/{identificador}")


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
# no tiene un plan de pagos que marque la fecha límite. El criterio es de
# negocio: dar margen suficiente para ir a la tienda, sin que la referencia
# siga viva después del evento.
VIGENCIA_REFERENCIA_HORAS = 72

# Tope duro de Openpay: "El tiempo máximo permitido por Openpay es de 30 días"
# (guía de pagos en banco). Importa porque la fecha límite puede venir del plan
# de pagos, y una parcialidad a 45 días empujaba el `due_date` fuera de rango.
VIGENCIA_REFERENCIA_MAXIMA_DIAS = 30


def _due_date_referencia(cotizacion: Cotizacion):
    """
    Fecha de vencimiento de una referencia de efectivo/SPEI, en el formato
    ISO 8601 que espera Openpay ('2014-05-28T13:45:00').

    Criterio, en orden:
    1. La fecha límite de la parcialidad pendiente más próxima, si hay plan
       de pagos activo (así la referencia muere cuando vence el compromiso).
    2. Si no, VIGENCIA_REFERENCIA_HORAS a partir de ahora.
    En ambos casos se topa a la fecha del evento —una referencia que vence
    después del evento no tiene sentido para el negocio— y a los 30 días que
    Openpay admite como máximo.
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

    # El tope de Openpay se aplica al final, sobre el resultado de las reglas
    # de negocio: es un límite del procesador, no una preferencia nuestra.
    tope = ahora + timedelta(days=VIGENCIA_REFERENCIA_MAXIMA_DIAS)
    vence = min(vence, tope)

    return vence.strftime('%Y-%m-%dT%H:%M:%S')


# Marca en el order_id de los cargos de depósito en garantía: permite saber el
# destino de un cargo aunque el registro local lo cree el webhook.
MARCA_DEPOSITO = 'DEP'


def _payload_cargo_base(cotizacion: Cotizacion, monto: Decimal, metodo: str, destino: str = 'SERVICIO'):
    es_deposito = destino == 'DEPOSITO'
    concepto = 'Depósito en garantía' if es_deposito else cotizacion.nombre_evento
    marca = MARCA_DEPOSITO if es_deposito else ''
    return {
        "method": metodo,
        "amount": float(monto),
        "currency": "MXN",
        "description": f"COT-{cotizacion.id:03d} - {concepto}",
        # uuid4 en vez de un contador basado en OpenpayTransaccion: los cargos
        # de efectivo/SPEI que Openpay rechaza (400) no dejan registro local,
        # así que un contador reintenta el mismo order_id ya usado y Openpay
        # responde "the order_id has already been processed".
        "order_id": f"COT-{cotizacion.id}-{marca}{uuid.uuid4().hex[:12]}",
        "customer": _datos_customer(cotizacion.cliente),
    }


def _destino_desde_order_id(order_id: str) -> str:
    partes = (order_id or '').split('-')
    return 'DEPOSITO' if len(partes) > 2 and partes[2].startswith(MARCA_DEPOSITO) else 'SERVICIO'


def _cobro_ya_registrado(registro) -> bool:
    """Un cargo confirmado queda como Pago (servicio) o como MovimientoDeposito."""
    if not registro.procesado:
        return False
    if registro.destino == 'DEPOSITO':
        return hasattr(registro, 'movimiento_deposito')
    return bool(registro.pago_id)


def _registrar_cobro(registro, cotizacion, monto, metodo):
    """Registra el cobro según su destino. Debe correr dentro de un atomic()."""
    if registro.destino == 'DEPOSITO':
        from .services_deposito import registrar_recepcion_openpay
        registro.cotizacion = cotizacion
        registrar_recepcion_openpay(registro, monto)
    else:
        registro.pago = _crear_pago_desde_cargo(cotizacion, monto, registro.openpay_id, metodo)


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
    if _cobro_ya_registrado(registro):
        return {'ok': True, 'mensaje': 'Pago realizado con éxito.'}
    try:
        with transaction.atomic():
            _registrar_cobro(registro, cotizacion, monto, metodo)
            registro.procesado = True
            registro.estado_openpay = 'completed'
            registro.error_detalle = ''
            registro.save(update_fields=['cotizacion', 'pago', 'procesado', 'estado_openpay', 'error_detalle'])
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
                           device_session_id: str, redirect_url: str = '',
                           use_card_points: bool = False, destino: str = 'SERVICIO'):
    """
    Crea el cargo con tarjeta usando 3D Secure.

    Con `use_3d_secure` el cargo NO se cobra de inmediato: Openpay responde
    status='charge_pending' y un `payment_method.url` al que hay que redirigir
    al cliente para que se autentique con su banco emisor. Terminada la
    autenticación, Openpay regresa al cliente a `redirect_url` y ahí se
    consulta el cargo para conocer el resultado final (ver
    `consultar_y_confirmar_cargo`).

    `use_card_points` solo llega en true si el token indicó que la tarjeta
    admite puntos y el cliente los aceptó en el portal.
    """
    payload = _payload_cargo_base(cotizacion, monto, 'card', destino)
    payload["source_id"] = token_id
    payload["device_session_id"] = device_session_id
    if use_card_points:
        payload["use_card_points"] = True
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
            monto=monto, cotizacion=cotizacion, payload_crudo=data, destino=destino,
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
        monto=monto, cotizacion=cotizacion, payload_crudo=data, destino=destino,
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
        resultado = _confirmar_cargo_completado(registro, cotizacion, monto, data, 'card')
        # Openpay puede devolver una leyenda sobre los puntos usados y el saldo
        # restante. Mostrarla en el comprobante es requisito de su guía, no un
        # adorno, así que se propaga al portal tal como la manda el emisor.
        leyenda = (data.get('card_points') or {}).get('caption', '')
        if leyenda:
            resultado['mensaje_puntos'] = leyenda
        return resultado

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
    if registro and _cobro_ya_registrado(registro):
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
            destino=_destino_desde_order_id(data.get('order_id', '')),
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
        resultado = _confirmar_cargo_completado(registro, cotizacion, monto, data, 'card (3D Secure)')
        leyenda = (data.get('card_points') or {}).get('caption', '')
        if leyenda:
            resultado['mensaje_puntos'] = leyenda
        return resultado

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

def procesar_cargo_efectivo(cotizacion: Cotizacion, monto: Decimal, destino: str = 'SERVICIO'):
    if monto > MONTO_MAXIMO_EFECTIVO:
        return {
            'ok': False,
            'mensaje': (
                f'El pago en efectivo admite como máximo ${MONTO_MAXIMO_EFECTIVO:,.2f} MXN '
                'por operación. Puedes abonar un monto menor y generar otra '
                'referencia después, o pagar con tarjeta o transferencia SPEI.'
            ),
        }

    payload = _payload_cargo_base(cotizacion, monto, 'store', destino)
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
            monto=monto, cotizacion=cotizacion, payload_crudo=data, destino=destino,
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
        'recibo_url': _recibo_pdf_url('paynet', store.get('reference', '')),
        # Para armar el enlace a nuestra propia ficha, que sí lleva la marca
        # del negocio y los datos del evento.
        'openpay_id': data.get('id', ''),
    }


# --- SPEI / TRANSFERENCIA (asíncrono, igual que efectivo) ---

def procesar_cargo_spei(cotizacion: Cotizacion, monto: Decimal, destino: str = 'SERVICIO'):
    payload = _payload_cargo_base(cotizacion, monto, 'bank_account', destino)
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
            monto=monto, cotizacion=cotizacion, payload_crudo=data, destino=destino,
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
        'recibo_url': _recibo_pdf_url('spei', data.get('id', '')),
    }


def datos_referencia_pendiente(transaccion: OpenpayTransaccion) -> dict:
    """
    Reconstruye la misma forma de dict que devuelven procesar_cargo_efectivo/
    procesar_cargo_spei, a partir de una OpenpayTransaccion ya guardada — para
    poder mostrarle al cliente en el portal una referencia generada en un
    intento anterior sin volver a llamar a Openpay.
    """
    data = transaccion.payload_crudo or {}
    pm = data.get('payment_method', {}) or data.get('store', {}) or {}
    monto = transaccion.monto if transaccion.monto is not None else Decimal('0')
    base = {
        'ok': True, 'referencia': True, 'metodo': transaccion.metodo,
        'reference': pm.get('reference') or pm.get('name', ''),
        'monto': f"{monto:,.2f}",
        'due_date': pm.get('due_date') or data.get('due_date', ''),
        'order_id': data.get('order_id', ''),
        'comercio': 'Quinta Ko\'ox Tanil',
    }
    if transaccion.metodo == 'store':
        base['barcode_url'] = pm.get('barcode_url', '')
        base['recibo_url'] = _recibo_pdf_url('paynet', pm.get('reference', ''))
        base['openpay_id'] = transaccion.openpay_id
    else:
        base['bank'] = pm.get('bank', '')
        base['clabe'] = pm.get('clabe', '')
        base['agreement'] = pm.get('agreement', '')
        base['recibo_url'] = _recibo_pdf_url('spei', transaccion.openpay_id)
    return base


def _referencias_vigentes(cotizacion: Cotizacion, destino: str = 'SERVICIO') -> list:
    """Referencias de efectivo/SPEI generadas, sin pagar y sin vencer (todas,
    de la más reciente a la más vieja). Las del depósito en garantía van
    aparte: no son saldo del servicio."""
    from django.utils import timezone

    ahora = timezone.localtime()
    vigentes = []
    qs = OpenpayTransaccion.objects.filter(
        cotizacion=cotizacion, metodo__in=('store', 'bank_account'), destino=destino,
        procesado=False, estado_openpay='in_progress',
    ).order_by('-created_at')
    for t in qs:
        data = t.payload_crudo or {}
        pm = data.get('payment_method', {}) or data.get('store', {}) or {}
        due = pm.get('due_date') or data.get('due_date')
        if due:
            try:
                vence = datetime.fromisoformat(due)
                if timezone.is_naive(vence):
                    vence = timezone.make_aware(vence)
                if vence < ahora:
                    continue
            except (ValueError, TypeError):
                pass
        vigentes.append(t)
    return vigentes


def transacciones_pendientes(cotizacion: Cotizacion, destino: str = 'SERVICIO') -> list:
    """
    Referencias de efectivo/SPEI ya generadas, vigentes y aún sin pagar — la
    más reciente por método (store/bank_account).

    Cada clic en "pagar" con estos dos métodos genera una referencia NUEVA
    sin cancelar la anterior; un cliente que reintenta sin saber esto termina
    con varias referencias válidas a la vez y no sabe cuál usar (caso real:
    2 CLABEs SPEI + 1 ficha de efectivo en 17 minutos). El portal usa esto
    para mostrárselas de entrada en vez de dejarlo generar otra a ciegas.
    """
    vistos = set()
    resultado = []
    for t in _referencias_vigentes(cotizacion, destino):
        if t.metodo in vistos:
            continue
        vistos.add(t.metodo)
        resultado.append(datos_referencia_pendiente(t))
    return resultado


def monto_en_camino(cotizacion: Cotizacion, destino: str = 'SERVICIO') -> Decimal:
    """Suma de TODAS las referencias vigentes sin pagar: cualquiera de ellas
    puede pagarse en la tienda o por SPEI en cualquier momento, así que es
    saldo ya comprometido. Sin descontarlo, el portal dejaba cobrar el mismo
    saldo con tarjeta y la ficha pagada después rebasaba el total."""
    return sum(
        (t.monto for t in _referencias_vigentes(cotizacion, destino) if t.monto is not None),
        Decimal('0.00'),
    )


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


def reembolsar_monto_openpay(transaccion: OpenpayTransaccion, monto: Decimal):
    """Reembolsa `monto` (total o parcial) de un cargo con tarjeta. Lo usa la
    devolución del depósito en garantía; Openpay solo reembolsa tarjetas."""
    url = f"{_charges_url()}/{transaccion.openpay_id}/refund"
    payload = {'description': 'Devolución de depósito en garantía', 'amount': float(monto)}
    response = requests.post(url, json=payload, auth=_auth(), timeout=20)
    if response.status_code >= 400:
        try:
            detalle = response.json().get('description', '')
        except ValueError:
            detalle = ''
        logger.warning("Openpay: reembolso de %s por %s rechazado: %s", transaccion.openpay_id, monto, detalle)
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

    if 'chargeback' in event_type.lower():
        # Un contracargo no es un cargo: se procesa en su propio modelo
        # (Contracargo), nunca en OpenpayTransaccion (esa tabla es
        # específicamente de cargos, ver su docstring).
        return procesar_webhook_contracargo(payload)

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
            'destino': _destino_desde_order_id(transaction_data.get('order_id', '')),
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
            _registrar_cobro(registro, registro.cotizacion, monto, registro.metodo or 'webhook')
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
        _alertar_equipo_cobro_sin_registrar(registro, monto)

    return registro


def _alertar_equipo_cobro_sin_registrar(registro, monto):
    """El dinero ya entró a Openpay pero no se pudo registrar como Pago (lo
    típico: rebasa el saldo porque el cliente pagó dos veces). Sin esta
    alerta solo quedaba el error en la transacción, sin que nadie se
    enterara de que hay que reembolsar o aplicar el excedente. Nunca debe
    tumbar el webhook."""
    try:
        from comunicacion.services import alertar_equipo_email
        cot = registro.cotizacion
        alertar_equipo_email(
            cot,
            asunto=f"⚠️ Cobro de Openpay sin registrar — COT-{cot.id:03d}",
            cuerpo=(
                f"Openpay confirmó un cobro de ${monto:,.2f} (transacción "
                f"{registro.openpay_id}) para COT-{cot.id:03d} ({cot.cliente}), pero "
                f"no se pudo registrar como pago.\n\nDetalle: {registro.error_detalle}\n\n"
                "Revisa la transacción en el admin y reembolsa o aplica el excedente."
            ),
            clave_idempotencia=f"openpay_sin_registrar:{registro.openpay_id}",
        )
    except Exception:
        logger.exception("No se pudo alertar del cobro sin registrar %s.", registro.openpay_id)


def _resolver_cotizacion_desde_order_id(order_id: str):
    if not order_id.startswith('COT-'):
        return None
    try:
        return Cotizacion.objects.get(pk=int(order_id.split('-')[1]))
    except (ValueError, IndexError, Cotizacion.DoesNotExist):
        return None


# --- CONTRACARGOS (Issue #303) ---
#
# Ciclo de vida confirmado con soporte de Openpay (caso CS1234019,
# 2026-09-22) — la nomenclatura es contraintuitiva, ver docstring de
# Contracargo en models.py: chargeback.created = en disputa,
# chargeback.accepted = perdido (a favor del cliente),
# chargeback.rejected = ganado (a favor del comercio).
#
# No se reinventa la reversión/reactivación contable: se crean Pago
# normales (tipo='REEMBOLSO' / tipo='INGRESO') y el signal ya existente en
# contabilidad/signals.py genera la póliza sola.

_EVENTO_CONTRACARGO_A_ESTADO = {
    'chargeback.created': 'EN_DISPUTA',
    'chargeback.accepted': 'PERDIDO',   # el banco le dio la razón al cliente
    'chargeback.rejected': 'GANADO',    # el banco le dio la razón al comercio
}


def _sumar_dias_habiles(fecha_inicio, n):
    """
    `fecha_inicio` + `n` días hábiles (lunes-viernes). No considera el
    calendario oficial de días festivos en México —no hay una fuente de
    festivos en el repo—, es una aproximación conservadora, no un cálculo
    legal exacto. Usado para el plazo de evidencia de un contracargo.
    """
    fecha = fecha_inicio
    agregados = 0
    while agregados < n:
        fecha += timedelta(days=1)
        if fecha.weekday() < 5:
            agregados += 1
    return fecha


def _resolver_openpay_id_original(transaction_data, payload):
    """
    Candidatos plausibles de id del cargo original dentro de una
    notificación de contracargo. Openpay no documenta el nombre exacto del
    campo —se prueban varias claves conocidas en vez de adivinar una sola—.
    """
    candidatos = []
    for fuente in (transaction_data, payload):
        if not isinstance(fuente, dict):
            continue
        for clave in ('transaction_id', 'charge_id', 'original_transaction_id'):
            valor = fuente.get(clave)
            if valor:
                candidatos.append(str(valor))
        anidada = fuente.get('transaction')
        if isinstance(anidada, dict) and anidada.get('id'):
            candidatos.append(str(anidada['id']))
    if transaction_data.get('id'):
        candidatos.append(str(transaction_data['id']))
    return candidatos


def _resolver_contexto_contracargo(transaction_data, payload):
    """
    Intenta resolver la OpenpayTransaccion/Cotizacion original de un
    contracargo. Si ninguna estrategia resuelve, se deja sin vincular en
    vez de adivinar (ver Contracargo.requiere_vinculacion_manual).
    """
    transaccion = None
    for candidato in _resolver_openpay_id_original(transaction_data, payload):
        transaccion = OpenpayTransaccion.objects.filter(openpay_id=candidato).first()
        if transaccion:
            break
    cotizacion = transaccion.cotizacion if transaccion else None
    if cotizacion is None:
        order_id = transaction_data.get('order_id') or payload.get('order_id') or ''
        cotizacion = _resolver_cotizacion_desde_order_id(order_id)
    return transaccion, cotizacion


def _asegurar_pago_reversion_contracargo(contracargo, cotizacion, monto):
    """
    Crea el Pago tipo REEMBOLSO que dispara la póliza de reversión, una sola vez.

    `_contracargo_reversion` es una bandera transitoria (no un campo del
    modelo): el signal de `comunicacion` que decide si notificar al cliente
    corre en el mismo `.save()`, ANTES de que este Pago quede enlazado al
    Contracargo (eso pasa después, cuando el propio Contracargo se guarda) —
    así que la supresión no puede depender de esa relación inversa todavía
    inexistente. Marcar la instancia en memoria sí llega a tiempo al signal,
    porque es el mismo objeto Python.
    """
    if contracargo.pago_reversion_id or cotizacion is None or monto is None:
        return
    pago = Pago(
        cotizacion=cotizacion, tipo='REEMBOLSO', concepto='VENTA',
        monto=monto, metodo='OTRO',
        referencia=f"Contracargo {contracargo.openpay_id}",
        notas="Reversión automática: contracargo reportado por Openpay.",
    )
    pago._contracargo_reversion = True
    pago.save()
    contracargo.pago_reversion = pago


def _asegurar_pago_reactivacion_contracargo(contracargo):
    """Crea el Pago tipo INGRESO que dispara la póliza de reactivación, si se ganó la disputa."""
    if contracargo.pago_reactivacion_id or not contracargo.pago_reversion_id:
        return
    reversion = contracargo.pago_reversion
    pago = Pago(
        cotizacion=reversion.cotizacion, tipo='INGRESO', concepto='VENTA',
        monto=reversion.monto, metodo='OTRO',
        referencia=f"Contracargo {contracargo.openpay_id}",
        notas="Reactivación automática: contracargo resuelto a favor del comercio.",
    )
    pago._contracargo_reactivacion = True
    pago.save()
    contracargo.pago_reactivacion = pago


def _alertar_equipo_contracargo(contracargo):
    """Nunca debe tumbar el webhook: cualquier fallo solo se loggea."""
    try:
        from comunicacion.services_notificaciones import alertar_equipo_contracargo
        alertar_equipo_contracargo(contracargo)
    except Exception:
        logger.exception(
            "No se pudo enviar la alerta interna del contracargo %s.",
            contracargo.openpay_id,
        )


def _armar_evidencia_contracargo(contracargo):
    """
    Arma el PDF de evidencia (Issue #305) al entrar en disputa, para que ya
    esté listo cuando alguien le dé clic a "Enviar evidencia a Openpay" en el
    admin — o para el envío automático de última instancia si nadie lo hace a
    tiempo. Nunca debe tumbar el webhook: cualquier fallo solo se loggea.
    """
    try:
        from .services_evidencia_contracargo import armar_evidencia
        armar_evidencia(contracargo)
    except Exception:
        logger.exception(
            "No se pudo armar la evidencia del contracargo %s.",
            contracargo.openpay_id,
        )


def procesar_webhook_contracargo(payload: dict):
    """
    Crea/actualiza el Contracargo correspondiente a una notificación
    chargeback.* y aplica el efecto en saldo/contabilidad reutilizando el
    mecanismo ya existente de Pago (ver docstring de Contracargo en
    models.py). Nunca lanza: cualquier fallo queda registrado, la vista
    siempre debe poder regresar 200 OK a Openpay.

    Idempotente frente a reintentos de Openpay: solo dispara la alerta
    interna cuando el estado realmente cambia (registro nuevo o transición
    de estado), no en cada reentrega del mismo evento.
    """
    from django.utils import timezone

    event_type = payload.get('type', '')
    transaction_data = payload.get('transaction', payload)
    if not isinstance(transaction_data, dict):
        return None

    chargeback_id = transaction_data.get('id') or payload.get('id')
    if not chargeback_id:
        return None

    estado_nuevo = _EVENTO_CONTRACARGO_A_ESTADO.get(event_type)
    if estado_nuevo is None:
        logger.warning("Contracargo %s: event_type no reconocido %r", chargeback_id, event_type)

    contracargo = Contracargo.objects.filter(openpay_id=str(chargeback_id)).first()
    es_nuevo = contracargo is None
    if contracargo is None:
        contracargo = Contracargo(openpay_id=str(chargeback_id))
    estado_previo = contracargo.estado if not es_nuevo else None

    monto = _decimal_o_none(transaction_data.get('amount'))

    try:
        with transaction.atomic():
            if es_nuevo:
                transaccion_original, cotizacion = _resolver_contexto_contracargo(transaction_data, payload)
                contracargo.transaccion_openpay = transaccion_original
                contracargo.cotizacion = cotizacion
                contracargo.requiere_vinculacion_manual = cotizacion is None

            contracargo.event_type = event_type
            if estado_nuevo:
                contracargo.estado = estado_nuevo
            if monto is not None:
                contracargo.monto = monto
            contracargo.motivo = (
                transaction_data.get('reason') or transaction_data.get('description') or contracargo.motivo
            )
            contracargo.payload_crudo = payload

            if contracargo.estado == 'EN_DISPUTA' and not contracargo.fecha_limite_evidencia:
                contracargo.fecha_limite_evidencia = _sumar_dias_habiles(timezone.localdate(), 3)
            if contracargo.estado in ('EN_DISPUTA', 'PERDIDO'):
                _asegurar_pago_reversion_contracargo(contracargo, contracargo.cotizacion, contracargo.monto)
            if contracargo.estado == 'GANADO':
                _asegurar_pago_reactivacion_contracargo(contracargo)
            if contracargo.estado in ('GANADO', 'PERDIDO') and not contracargo.fecha_resolucion:
                contracargo.fecha_resolucion = timezone.now()

            contracargo.save()
    except Exception as e:
        logger.exception("Error procesando contracargo %s (%s)", chargeback_id, event_type)
        contracargo.notas = f"{contracargo.notas}\nError al procesar: {e}".strip()
        try:
            contracargo.save()
        except Exception:
            logger.exception("No se pudo ni siquiera guardar el contracargo %s tras el error.", chargeback_id)
        return contracargo

    if es_nuevo or contracargo.estado != estado_previo:
        # Diferida a on_commit, igual que el resto de las comunicaciones del
        # repo: no se avisa de un contracargo cuya transacción (Pago/póliza)
        # todavía puede revertirse.
        transaction.on_commit(lambda: _alertar_equipo_contracargo(contracargo))
        if contracargo.estado == 'EN_DISPUTA':
            # Issue #305: se arma sola, lista para el envío manual con un
            # clic o el automático de última instancia — no hace falta
            # esperar a que alguien entre al admin para empezar a juntarla.
            transaction.on_commit(lambda: _armar_evidencia_contracargo(contracargo))
    return contracargo
