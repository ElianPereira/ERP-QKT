"""
Signals del Módulo de Contabilidad
==================================
Genera pólizas automáticas cuando se registran operaciones en otros módulos.

Para desactivar temporalmente (migraciones masivas):
    settings.CONTABILIDAD_SIGNALS_ENABLED = False

Mejoras v2.0:
- Desglose de IVA trasladado en pagos de clientes
- Registro de retenciones ISR de clientes morales
- Mapeo de categorías de gasto a cuentas específicas
"""
import logging
from decimal import Decimal, ROUND_HALF_UP
from core_erp import impuestos
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType

logger = logging.getLogger(__name__)

from .models import (
    Poliza, MovimientoContable, ConfiguracionContable, UnidadNegocio
)


def signals_enabled():
    """Verifica si los signals están habilitados."""
    return getattr(settings, 'CONTABILIDAD_SIGNALS_ENABLED', True)


def get_usuario_sistema():
    """Obtiene o crea el usuario del sistema para pólizas automáticas."""
    usuario, _ = User.objects.get_or_create(
        username='sistema_contable',
        defaults={
            'first_name': 'Sistema',
            'last_name': 'Contable',
            'is_active': False,
        }
    )
    return usuario


def get_cuenta(operacion):
    """Obtiene la cuenta configurada para una operación."""
    try:
        config = ConfiguracionContable.objects.get(operacion=operacion, activa=True)
        return config.cuenta
    except ConfiguracionContable.DoesNotExist:
        return None


def get_unidad_negocio(clave):
    """Obtiene una unidad de negocio por clave."""
    try:
        return UnidadNegocio.objects.get(clave=clave)
    except UnidadNegocio.DoesNotExist:
        # Fallback: primera unidad activa
        return UnidadNegocio.objects.filter(activa=True).first()


from comercial.services import calcular_desglose_proporcional  # noqa: F401 — shared fiscal logic


# ==========================================
# SIGNAL: PAGO DE CLIENTE (comercial.Pago)
# ==========================================
@receiver(post_save, sender='comercial.Pago')
def crear_poliza_pago_cliente(sender, instance, created, **kwargs):
    """
    Genera póliza de ingreso cuando se registra un pago de cliente.
    
    Asiento con desglose de IVA (ejemplo pago $11,600 de cotización persona física):
        DEBE: Bancos/Caja                  $11,600.00
        HABER: Anticipo clientes           $10,000.00 (subtotal proporcional)
        HABER: IVA trasladado               $1,600.00 (16% proporcional)
    
    Asiento cliente MORAL (ejemplo pago $11,475 con retención ISR 1.25%):
        DEBE: Bancos/Caja                  $11,475.00
        DEBE: ISR retenido por cliente        $125.00 (impuesto a favor)
        HABER: Anticipo clientes           $10,000.00
        HABER: IVA trasladado               $1,600.00
    """
    if not signals_enabled() or not created:
        return

    pago = instance
    cotizacion = pago.cotizacion
    monto = Decimal(str(pago.monto))

    # Si es reembolso, generar póliza inversa de egreso
    if getattr(pago, 'tipo', 'INGRESO') == 'REEMBOLSO':
        crear_poliza_reembolso_cliente(pago)
        return

    # Ingreso EXTRA (propina, comisión, etc.): no es parte del precio de la
    # venta, así que se registra aparte contra "Otros ingresos" en vez de
    # mezclarse con el desglose de IVA/anticipo de la cotización.
    if getattr(pago, 'concepto', 'VENTA') == 'EXTRA':
        crear_poliza_ingreso_extra(pago)
        return

    # ─── Determinar cuenta de cargo según método de pago ────────
    if pago.metodo == 'EFECTIVO':
        cuenta_cargo = get_cuenta('CAJA')
    elif pago.metodo in ('TRANSFERENCIA', 'DEPOSITO'):
        cuenta_cargo = get_cuenta('BANCO_PRINCIPAL')
    elif pago.metodo in ('TARJETA_CREDITO', 'TARJETA_DEBITO'):
        cuenta_cargo = get_cuenta('BANCO_PRINCIPAL')
    else:
        cuenta_cargo = get_cuenta('BANCO_PRINCIPAL')

    # ─── Determinar cuenta de abono principal ───────────────────
    # Anticipo si el evento no se ha ejecutado, Ingreso si ya se ejecutó
    if cotizacion.estado in ('EJECUTADA', 'CERRADA'):
        cuenta_abono = get_cuenta('INGRESO_EVENTOS')
    else:
        cuenta_abono = get_cuenta('ANTICIPO_CLIENTES')

    # ─── Obtener cuentas de impuestos ───────────────────────────
    cuenta_iva_trasladado = get_cuenta('IVA_TRASLADADO')
    cuenta_isr_retenido = get_cuenta('ISR_RETENIDO_CLIENTES')

    # Validar que existan las cuentas mínimas
    if not cuenta_cargo or not cuenta_abono:
        logger.warning(
            "Póliza NO generada para Pago #%s: falta configuración contable "
            "(cuenta_cargo=%s, cuenta_abono=%s)", pago.pk, cuenta_cargo, cuenta_abono
        )
        return

    # ─── Obtener unidad de negocio ──────────────────────────────
    unidad = get_unidad_negocio('QUINTA')
    if not unidad:
        logger.warning("Póliza NO generada para Pago #%s: falta UnidadNegocio 'QUINTA'", pago.pk)
        return

    # ─── Calcular desglose proporcional ─────────────────────────
    desglose = calcular_desglose_proporcional(monto, cotizacion)
    subtotal = desglose['subtotal']
    iva = desglose['iva']
    retencion_isr = desglose['retencion_isr']
    retencion_iva = desglose['retencion_iva']

    # ─── Crear póliza ───────────────────────────────────────────
    usuario = get_usuario_sistema()
    content_type = ContentType.objects.get_for_model(pago)

    poliza = Poliza.objects.create(
        tipo='I',
        folio=Poliza.siguiente_folio('I', pago.fecha_pago),
        fecha=pago.fecha_pago,
        concepto=f"Pago cliente: {cotizacion.cliente.nombre} - {cotizacion.nombre_evento}",
        unidad_negocio=unidad,
        estado='APLICADA',
        origen='PAGO_CLIENTE',
        content_type=content_type,
        object_id=pago.pk,
        created_by=usuario,
    )

    # ─── DEBE: Bancos/Caja (monto neto recibido) ────────────────
    MovimientoContable.objects.create(
        poliza=poliza,
        cuenta=cuenta_cargo,
        debe=monto,
        haber=Decimal('0.00'),
        concepto=f"Pago {pago.get_metodo_display()}",
        referencia=pago.referencia or '',
    )

    # ─── DEBE: ISR retenido por cliente (si aplica) ─────────────
    # Cuando cliente MORAL retiene ISR, es un impuesto a FAVOR de QKT
    if cuenta_isr_retenido and retencion_isr > 0:
        MovimientoContable.objects.create(
            poliza=poliza,
            cuenta=cuenta_isr_retenido,
            debe=retencion_isr,
            haber=Decimal('0.00'),
            concepto="ISR retenido por cliente (1.25%)",
            referencia=f"COT-{cotizacion.pk:03d}",
        )

    # ─── HABER: Anticipo/Ingreso (subtotal sin IVA) ─────────────
    MovimientoContable.objects.create(
        poliza=poliza,
        cuenta=cuenta_abono,
        debe=Decimal('0.00'),
        haber=subtotal,
        concepto=f"COT-{cotizacion.pk:03d} (Subtotal)",
        referencia=pago.referencia or '',
    )

    # ─── HABER: IVA trasladado ──────────────────────────────────
    if cuenta_iva_trasladado and iva > 0:
        MovimientoContable.objects.create(
            poliza=poliza,
            cuenta=cuenta_iva_trasladado,
            debe=Decimal('0.00'),
            haber=iva,
            concepto="IVA trasladado 16%",
            referencia=f"COT-{cotizacion.pk:03d}",
        )
    elif iva > 0:
        # Fallback: si no hay cuenta IVA configurada, incluir en ingreso
        MovimientoContable.objects.create(
            poliza=poliza,
            cuenta=cuenta_abono,
            debe=Decimal('0.00'),
            haber=iva,
            concepto=f"COT-{cotizacion.pk:03d} (IVA incluido)",
            referencia=pago.referencia or '',
        )

    # ─── Comisión de terminal (TPV), si se capturó ──────────────
    if pago.metodo in ('TARJETA_CREDITO', 'TARJETA_DEBITO') and pago.comision_tpv:
        crear_poliza_comision_tpv(pago)


