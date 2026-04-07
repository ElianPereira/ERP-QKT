"""
Signals del Módulo de Facturación
=================================
Genera solicitudes de factura automáticamente desde Pagos.

Lógica de desglose fiscal:
- Siempre usa la proporción del pago vs precio_final de la cotización
- La cotización SIEMPRE tiene IVA calculado (precio_final = subtotal + iva - retenciones)
- El pago es una fracción del precio_final, se desglosa proporcionalmente
"""
from decimal import Decimal
from django.db.models.signals import post_save
from django.dispatch import receiver

from comercial.services import calcular_desglose_proporcional


@receiver(post_save, sender='comercial.Pago')
def crear_solicitud_factura_desde_pago(sender, instance, created, **kwargs):
    """
    Crea una SolicitudFactura automáticamente cuando se registra un Pago.
    Todos los pagos nuevos generan solicitud de factura.

    El desglose fiscal se calcula proporcionalmente basado en la cotización.
    """
    if not created:
        return

    pago = instance
    cotizacion = pago.cotizacion
    cliente = cotizacion.cliente

    # Importar aquí para evitar circular imports
    from facturacion.models import SolicitudFactura
    from facturacion.choices import FormaPago

    # ─── Idempotencia: no crear duplicados ──────────────────────
    if SolicitudFactura.objects.filter(pago=pago).exists():
        return

    # ─── Determinar datos fiscales ──────────────────────────────
    if cliente.rfc and cliente.razon_social:
        rfc = cliente.rfc
        razon_social = cliente.razon_social
        codigo_postal = cliente.codigo_postal_fiscal or '97238'
        regimen_fiscal = cliente.regimen_fiscal or '616'
        uso_cfdi = cliente.uso_cfdi or 'G03'
    else:
        rfc = 'XAXX010101000'
        razon_social = 'PUBLICO EN GENERAL'
        codigo_postal = '97238'
        regimen_fiscal = '616'
        uso_cfdi = 'S01'

    # ─── Calcular desglose fiscal proporcional ──────────────────
    monto_pago = Decimal(str(pago.monto))
    desglose = calcular_desglose_proporcional(monto_pago, cotizacion)
    subtotal = desglose['subtotal']
    iva = desglose['iva']
    retencion_isr = desglose['retencion_isr']
    retencion_iva = desglose['retencion_iva']

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
