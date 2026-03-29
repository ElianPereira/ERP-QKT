"""
Signals del Módulo de Facturación
=================================
Genera solicitudes de factura automáticamente desde Pagos.
Todo pago genera su solicitud — IVA siempre incluido.
"""
from decimal import Decimal, ROUND_HALF_UP
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender='comercial.Pago')
def crear_solicitud_factura_desde_pago(sender, instance, created, **kwargs):
    """
    Crea una SolicitudFactura cuando se registra un Pago.

    Desglose fiscal:
    - El monto del pago es el TOTAL (ya incluye IVA).
    - subtotal = monto / 1.16
    - iva      = monto - subtotal
    - Si cliente MORAL: retencion_isr = subtotal * 0.0125
    """
    if not created:
        return

    pago = instance
    cotizacion = pago.cotizacion
    cliente = cotizacion.cliente

    from facturacion.models import SolicitudFactura
    from facturacion.choices import FormaPago

    # ─── Datos fiscales ─────────────────────────────────────────
    if cliente.rfc and cliente.razon_social:
        rfc          = cliente.rfc
        razon_social = cliente.razon_social
        codigo_postal = cliente.codigo_postal_fiscal or '97238'
        regimen_fiscal = cliente.regimen_fiscal or '616'
        uso_cfdi     = cliente.uso_cfdi or 'G03'
    else:
        rfc           = 'XAXX010101000'
        razon_social  = 'PUBLICO EN GENERAL'
        codigo_postal = '97238'
        regimen_fiscal = '616'
        uso_cfdi      = 'S01'

    # ─── Desglose fiscal ────────────────────────────────────────
    # El monto del pago es el total con IVA incluido
    monto_pago = Decimal(str(pago.monto))

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
        retencion_isr = Decimal('0.00')

    retencion_iva = Decimal('0.00')

    # ─── Método de pago ─────────────────────────────────────────
    mapeo_forma_pago = {
        'EFECTIVO':       FormaPago.EFECTIVO,
        'TRANSFERENCIA':  FormaPago.TRANSFERENCIA,
        'TARJETA_CREDITO':FormaPago.TARJETA_CREDITO,
        'TARJETA_DEBITO': FormaPago.TARJETA_DEBITO,
        'CHEQUE':         FormaPago.CHEQUE,
        'DEPOSITO':       FormaPago.TRANSFERENCIA,
        'PLATAFORMA':     FormaPago.TRANSFERENCIA,
        'OTRO':           FormaPago.POR_DEFINIR,
    }
    forma_pago = mapeo_forma_pago.get(pago.metodo, FormaPago.TRANSFERENCIA)

    # ─── Crear solicitud ────────────────────────────────────────
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