# ==========================================
# COMISIÓN DE TERMINAL (TPV) — pago con tarjeta física, BBVA/Netpay
# ==========================================
def crear_poliza_comision_tpv(pago):
    """
    Póliza del descuento que BBVA/Netpay aplica a cada venta cobrada con la
    terminal física. El banco nunca lo desglosa en el estado de cuenta —
    solo deposita el neto — así que se captura a mano en Pago.comision_tpv
    y de ahí se genera este gasto financiero, con la misma unidad_negocio de
    la venta que lo originó (mismo criterio que la comisión de Openpay).

    El monto capturado se asume con IVA incluido (así lo descuenta el banco
    del depósito), igual que en el manual de contabilidad 7.2:
        DEBE: Comisiones bancarias    comisión neta
        DEBE: IVA acreditable         IVA de la comisión
        HABER: Banco principal        comisión total (neto + IVA)

    Idempotente: una sola póliza por Pago aunque el signal se dispare de más.
    """
    if not signals_enabled():
        return None

    comision_total = pago.comision_tpv
    if not comision_total or comision_total <= 0:
        return None

    content_type = ContentType.objects.get_for_model(pago)
    if Poliza.objects.filter(content_type=content_type, object_id=pago.pk, origen='COMISION_TPV').exists():
        return None  # idempotencia

    cuenta_gasto = get_cuenta('GASTO_BANCARIOS')
    cuenta_iva = get_cuenta('IVA_ACREDITABLE')
    cuenta_banco = get_cuenta('BANCO_PRINCIPAL')
    if not cuenta_gasto or not cuenta_banco:
        logger.warning(
            "Comisión TPV no registrada para Pago #%s: falta configuración contable "
            "(GASTO_BANCARIOS=%s, BANCO_PRINCIPAL=%s)",
            pago.pk, cuenta_gasto, cuenta_banco,
        )
        return None

    unidad = get_unidad_negocio('QUINTA')
    if not unidad:
        logger.warning("Comisión TPV no registrada para Pago #%s: falta UnidadNegocio 'QUINTA'", pago.pk)
        return None

    comision_total = Decimal(str(comision_total)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if cuenta_iva:
        comision_neta = impuestos.sin_iva(comision_total)
        iva = comision_total - comision_neta
    else:
        comision_neta = comision_total
        iva = Decimal('0.00')

    poliza = Poliza.objects.create(
        tipo='E',
        folio=Poliza.siguiente_folio('E', pago.fecha_pago),
        fecha=pago.fecha_pago,
        concepto=f"Comisión terminal BBVA — COT-{pago.cotizacion_id:03d} ({pago.cotizacion.cliente.nombre})",
        unidad_negocio=unidad,
        estado='APLICADA',
        origen='COMISION_TPV',
        content_type=content_type,
        object_id=pago.pk,
        created_by=get_usuario_sistema(),
    )

    MovimientoContable.objects.create(
        poliza=poliza,
        cuenta=cuenta_gasto,
        debe=comision_neta,
        haber=Decimal('0.00'),
        concepto="Comisión terminal TPV",
        referencia=f"COT-{pago.cotizacion_id:03d}",
    )
    if cuenta_iva and iva > 0:
        MovimientoContable.objects.create(
            poliza=poliza,
            cuenta=cuenta_iva,
            debe=iva,
            haber=Decimal('0.00'),
            concepto="IVA acreditable comisión TPV",
            referencia=f"COT-{pago.cotizacion_id:03d}",
        )
    elif iva > 0:
        MovimientoContable.objects.create(
            poliza=poliza,
            cuenta=cuenta_gasto,
            debe=iva,
            haber=Decimal('0.00'),
            concepto="IVA comisión TPV (incluido en gasto)",
            referencia=f"COT-{pago.cotizacion_id:03d}",
        )

    MovimientoContable.objects.create(
        poliza=poliza,
        cuenta=cuenta_banco,
        debe=Decimal('0.00'),
        haber=comision_total,
        concepto="Comisión TPV descontada del depósito",
        referencia=f"COT-{pago.cotizacion_id:03d}",
    )

    return poliza


# ==========================================
# INGRESO EXTRA LIGADO A CLIENTE/COTIZACIÓN
# (propina, comisión, etc. — no es parte del precio de la venta)
# ==========================================
def crear_poliza_ingreso_extra(pago):
    """
    Genera póliza de ingreso simple para un Pago con concepto=EXTRA: no se
    desglosa IVA/anticipo (no es parte del precio de la cotización), solo
    se registra el monto completo contra "Otros ingresos", conservando la
    referencia al cliente/cotización para saber de dónde provino.

    Asiento:
        DEBE: Bancos/Caja        (monto completo)
        HABER: Otros ingresos    (monto completo)
    """
    cotizacion = pago.cotizacion
    monto = Decimal(str(pago.monto))

    if pago.metodo == 'EFECTIVO':
        cuenta_cargo = get_cuenta('CAJA')
    else:
        cuenta_cargo = get_cuenta('BANCO_PRINCIPAL')
    cuenta_abono = get_cuenta('OTROS_INGRESOS_CLIENTE')

    if not cuenta_cargo or not cuenta_abono:
        logger.warning(
            "Póliza NO generada para Pago (EXTRA) #%s: falta configuración contable "
            "(cuenta_cargo=%s, cuenta_abono=%s)", pago.pk, cuenta_cargo, cuenta_abono
        )
        return

    unidad = get_unidad_negocio('QUINTA')
    if not unidad:
        logger.warning("Póliza NO generada para Pago (EXTRA) #%s: falta UnidadNegocio 'QUINTA'", pago.pk)
        return

    usuario = get_usuario_sistema()
    content_type = ContentType.objects.get_for_model(pago)

    poliza = Poliza.objects.create(
        tipo='I',
        folio=Poliza.siguiente_folio('I', pago.fecha_pago),
        fecha=pago.fecha_pago,
        concepto=f"Ingreso adicional: {cotizacion.cliente.nombre} - {cotizacion.nombre_evento} (COT-{cotizacion.pk:03d})",
        unidad_negocio=unidad,
        estado='APLICADA',
        origen='PAGO_CLIENTE',
        content_type=content_type,
        object_id=pago.pk,
        created_by=usuario,
    )

    MovimientoContable.objects.create(
        poliza=poliza, cuenta=cuenta_cargo,
        debe=monto, haber=Decimal('0.00'),
        concepto=f"Ingreso adicional — {pago.get_metodo_display()}",
        referencia=pago.referencia or '',
    )
    MovimientoContable.objects.create(
        poliza=poliza, cuenta=cuenta_abono,
        debe=Decimal('0.00'), haber=monto,
        concepto=f"COT-{cotizacion.pk:03d} — ingreso adicional",
        referencia=pago.referencia or '',
    )


# ==========================================
# COMISIÓN OPENPAY (automática, con el fee que reporta Openpay)
# ==========================================
def crear_poliza_comision_openpay(transaccion, fee):
    """
    Genera la póliza de la comisión que Openpay cobra por un cargo, con el
    desglose exacto que Openpay reporta en el campo `fee` de la transacción.
    Mismo asiento que la comisión de terminal (manual de contabilidad 7.2):

        DEBE: Comisiones bancarias    fee.amount
        DEBE: IVA acreditable         fee.tax
        HABER: Banco principal        fee.amount + fee.tax

    Idempotente: una sola póliza por OpenpayTransaccion aunque el webhook
    llegue repetido. Si faltan cuentas configuradas se omite con warning,
    sin afectar el registro del Pago.
    """
    if not signals_enabled():
        return None

    fee = fee or {}
    try:
        comision = Decimal(str(fee.get('amount') or 0))
        iva = Decimal(str(fee.get('tax') or 0))
    except Exception:
        logger.warning("Comisión Openpay no registrada para %s: fee ilegible %r", transaccion.openpay_id, fee)
        return None

    if comision <= 0:
        return None

    content_type = ContentType.objects.get_for_model(transaccion)
    if Poliza.objects.filter(
        content_type=content_type, object_id=transaccion.pk, origen='COMISION_OPENPAY'
    ).exists():
        return None  # ya registrada (idempotencia ante reintentos del webhook)

    cuenta_gasto = get_cuenta('GASTO_BANCARIOS')
    cuenta_iva = get_cuenta('IVA_ACREDITABLE')
    cuenta_banco = get_cuenta('BANCO_PRINCIPAL')
    if not cuenta_gasto or not cuenta_banco:
        logger.warning(
            "Comisión Openpay no registrada para %s: falta configuración contable "
            "(GASTO_BANCARIOS=%s, BANCO_PRINCIPAL=%s)",
            transaccion.openpay_id, cuenta_gasto, cuenta_banco,
        )
        return None

    unidad = get_unidad_negocio('QUINTA')
    if not unidad:
        logger.warning("Comisión Openpay no registrada para %s: falta UnidadNegocio 'QUINTA'", transaccion.openpay_id)
        return None

    fecha = transaccion.pago.fecha_pago if transaccion.pago else transaccion.created_at.date()

    poliza = Poliza.objects.create(
        tipo='E',
        folio=Poliza.siguiente_folio('E', fecha),
        fecha=fecha,
        concepto=f"Comisión Openpay {transaccion.metodo or ''} — {transaccion.openpay_id}".strip(),
        unidad_negocio=unidad,
        estado='APLICADA',
        origen='COMISION_OPENPAY',
        content_type=content_type,
        object_id=transaccion.pk,
        created_by=get_usuario_sistema(),
    )

    MovimientoContable.objects.create(
        poliza=poliza,
        cuenta=cuenta_gasto,
        debe=comision,
        haber=Decimal('0.00'),
        concepto="Comisión Openpay",
        referencia=transaccion.openpay_id,
    )
    if cuenta_iva and iva > 0:
        MovimientoContable.objects.create(
            poliza=poliza,
            cuenta=cuenta_iva,
            debe=iva,
            haber=Decimal('0.00'),
            concepto="IVA acreditable comisión Openpay",
            referencia=transaccion.openpay_id,
        )
    elif iva > 0:
        # Sin cuenta de IVA acreditable configurada: se incluye en el gasto
        MovimientoContable.objects.create(
            poliza=poliza,
            cuenta=cuenta_gasto,
            debe=iva,
            haber=Decimal('0.00'),
            concepto="IVA comisión Openpay (incluido en gasto)",
            referencia=transaccion.openpay_id,
        )

    MovimientoContable.objects.create(
        poliza=poliza,
        cuenta=cuenta_banco,
        debe=Decimal('0.00'),
        haber=comision + iva,
        concepto="Comisión Openpay descontada del depósito",
        referencia=transaccion.openpay_id,
    )

    return poliza


# ==========================================
# REEMBOLSO A CLIENTE (póliza inversa)
# ==========================================
def crear_poliza_reembolso_cliente(pago):
    """
    Genera póliza de egreso al reembolsar a un cliente.

    Asiento (inverso al pago original):
        DEBE: Anticipo clientes      (subtotal)
        DEBE: IVA trasladado         (iva)
        HABER: ISR retenido clientes (si aplica)
        HABER: Bancos/Caja           (monto neto devuelto)
    """
    cotizacion = pago.cotizacion
    monto = Decimal(str(pago.monto))

    if pago.metodo == 'EFECTIVO':
        cuenta_banco = get_cuenta('CAJA')
    else:
        cuenta_banco = get_cuenta('BANCO_PRINCIPAL')

    cuenta_anticipo = get_cuenta('ANTICIPO_CLIENTES')
    cuenta_iva = get_cuenta('IVA_TRASLADADO')
    cuenta_isr_ret = get_cuenta('ISR_RETENIDO_CLIENTES')

    if not cuenta_banco or not cuenta_anticipo:
        logger.warning(
            "Póliza NO generada para Reembolso #%s: falta configuración contable", pago.pk
        )
        return

    unidad = get_unidad_negocio('QUINTA')
    if not unidad:
        logger.warning("Póliza NO generada para Reembolso #%s: falta UnidadNegocio", pago.pk)
        return

    desglose = calcular_desglose_proporcional(monto, cotizacion)
    subtotal = desglose['subtotal']
    iva = desglose['iva']
    retencion_isr = desglose['retencion_isr']

    usuario = get_usuario_sistema()
    content_type = ContentType.objects.get_for_model(pago)

    poliza = Poliza.objects.create(
        tipo='E',
        folio=Poliza.siguiente_folio('E', pago.fecha_pago),
        fecha=pago.fecha_pago,
        concepto=f"Reembolso cliente: {cotizacion.cliente.nombre} - {cotizacion.nombre_evento}",
        unidad_negocio=unidad,
        estado='APLICADA',
        origen='PAGO_CLIENTE',
        content_type=content_type,
        object_id=pago.pk,
        created_by=usuario,
    )

    MovimientoContable.objects.create(
        poliza=poliza, cuenta=cuenta_anticipo,
        debe=subtotal, haber=Decimal('0.00'),
        concepto=f"Reembolso COT-{cotizacion.pk:03d} (Subtotal)",
        referencia=pago.referencia or '',
    )
    if cuenta_iva and iva > 0:
        MovimientoContable.objects.create(
            poliza=poliza, cuenta=cuenta_iva,
            debe=iva, haber=Decimal('0.00'),
            concepto="Reverso IVA trasladado 16%",
            referencia=f"COT-{cotizacion.pk:03d}",
        )
    if cuenta_isr_ret and retencion_isr > 0:
        MovimientoContable.objects.create(
            poliza=poliza, cuenta=cuenta_isr_ret,
            debe=Decimal('0.00'), haber=retencion_isr,
            concepto="Reverso ISR retenido por cliente",
            referencia=f"COT-{cotizacion.pk:03d}",
        )
    MovimientoContable.objects.create(
        poliza=poliza, cuenta=cuenta_banco,
        debe=Decimal('0.00'), haber=monto,
        concepto=f"Devolución {pago.get_metodo_display()}",
        referencia=pago.referencia or '',
    )


# ==========================================
# REVERSIÓN POR CANCELACIÓN DE COTIZACIÓN
# ==========================================
def crear_polizas_reversion_cancelacion(cotizacion, usuario=None, motivo=''):
    """
    Al cancelar una cotización, crea pólizas de reversión por cada póliza
    APLICADA originada por sus pagos. Cada nueva póliza invierte los movimientos
    DEBE↔HABER y queda marcada como APLICADA con origen='AJUSTE'.

    No modifica ni elimina las pólizas originales (auditoría preservada).
    Idempotente: omite pagos cuya reversión ya existe.
    """
    if not signals_enabled():
        return

    from comercial.models import Pago
    pagos = cotizacion.pagos.all()
    if not pagos.exists():
        return

    usuario = usuario or get_usuario_sistema()
    pago_ct = ContentType.objects.get_for_model(Pago)

    polizas_originales = Poliza.objects.filter(
        origen='PAGO_CLIENTE',
        content_type=pago_ct,
        object_id__in=pagos.values_list('pk', flat=True),
        estado='APLICADA',
    ).prefetch_related('movimientos')

    for original in polizas_originales:
        # Idempotencia: evitar reversiones duplicadas
        ya_revertida = Poliza.objects.filter(
            origen='AJUSTE',
            content_type=pago_ct,
            object_id=original.object_id,
            concepto__startswith=f"Reversión cancelación COT-{cotizacion.pk:03d}",
        ).exists()
        if ya_revertida:
            continue

        reversion = Poliza.objects.create(
            tipo='D',
            folio=Poliza.siguiente_folio('D', cotizacion.fecha_cancelacion.date() if cotizacion.fecha_cancelacion else original.fecha),
            fecha=cotizacion.fecha_cancelacion.date() if cotizacion.fecha_cancelacion else original.fecha,
            concepto=f"Reversión cancelación COT-{cotizacion.pk:03d}: {motivo or 'Cancelada'}"[:500],
            unidad_negocio=original.unidad_negocio,
            estado='APLICADA',
            origen='AJUSTE',
            content_type=pago_ct,
            object_id=original.object_id,
            created_by=usuario,
        )
        for mov in original.movimientos.all():
            MovimientoContable.objects.create(
                poliza=reversion,
                cuenta=mov.cuenta,
                debe=mov.haber,
                haber=mov.debe,
                concepto=f"Reversión: {mov.concepto}"[:255],
                referencia=mov.referencia,
            )


# ==========================================
# SIGNAL: PAGO AIRBNB (airbnb.PagoAirbnb)
# ==========================================
def _cuenta_si_hay_importe(operacion, importe, faltantes):
    """
    Cuenta configurada para `operacion`, solo si el importe la necesita.

    Si el importe es cero la línea no se asienta y la cuenta no hace falta.
    Si no lo es y la cuenta no está configurada, se anota en `faltantes`:
    omitir la línea descuadraría el asiento.
    """
    if importe <= 0:
        return None
    cuenta = get_cuenta(operacion)
    if cuenta is None:
        faltantes.append(operacion)
    return cuenta


def _asiento_pago_airbnb(pago):
    """
    Líneas del asiento de un pago de Airbnb, como tuplas
    `(cuenta, debe, haber, concepto, referencia)`.

    Ejemplo (base $10,000, IVA trasladado $1,600, comisión $420):

        DEBE : Bancos                   $10,780   (lo que Airbnb deposita)
        DEBE : Retención ISR               $400
        DEBE : Retención IVA               $800
        DEBE : Comisión Airbnb             $420
        HABER: Ingreso Airbnb           $10,000
        HABER: IVA trasladado            $1,600

    El IVA trasladado va al HABER porque Airbnb lo cobra al huésped y lo
    transfiere al anfitrión para que sea él quien lo entere: es un pasivo,
    no un ingreso. Sin esa línea el asiento no cuadra —el depósito sí la
    incluye—, que es como se venía generando antes de que el modelo
    distinguiera el IVA trasladado de las retenciones.

    El impuesto al hospedaje no aparece: lo retiene y entera Airbnb por su
    cuenta, nunca pasa por la cuenta bancaria.

    Devuelve `None` si falta alguna cuenta indispensable.
    """
    faltantes = []

    cuenta_banco = get_cuenta('BANCO_PRINCIPAL')
    if cuenta_banco is None:
        faltantes.append('BANCO_PRINCIPAL')
    cuenta_ingreso = get_cuenta('INGRESO_AIRBNB')
    if cuenta_ingreso is None:
        faltantes.append('INGRESO_AIRBNB')

    cuenta_ret_isr = _cuenta_si_hay_importe(
        'RETENCION_ISR_AIRBNB', pago.retencion_isr, faltantes)
    cuenta_ret_iva = _cuenta_si_hay_importe(
        'RETENCION_IVA_AIRBNB', pago.retencion_iva, faltantes)
    cuenta_comision = _cuenta_si_hay_importe(
        'COMISION_AIRBNB', pago.comision_airbnb, faltantes)
    cuenta_iva_trasladado = _cuenta_si_hay_importe(
        'IVA_TRASLADADO', pago.iva_trasladado, faltantes)

    if faltantes:
        logger.warning(
            "Póliza NO generada para PagoAirbnb #%s: falta configuración "
            "contable (%s)", pago.pk, ', '.join(faltantes)
        )
        return None

    referencia = pago.codigo_confirmacion or ''
    lineas = []

    if pago.monto_neto != 0:
        lineas.append((cuenta_banco, pago.monto_neto, Decimal('0.00'),
                       "Depósito Airbnb", referencia))
    if cuenta_ret_isr:
        lineas.append((cuenta_ret_isr, pago.retencion_isr, Decimal('0.00'),
                       "ISR retenido por la plataforma", referencia))
    if cuenta_ret_iva:
        lineas.append((cuenta_ret_iva, pago.retencion_iva, Decimal('0.00'),
                       "IVA retenido por la plataforma", referencia))
    if cuenta_comision:
        lineas.append((cuenta_comision, pago.comision_airbnb, Decimal('0.00'),
                       "Comisión plataforma", referencia))

    lineas.append((cuenta_ingreso, Decimal('0.00'), pago.monto_bruto,
                   f"{pago.noches} noches hospedaje", referencia))
    if cuenta_iva_trasladado:
        lineas.append((cuenta_iva_trasladado, Decimal('0.00'),
                       pago.iva_trasladado, "IVA trasladado a enterar",
                       referencia))

    return lineas


def _estado_segun_cuadre(lineas, pago):
    """
    'APLICADA' si el asiento cuadra; 'BORRADOR' si no, con aviso en el log.

    Un pago cuyo neto no coincide con la fórmula (`diferencia_neto`) produce
    un asiento descuadrado. Dejarlo en borrador lo saca de los reportes y lo
    pone a la vista para revisión, en vez de aplicar cifras que no cuadran.
    """
    diferencia = (sum(linea[1] for linea in lineas)
                  - sum(linea[2] for linea in lineas))
    if diferencia == 0:
        return 'APLICADA'
    logger.warning(
        "Póliza de PagoAirbnb #%s queda en BORRADOR: el asiento descuadra "
        "por %s (revisa monto_neto contra la fórmula)", pago.pk, diferencia
    )
    return 'BORRADOR'


def _escribir_movimientos(poliza, lineas):
    """Reescribe los movimientos de una póliza a partir de `lineas`."""
    poliza.movimientos.all().delete()
    for cuenta, debe, haber, concepto, referencia in lineas:
        MovimientoContable.objects.create(
            poliza=poliza, cuenta=cuenta, debe=debe, haber=haber,
            concepto=concepto, referencia=referencia,
        )


def _movimientos_actuales(poliza):
    return [
        (mov.cuenta_id, mov.debe, mov.haber, mov.concepto, mov.referencia)
        for mov in poliza.movimientos.all()
    ]


def _marcador_reversion(pago):
    return f"Airbnb #{pago.pk}: reversión"


def _marcador_reactivacion(pago):
    return f"Airbnb #{pago.pk}: reactivación"


def _polizas_de_ajuste(pago, marcador):
    ct = ContentType.objects.get_for_model(pago)
    return Poliza.objects.filter(
        origen='AJUSTE', content_type=ct, object_id=pago.pk,
        concepto__startswith=marcador,
    )


def _esta_revertido(pago):
    """
    True si la última operación sobre el pago fue una reversión.

    Un pago puede ir y volver de PAGADO (se cancela, se reexpide). Cada
    reversión se compensa con su reactivación, así que basta comparar
    cuántas hay de cada una.
    """
    return (_polizas_de_ajuste(pago, _marcador_reversion(pago)).count()
            > _polizas_de_ajuste(pago, _marcador_reactivacion(pago)).count())


def _poliza_espejo(origen, pago, concepto, fecha):
    """Crea una póliza de diario que invierte los movimientos de `origen`."""
    espejo = Poliza.objects.create(
        tipo='D',
        folio=Poliza.siguiente_folio('D', fecha),
        fecha=fecha,
        concepto=concepto[:500],
        unidad_negocio=origen.unidad_negocio,
        estado='APLICADA',
        origen='AJUSTE',
        content_type=origen.content_type,
        object_id=origen.object_id,
        created_by=get_usuario_sistema(),
    )
    for mov in origen.movimientos.all():
        MovimientoContable.objects.create(
            poliza=espejo,
            cuenta=mov.cuenta,
            debe=mov.haber,
            haber=mov.debe,
            concepto=f"Reversión: {mov.concepto}"[:255],
            referencia=mov.referencia,
        )
    return espejo


@receiver(post_save, sender='airbnb.PagoAirbnb')
def sincronizar_poliza_pago_airbnb(sender, instance, created, **kwargs):
    """
    Mantiene la póliza de un pago de Airbnb alineada con el pago.

    Al crear el pago genera la póliza. Al actualizarlo la **regenera en
    sitio**: misma póliza, mismo folio y mismos datos de auditoría, con los
    movimientos reescritos. Reimportar el CSV corrige montos, y antes de
    esto la póliza se quedaba con las cifras viejas —la contabilidad
    declaraba un importe que el pago ya no tenía—.

    También cubre el caso de un pago capturado como PENDIENTE y marcado
    PAGADO después: antes nunca llegaba a generar póliza.

    Si el pago deja de estar PAGADO (cancelado o reembolsado) no se borra ni
    se modifica nada: se emite una póliza de reversión con origen='AJUSTE',
    igual que en la cancelación de cotizaciones. Si vuelve a PAGADO se emite
    la reactivación que compensa esa reversión.
    """
    if not signals_enabled():
        return

    pago = instance
    content_type = ContentType.objects.get_for_model(pago)
    poliza = (
        Poliza.objects
        .filter(content_type=content_type, object_id=pago.pk,
                origen='PAGO_AIRBNB')
        .exclude(estado='CANCELADA')
        .order_by('-pk')
        .first()
    )

    if pago.estado != 'PAGADO':
        if poliza is not None and not _esta_revertido(pago):
            _poliza_espejo(
                poliza, pago,
                f"{_marcador_reversion(pago)} por "
                f"{pago.get_estado_display().lower()}",
                pago.fecha_pago or poliza.fecha,
            )
        return

    lineas = _asiento_pago_airbnb(pago)
    if lineas is None:
        return

    fecha_poliza = pago.fecha_pago or pago.fecha_checkin
    concepto = (f"Airbnb: {pago.huesped} "
                f"({pago.codigo_confirmacion or 'Sin código'})")
    estado = _estado_segun_cuadre(lineas, pago)

    if poliza is None:
        unidad = get_unidad_negocio('AIRBNB')
        if not unidad:
            logger.warning(
                "Póliza NO generada para PagoAirbnb #%s: falta UnidadNegocio "
                "'AIRBNB'", pago.pk
            )
            return
        poliza = Poliza.objects.create(
            tipo='I',
            folio=Poliza.siguiente_folio('I', fecha_poliza),
            fecha=fecha_poliza,
            concepto=concepto[:500],
            unidad_negocio=unidad,
            estado=estado,
            origen='PAGO_AIRBNB',
            content_type=content_type,
            object_id=pago.pk,
            created_by=get_usuario_sistema(),
        )
        _escribir_movimientos(poliza, lineas)
        return

    # El pago volvió a PAGADO tras una reversión: se compensa antes de
    # regenerar, para que la reversión no siga restando el ingreso.
    if _esta_revertido(pago):
        reversion = _polizas_de_ajuste(
            pago, _marcador_reversion(pago)).order_by('-pk').first()
        _poliza_espejo(
            reversion, pago,
            f"{_marcador_reactivacion(pago)} tras "
            f"{reversion.concepto.split('por ')[-1]}",
            pago.fecha_pago or reversion.fecha,
        )

    deseados = [(cuenta.pk, debe, haber, concepto_mov, referencia)
                for cuenta, debe, haber, concepto_mov, referencia in lineas]
    sin_cambios = (
        poliza.fecha == fecha_poliza
        and poliza.concepto == concepto[:500]
        and poliza.estado == estado
        and _movimientos_actuales(poliza) == deseados
    )
    if sin_cambios:
        return

    if (poliza.fecha.year, poliza.fecha.month) != (fecha_poliza.year,
                                                   fecha_poliza.month):
        poliza.folio = Poliza.siguiente_folio('I', fecha_poliza)
    poliza.fecha = fecha_poliza
    poliza.concepto = concepto[:500]
    poliza.estado = estado
    poliza.save(update_fields=['fecha', 'folio', 'concepto', 'estado',
                               'updated_at'])
    _escribir_movimientos(poliza, lineas)
    logger.info(
        "Póliza %s regenerada por actualización de PagoAirbnb #%s",
        poliza.pk, pago.pk
    )


# ==========================================
# MAPEO DE CATEGORÍAS DE GASTO A CUENTAS
# ==========================================
MAPEO_CATEGORIA_CUENTA = {
    # Categoría de gasto -> Operación de ConfiguracionContable
    'INSUMOS': 'GASTO_INSUMOS',
    'BEBIDAS': 'GASTO_INSUMOS',
    'LICOR': 'GASTO_INSUMOS',
    'HIELO': 'GASTO_INSUMOS',
    'SERVICIOS': 'GASTO_SERVICIOS',
    'AGUA': 'GASTO_SERVICIOS',
    'LUZ': 'GASTO_SERVICIOS',
    'INTERNET': 'GASTO_SERVICIOS',
    'TELEFONO': 'GASTO_SERVICIOS',
    'MANTENIMIENTO': 'GASTO_MANTENIMIENTO',
    'JARDINERIA': 'GASTO_MANTENIMIENTO',
    'LIMPIEZA': 'GASTO_MANTENIMIENTO',
    'REPARACIONES': 'GASTO_MANTENIMIENTO',
    'PUBLICIDAD': 'GASTO_PUBLICIDAD',
    'MARKETING': 'GASTO_PUBLICIDAD',
    'REDES': 'GASTO_PUBLICIDAD',
    'EQUIPO': 'GASTO_EQUIPO',
    'MOBILIARIO': 'GASTO_EQUIPO',
    'HERRAMIENTAS': 'GASTO_EQUIPO',
    'VEHICULOS': 'GASTO_VEHICULOS',
    'GASOLINA': 'GASTO_VEHICULOS',
    'COMBUSTIBLE': 'GASTO_VEHICULOS',
    'OFICINA': 'GASTO_OFICINA',
    'PAPELERIA': 'GASTO_OFICINA',
    'ADMINISTRATIVO': 'GASTO_OFICINA',
    'IMPUESTOS': 'GASTO_IMPUESTOS',
    'SAT': 'GASTO_IMPUESTOS',
    'PREDIAL': 'GASTO_IMPUESTOS',
    'SEGUROS': 'GASTO_SEGUROS',
    'FIANZAS': 'GASTO_SEGUROS',
    'BANCARIOS': 'GASTO_BANCARIOS',
    'COMISIONES': 'GASTO_BANCARIOS',
    'OTROS': 'GASTOS_GENERALES',
}


def get_cuenta_por_categoria(categoria):
    """
    Obtiene la cuenta contable apropiada según la categoría del gasto.
    
    Args:
        categoria: str - Categoría del gasto (puede ser None)
    
    Returns:
        CuentaContable o None
    """
    if not categoria:
        return get_cuenta('GASTOS_GENERALES')
    
    # Buscar mapeo exacto
    operacion = MAPEO_CATEGORIA_CUENTA.get(categoria.upper())
    if operacion:
        cuenta = get_cuenta(operacion)
        if cuenta:
            return cuenta
    
    # Buscar por palabra clave parcial
    categoria_upper = categoria.upper()
    for keyword, op in MAPEO_CATEGORIA_CUENTA.items():
        if keyword in categoria_upper:
            cuenta = get_cuenta(op)
            if cuenta:
                return cuenta
    
    # Fallback a gastos generales
    return get_cuenta('GASTOS_GENERALES')


# ==========================================
# SIGNAL: COMPRA/GASTO (comercial.Compra)
# ==========================================
@receiver(post_save, sender='comercial.Compra')
def crear_poliza_compra(sender, instance, created, **kwargs):
    """
    Genera póliza de egreso al registrar una compra.
    Si la compra no tiene cuenta_pago y/o unidad_negocio asignados, la póliza
    se crea en estado BORRADOR (no aplicada) para que quede visible como
    pendiente de completar, en vez de asumir una cuenta o unidad por defecto.
    """
    if not signals_enabled() or not created:
        return

    compra = instance
    if compra.total <= 0:
        return

    cuenta_iva = get_cuenta('IVA_ACREDITABLE')
    categoria = getattr(compra, 'categoria', None)
    cuenta_gasto = get_cuenta_por_categoria(categoria)

    if not cuenta_gasto:
        logger.warning("Póliza NO generada para Compra #%s: falta cuenta de gasto", compra.pk)
        return

    # Unidad de negocio: SOLO la explícita en la compra. Sin fallback a una
    # unidad "por defecto" — eso fue exactamente el bug que mezcló Eventos con Airbnb.
    unidad = compra.unidad_negocio
    cuenta_banco = compra.cuenta_pago.cuenta_contable if (compra.cuenta_pago and compra.cuenta_pago.cuenta_contable) else None

    faltantes = []
    if not unidad:
        faltantes.append("unidad_negocio")
    if not cuenta_banco:
        faltantes.append("cuenta_pago")

    estado_poliza = 'APLICADA' if not faltantes else 'BORRADOR'
    if faltantes:
        logger.warning(
            "Compra #%s incompleta (%s): póliza creada en BORRADOR.",
            compra.pk, ', '.join(faltantes)
        )
        # Se necesita una UnidadNegocio para crear la Poliza (campo no-nulo en el modelo).
        # Si falta, se usa temporalmente 'QUINTA' solo para poder guardar el BORRADOR,
        # pero el estado BORRADOR deja claro que debe revisarse y no se cuenta en reportes.
        unidad = unidad or get_unidad_negocio('QUINTA')

    usuario = get_usuario_sistema()
    content_type = ContentType.objects.get_for_model(compra)
    fecha_poliza = compra.fecha_emision or compra.uploaded_at.date()

    concepto_poliza = f"Compra: {compra.proveedor_display or 'Proveedor'}" + (f" [{categoria}]" if categoria else "")
    if not compra.es_deducible:
        concepto_poliza += " [SIN CFDI - NO DEDUCIBLE]"

    poliza = Poliza.objects.create(
        tipo='E',
        folio=Poliza.siguiente_folio('E', fecha_poliza),
        fecha=fecha_poliza,
        concepto=concepto_poliza,
        unidad_negocio=unidad,
        estado=estado_poliza,
        origen='COMPRA',
        content_type=content_type,
        object_id=compra.pk,
        created_by=usuario,
    )

    # Sin CFDI no hay IVA acreditable ante el SAT: el total completo (incluido
    # lo que hubiera sido IVA) se carga como gasto no deducible, en vez de
    # separar una porción como "acreditable" que en realidad no se puede acreditar.
    monto_gasto = compra.subtotal if (compra.es_deducible and cuenta_iva and compra.iva > 0) else compra.total

    MovimientoContable.objects.create(
        poliza=poliza, cuenta=cuenta_gasto,
        debe=monto_gasto, haber=Decimal('0.00'),
        concepto=compra.proveedor_display[:100] if compra.proveedor_display else "Compra",
        referencia=compra.uuid[:20] if compra.uuid else '',
    )

    if compra.es_deducible and cuenta_iva and compra.iva > 0:
        MovimientoContable.objects.create(
            poliza=poliza, cuenta=cuenta_iva,
            debe=compra.iva, haber=Decimal('0.00'),
            concepto="IVA acreditable",
        )

    if cuenta_banco:
        MovimientoContable.objects.create(
            poliza=poliza, cuenta=cuenta_banco,
            debe=Decimal('0.00'), haber=compra.total,
            concepto="Pago a proveedor",
            referencia=compra.uuid[:20] if compra.uuid else '',
        )

