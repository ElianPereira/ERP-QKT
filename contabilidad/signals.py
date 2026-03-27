"""
Signals del Módulo de Contabilidad
==================================
Genera pólizas automáticas cuando se registran operaciones en otros módulos.

Para desactivar temporalmente (migraciones masivas):
    settings.CONTABILIDAD_SIGNALS_ENABLED = False
"""
from decimal import Decimal
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType

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


# ==========================================
# SIGNAL: PAGO DE CLIENTE (comercial.Pago)
# ==========================================
@receiver(post_save, sender='comercial.Pago')
def crear_poliza_pago_cliente(sender, instance, created, **kwargs):
    """
    Genera póliza de ingreso cuando se registra un pago de cliente.
    
    Asiento:
        DEBE: Bancos/Caja (según método de pago)
        HABER: Anticipo de clientes (o Ingreso si evento ya ejecutado)
    """
    if not signals_enabled() or not created:
        return
    
    pago = instance
    
    # Determinar cuenta de cargo según método de pago
    if pago.metodo == 'EFECTIVO':
        cuenta_cargo = get_cuenta('CAJA')
    elif pago.metodo in ('TRANSFERENCIA', 'DEPOSITO'):
        cuenta_cargo = get_cuenta('BANCO_PRINCIPAL')
    elif pago.metodo in ('TARJETA_CREDITO', 'TARJETA_DEBITO'):
        cuenta_cargo = get_cuenta('BANCO_PRINCIPAL')  # Normalmente cae al banco
    else:
        cuenta_cargo = get_cuenta('BANCO_PRINCIPAL')
    
    # Determinar cuenta de abono: anticipo o ingreso
    cotizacion = pago.cotizacion
    if cotizacion.estado in ('EJECUTADA', 'CERRADA'):
        cuenta_abono = get_cuenta('INGRESO_EVENTOS')
    else:
        cuenta_abono = get_cuenta('ANTICIPO_CLIENTES')
    
    # Validar que existan las cuentas
    if not cuenta_cargo or not cuenta_abono:
        return  # Sin configuración, no genera póliza
    
    # Obtener unidad de negocio
    unidad = get_unidad_negocio('EVENTOS')
    if not unidad:
        return
    
    # Crear póliza
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
    
    # Movimiento DEBE: Bancos/Caja
    MovimientoContable.objects.create(
        poliza=poliza,
        cuenta=cuenta_cargo,
        debe=pago.monto,
        haber=Decimal('0.00'),
        concepto=f"Pago {pago.get_metodo_display()}",
        referencia=pago.referencia or '',
    )
    
    # Movimiento HABER: Anticipo/Ingreso
    MovimientoContable.objects.create(
        poliza=poliza,
        cuenta=cuenta_abono,
        debe=Decimal('0.00'),
        haber=pago.monto,
        concepto=f"COT-{cotizacion.pk:03d}",
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
        return
    
    # Obtener unidad de negocio
    unidad = get_unidad_negocio('AIRBNB')
    if not unidad:
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
# SIGNAL: COMPRA/GASTO (comercial.Compra)
# ==========================================
@receiver(post_save, sender='comercial.Compra')
def crear_poliza_compra(sender, instance, created, **kwargs):
    """
    Genera póliza de egreso cuando se registra una compra.
    
    Asiento:
        DEBE: Gastos generales (subtotal)
        DEBE: IVA acreditable
        HABER: Bancos/Proveedores (total)
    """
    if not signals_enabled() or not created:
        return
    
    compra = instance
    
    # Solo si tiene total > 0
    if compra.total <= 0:
        return
    
    # Obtener cuentas
    cuenta_banco = get_cuenta('BANCO_PRINCIPAL')
    cuenta_gasto = get_cuenta('GASTOS_GENERALES')
    cuenta_iva = get_cuenta('IVA_ACREDITABLE')
    
    if not cuenta_banco or not cuenta_gasto:
        return
    
    # Obtener unidad de negocio (default QUINTA)
    unidad = getattr(compra, 'unidad_negocio', None) or get_unidad_negocio('EVENTOS')
    if not unidad:
        return
    
    # Crear póliza
    usuario = get_usuario_sistema()
    content_type = ContentType.objects.get_for_model(compra)
    
    fecha_poliza = compra.fecha_emision or compra.uploaded_at.date()
    
    poliza = Poliza.objects.create(
        tipo='E',
        folio=Poliza.siguiente_folio('E', fecha_poliza),
        fecha=fecha_poliza,
        concepto=f"Compra: {compra.proveedor or 'Proveedor'}",
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
        return
    
    # Obtener unidad de negocio
    unidad = get_unidad_negocio('EVENTOS')
    if not unidad:
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