"""
Signals del Módulo de Facturación
=================================
Genera solicitudes de factura automáticamente desde Pagos.
"""
from decimal import Decimal, ROUND_HALF_UP
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender='comercial.Pago')
def crear_solicitud_factura_desde_pago(sender, instance, created, **kwargs):
    """
    Crea una SolicitudFactura cuando se registra un Pago
    con solicitar_factura=True.
    
    Si el cliente tiene datos fiscales completos, usa esos datos.
    Si no, factura a Público en General.
    
    El desglose fiscal se calcula proporcionalmente basado en la cotización.
    """
    if not created:
        return
    
    pago = instance
    
    # Verificar si el pago tiene el flag de solicitar factura
    if not getattr(pago, 'solicitar_factura', False):
        return
    
    cotizacion = pago.cotizacion
    cliente = cotizacion.cliente
    
    # Importar aquí para evitar circular imports
    from facturacion.models import SolicitudFactura
    from facturacion.choices import FormaPago
    
    # Determinar datos fiscales: del cliente o Público en General
    if cliente.rfc and cliente.razon_social:
        # Cliente con datos fiscales completos
        rfc = cliente.rfc
        razon_social = cliente.razon_social
        codigo_postal = cliente.codigo_postal_fiscal or '97238'
        regimen_fiscal = cliente.regimen_fiscal or '616'
        uso_cfdi = cliente.uso_cfdi or 'G03'
    else:
        # Público en General
        rfc = 'XAXX010101000'
        razon_social = 'PUBLICO EN GENERAL'
        codigo_postal = '97238'
        regimen_fiscal = '616'  # Sin obligaciones fiscales
        uso_cfdi = 'S01'  # Sin efectos fiscales
    
    # ─── Calcular desglose fiscal ───────────────────────────────
    monto_pago = Decimal(str(pago.monto))
    
# Todo pago tiene IVA — desglose proporcional de la cotización
    if cotizacion.precio_final > 0:
        precio_final = Decimal(str(cotizacion.precio_final))
        proporcion = monto_pago / precio_final

        subtotal = (Decimal(str(cotizacion.subtotal)) * proporcion).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        iva = (Decimal(str(cotizacion.iva)) * proporcion).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        retencion_isr = (Decimal(str(cotizacion.retencion_isr)) * proporcion).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        retencion_iva = (Decimal(str(cotizacion.retencion_iva)) * proporcion).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        subtotal = (monto_pago / Decimal('1.16')).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        iva = (monto_pago - subtotal).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        if cliente.tipo_persona == 'MORAL':
            retencion_isr = (subtotal * Decimal('0.0125')).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
    else:
        # Cotización sin precio aún: desglosar el pago directamente
        subtotal = (monto_pago / Decimal('1.16')).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        iva = monto_pago - subtotal
        retencion_isr = Decimal('0.00')
        retencion_iva = Decimal('0.00')
        
    # ─── Mapear método de pago ──────────────────────────────────
    mapeo_forma_pago = {
        'EFECTIVO': FormaPago.EFECTIVO,
        'TRANSFERENCIA': FormaPago.TRANSFERENCIA,
        'TARJETA_CREDITO': FormaPago.TARJETA_CREDITO,
        'TARJETA_DEBITO': FormaPago.TARJETA_DEBITO,
        'CHEQUE': FormaPago.CHEQUE,
        'DEPOSITO': FormaPago.TRANSFERENCIA,
        'PLATAFORMA': FormaPago.TRANSFERENCIA,
        'OTRO': FormaPago.POR_DEFINIR,
    }
    
    forma_pago = mapeo_forma_pago.get(pago.metodo, FormaPago.TRANSFERENCIA)
    
    # ─── Crear la solicitud ─────────────────────────────────────
    SolicitudFactura.objects.create(
        cliente=cliente,
        cotizacion=cotizacion,
        pago=pago,
        monto=monto_pago,
        subtotal=subtotal,
        iva=iva,
        retencion_isr=retencion_isr,
        retencion_iva=retencion_iva,
        concepto="COT-{} Servicio De Evento En General".format(str(cotizacion.id).zfill(4)),
        rfc=rfc,
        razon_social=razon_social,
        codigo_postal=codigo_postal,
        regimen_fiscal=regimen_fiscal,
        uso_cfdi=uso_cfdi,
        forma_pago=forma_pago,
        fecha_pago=pago.fecha_pago,
        created_by=pago.usuario,
    )