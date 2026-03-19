"""
Signals del Módulo de Facturación
=================================
Genera solicitudes de factura automáticamente desde Pagos.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender='comercial.Pago')
def crear_solicitud_factura_desde_pago(sender, instance, created, **kwargs):
    """
    Crea una SolicitudFactura cuando se registra un Pago
    con solicitar_factura=True.
    """
    if not created:
        return
    
    pago = instance
    
    # Verificar si el pago tiene el flag de solicitar factura
    if not getattr(pago, 'solicitar_factura', False):
        return
    
    # Verificar que la cotización requiere factura y tiene datos fiscales
    cotizacion = pago.cotizacion
    cliente = cotizacion.cliente
    
    if not cliente.es_cliente_fiscal or not cliente.rfc:
        return
    
    # Importar aquí para evitar circular imports
    from facturacion.models import SolicitudFactura
    from facturacion.choices import FormaPago
    
    # Mapear método de pago de Pago a FormaPago SAT
    mapeo_forma_pago = {
        'EFECTIVO': FormaPago.EFECTIVO,
        'TRANSFERENCIA': FormaPago.TRANSFERENCIA,
        'TARJETA_CREDITO': FormaPago.TARJETA_CREDITO,
        'TARJETA_DEBITO': FormaPago.TARJETA_DEBITO,
        'CHEQUE': FormaPago.CHEQUE,
        'DEPOSITO': FormaPago.TRANSFERENCIA,  # Depósito = Transferencia para SAT
        'PLATAFORMA': FormaPago.TRANSFERENCIA,
        'OTRO': FormaPago.POR_DEFINIR,
    }
    
    forma_pago = mapeo_forma_pago.get(pago.metodo, FormaPago.TRANSFERENCIA)
    
    # Crear la solicitud
    SolicitudFactura.objects.create(
        cliente=cliente,
        cotizacion=cotizacion,
        pago=pago,
        monto=pago.monto,
        concepto=f"Pago por evento: {cotizacion.nombre_evento}",
        rfc=cliente.rfc or '',
        razon_social=cliente.razon_social or cliente.nombre,
        codigo_postal=cliente.codigo_postal_fiscal or '',
        regimen_fiscal=cliente.regimen_fiscal or '616',  # Default: Sin obligaciones
        uso_cfdi=cliente.uso_cfdi or 'G03',  # Default: Gastos en general
        forma_pago=forma_pago,
        fecha_pago=pago.fecha_pago,
        created_by=pago.usuario,
    )