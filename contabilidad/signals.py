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
    unidad = get_unidad_negocio('EVENTOS')
    if not unidad:
        logger.warning("Póliza NO generada para Pago #%s: falta UnidadNegocio 'EVENTOS'", pago.pk)
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


# ==========================================
# SIGNAL: PAGO AIRBNB (airbnb.PagoAirbnb)
# ==========================================
@receiver(post_save, sender='airbnb.PagoAirbnb')
def crear_poliza_pago_airbnb(sender, instance, created, **kwargs):
    """
    Genera póliza de ingreso cuando se registra un pago de Airbnb.

    Asiento (ejemplo pago $10,000 bruto):
        DEBE: Bancos                    $8,500 (neto recibido)
        DEBE: Retención ISR (4%)          $400 (impuesto a favor)
        DEBE: Retención IVA (8%)          $800 (impuesto a favor)
        DEBE: Comisión Airbnb             $300 (gasto)
        HABER: Ingreso Airbnb          $10,000 (bruto)
    """
    if not signals_enabled() or not created:
        return

    pago = instance

    # Solo generar póliza si está marcado como PAGADO
    if pago.estado != 'PAGADO':
        return

    # Obtener cuentas
    cuenta_banco = get_cuenta('BANCO_PRINCIPAL')
    cuenta_ingreso = get_cuenta('INGRESO_AIRBNB')
    cuenta_ret_isr = get_cuenta('RETENCION_ISR_AIRBNB')
    cuenta_ret_iva = get_cuenta('RETENCION_IVA_AIRBNB')
    cuenta_comision = get_cuenta('COMISION_AIRBNB')

    # Validar cuentas mínimas
    if not cuenta_banco or not cuenta_ingreso:
        logger.warning(
            "Póliza NO generada para PagoAirbnb #%s: falta configuración contable "
            "(cuenta_banco=%s, cuenta_ingreso=%s)", pago.pk, cuenta_banco, cuenta_ingreso
        )
        return

    # Obtener unidad de negocio
    unidad = get_unidad_negocio('AIRBNB')
    if not unidad:
        logger.warning("Póliza NO generada para PagoAirbnb #%s: falta UnidadNegocio 'AIRBNB'", pago.pk)
        return

    # Crear póliza
    usuario = get_usuario_sistema()
    content_type = ContentType.objects.get_for_model(pago)

    fecha_poliza = pago.fecha_pago or pago.fecha_checkin

    poliza = Poliza.objects.create(
        tipo='I',
        folio=Poliza.siguiente_folio('I', fecha_poliza),
        fecha=fecha_poliza,
        concepto=f"Airbnb: {pago.huesped} ({pago.codigo_confirmacion or 'Sin código'})",
        unidad_negocio=unidad,
        estado='APLICADA',
        origen='PAGO_AIRBNB',
        content_type=content_type,
        object_id=pago.pk,
        created_by=usuario,
    )

    # DEBE: Banco (monto neto)
    MovimientoContable.objects.create(
        poliza=poliza,
        cuenta=cuenta_banco,
        debe=pago.monto_neto,
        haber=Decimal('0.00'),
        concepto="Depósito Airbnb",
        referencia=pago.codigo_confirmacion or '',
    )

    # DEBE: Retención ISR (si existe cuenta configurada)
    if cuenta_ret_isr and pago.retencion_isr > 0:
        MovimientoContable.objects.create(
            poliza=poliza,
            cuenta=cuenta_ret_isr,
            debe=pago.retencion_isr,
            haber=Decimal('0.00'),
            concepto="ISR retenido 4%",
        )

    # DEBE: Retención IVA (si existe cuenta configurada)
    if cuenta_ret_iva and pago.retencion_iva > 0:
        MovimientoContable.objects.create(
            poliza=poliza,
            cuenta=cuenta_ret_iva,
            debe=pago.retencion_iva,
            haber=Decimal('0.00'),
            concepto="IVA retenido 8%",
        )

    # DEBE: Comisión Airbnb (si existe cuenta configurada)
    if cuenta_comision and pago.comision_airbnb > 0:
        MovimientoContable.objects.create(
            poliza=poliza,
            cuenta=cuenta_comision,
            debe=pago.comision_airbnb,
            haber=Decimal('0.00'),
            concepto="Comisión plataforma",
        )

    # HABER: Ingreso bruto
    MovimientoContable.objects.create(
        poliza=poliza,
        cuenta=cuenta_ingreso,
        debe=Decimal('0.00'),
        haber=pago.monto_bruto,
        concepto=f"{pago.noches} noches hospedaje",
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
    Genera póliza de egreso cuando se registra una compra.
    
    Mejoras:
    - Mapea categoría de gasto a cuenta contable específica
    - Soporta unidad de negocio asignada a la compra

    Asiento:
        DEBE: Cuenta de gasto según categoría (subtotal)
        DEBE: IVA acreditable (si aplica)
        HABER: Bancos (total)
    """
    if not signals_enabled() or not created:
        return

    compra = instance

    # Solo si tiene total > 0
    if compra.total <= 0:
        return

    # Obtener cuentas
    cuenta_banco = get_cuenta('BANCO_PRINCIPAL')
    cuenta_iva = get_cuenta('IVA_ACREDITABLE')
    
    # Obtener cuenta de gasto según categoría
    categoria = getattr(compra, 'categoria', None)
    cuenta_gasto = get_cuenta_por_categoria(categoria)

    if not cuenta_banco or not cuenta_gasto:
        logger.warning(
            "Póliza NO generada para Compra #%s: falta configuración contable "
            "(cuenta_banco=%s, cuenta_gasto=%s)", compra.pk, cuenta_banco, cuenta_gasto
        )
        return

    # Obtener unidad de negocio desde la compra o default EVENTOS
    unidad = getattr(compra, 'unidad_negocio', None) or get_unidad_negocio('EVENTOS')
    if not unidad:
        logger.warning("Póliza NO generada para Compra #%s: falta UnidadNegocio", compra.pk)
        return

    # Crear póliza
    usuario = get_usuario_sistema()
    content_type = ContentType.objects.get_for_model(compra)

    fecha_poliza = compra.fecha_emision or compra.uploaded_at.date()

    poliza = Poliza.objects.create(
        tipo='E',
        folio=Poliza.siguiente_folio('E', fecha_poliza),
        fecha=fecha_poliza,
        concepto=f"Compra: {compra.proveedor or 'Proveedor'}" + (f" [{categoria}]" if categoria else ""),
        unidad_negocio=unidad,
        estado='APLICADA',
        origen='COMPRA',
        content_type=content_type,
        object_id=compra.pk,
        created_by=usuario,
    )

    # DEBE: Gasto (subtotal)
    MovimientoContable.objects.create(
        poliza=poliza,
        cuenta=cuenta_gasto,
        debe=compra.subtotal,
        haber=Decimal('0.00'),
        concepto=compra.proveedor[:100] if compra.proveedor else "Compra",
        referencia=compra.uuid[:20] if compra.uuid else '',
    )

    # DEBE: IVA acreditable (si aplica)
    if cuenta_iva and compra.iva > 0:
        MovimientoContable.objects.create(
            poliza=poliza,
            cuenta=cuenta_iva,
            debe=compra.iva,
            haber=Decimal('0.00'),
            concepto="IVA acreditable",
        )

    # HABER: Banco (total)
    MovimientoContable.objects.create(
        poliza=poliza,
        cuenta=cuenta_banco,
        debe=Decimal('0.00'),
        haber=compra.total,
        concepto="Pago a proveedor",
        referencia=compra.uuid[:20] if compra.uuid else '',
    )


# ==========================================
# SIGNAL: NÓMINA (nomina.ReciboNomina)
# ==========================================
@receiver(post_save, sender='nomina.ReciboNomina')
def crear_poliza_nomina(sender, instance, created, **kwargs):
    """
    Genera póliza de egreso cuando se registra un recibo de nómina.

    Asiento:
        DEBE: Sueldos y salarios
        HABER: Bancos/Caja
    """
    if not signals_enabled() or not created:
        return

    recibo = instance

    if recibo.total_pagado <= 0:
        return

    # Obtener cuentas
    cuenta_sueldos = get_cuenta('SUELDOS_SALARIOS')
    cuenta_banco = get_cuenta('BANCO_PRINCIPAL')

    if not cuenta_sueldos or not cuenta_banco:
        logger.warning(
            "Póliza NO generada para ReciboNomina #%s: falta configuración contable "
            "(cuenta_sueldos=%s, cuenta_banco=%s)", recibo.pk, cuenta_sueldos, cuenta_banco
        )
        return

    # Obtener unidad de negocio
    unidad = get_unidad_negocio('EVENTOS')
    if not unidad:
        logger.warning("Póliza NO generada para ReciboNomina #%s: falta UnidadNegocio 'EVENTOS'", recibo.pk)
        return

    # Crear póliza
    usuario = get_usuario_sistema()
    content_type = ContentType.objects.get_for_model(recibo)

    fecha_poliza = recibo.fecha_generacion.date()

    poliza = Poliza.objects.create(
        tipo='E',
        folio=Poliza.siguiente_folio('E', fecha_poliza),
        fecha=fecha_poliza,
        concepto=f"Nómina: {recibo.empleado.nombre} - {recibo.periodo}",
        unidad_negocio=unidad,
        estado='APLICADA',
        origen='NOMINA',
        content_type=content_type,
        object_id=recibo.pk,
        created_by=usuario,
    )

    # DEBE: Sueldos y salarios
    MovimientoContable.objects.create(
        poliza=poliza,
        cuenta=cuenta_sueldos,
        debe=recibo.total_pagado,
        haber=Decimal('0.00'),
        concepto=f"{recibo.empleado.nombre} - {recibo.horas_trabajadas}h",
    )

    # HABER: Banco
    MovimientoContable.objects.create(
        poliza=poliza,
        cuenta=cuenta_banco,
        debe=Decimal('0.00'),
        haber=recibo.total_pagado,
        concepto="Pago nómina",
    )