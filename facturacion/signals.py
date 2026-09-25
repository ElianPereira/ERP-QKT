"""
Signals del Módulo de Facturación
=================================
Genera solicitudes de factura automáticamente desde Pagos.

Lógica de desglose fiscal:
- Siempre usa la proporción del pago vs precio_final de la cotización
- La cotización SIEMPRE tiene IVA calculado (precio_final = subtotal + iva - retenciones)
- El pago es una fracción del precio_final, se desglosa proporcionalmente
"""
import logging
from decimal import Decimal

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from comercial.services import calcular_desglose_proporcional

logger = logging.getLogger(__name__)

# Concepto que ve el contador (y termina en el CFDI) por tipo de servicio.
# Antes era "Servicio De Evento En General" para todo, también Pasadía y
# Hospedaje.
CONCEPTO_POR_SERVICIO = {
    'EVENTO': 'Servicio De Evento En General',
    'PASADIA': 'Servicio De Pasadía',
    'HOSPEDAJE': 'Servicio De Hospedaje',
    'ARRENDAMIENTO': 'Arrendamiento De Mobiliario',
}


def _forma_pago_openpay(pago):
    """Forma de pago SAT real de un cobro de Openpay, o None si no lo es.

    Todo cobro de Openpay se registra como Pago(metodo='PLATAFORMA'), que a
    secas se mapeaba a 03 Transferencia aunque fuera tarjeta o efectivo en
    tienda. El método real vive en la OpenpayTransaccion (el Pago guarda su
    openpay_id en `referencia`), que siempre existe antes de crear el Pago.
    """
    if pago.metodo != 'PLATAFORMA' or not pago.referencia:
        return None
    from comercial.models import OpenpayTransaccion
    from facturacion.choices import FormaPago

    registro = OpenpayTransaccion.objects.filter(openpay_id=pago.referencia).first()
    if registro is None:
        return None
    if registro.metodo == 'store':
        return FormaPago.EFECTIVO
    if registro.metodo == 'bank_account':
        return FormaPago.TRANSFERENCIA
    if registro.metodo == 'card':
        # El cargo síncrono guarda la respuesta del cargo; el webhook, el
        # evento completo con el cargo bajo 'transaction'.
        crudo = registro.payload_crudo or {}
        cargo = crudo.get('transaction') or crudo
        tipo = ((cargo.get('card') or {}).get('type') or '').lower()
        if tipo == 'debit':
            return FormaPago.TARJETA_DEBITO
        # 'credit', o tipo desconocido: 99 "Por definir" no es válido con
        # PUE, así que se asume crédito y el contador lo ve en la solicitud.
        return FormaPago.TARJETA_CREDITO
    return None


@receiver(post_save, sender='comercial.Pago')
def crear_solicitud_factura_desde_pago(sender, instance, created, **kwargs):
    """
    Crea una SolicitudFactura automáticamente cuando se registra un Pago.
    Todos los pagos nuevos generan solicitud de factura, con una excepción.

    El desglose fiscal se calcula proporcionalmente basado en la cotización.
    """
    if not created:
        return

    pago = instance

    # Excepción: el Pago de reactivación que genera un contracargo ganado
    # (ver comercial.models.Contracargo) no es una venta nueva — es la
    # reversión de la reversión que ya generó su propia SolicitudFactura al
    # recibirse el contracargo. Pedir una factura nueva aquí confundiría al
    # contador con un CFDI que no corresponde a ninguna venta real; el ajuste
    # (nota de crédito/cancelación) lo maneja a mano contra la solicitud que
    # ya existe. El Pago de reversión SÍ sigue el camino normal, igual que
    # cualquier reembolso. Se marca con un atributo transitorio (no la
    # relación inversa Contracargo.pago_reactivacion) porque este signal
    # corre antes de que el Contracargo quede guardado y enlazado — ver
    # comercial.services_openpay._asegurar_pago_reactivacion_contracargo.
    if getattr(pago, '_contracargo_reactivacion', False):
        return

    cotizacion = pago.cotizacion
    cliente = cotizacion.cliente

    # Importar aquí para evitar circular imports
    from facturacion.choices import FormaPago, uso_cfdi_compatible
    from facturacion.models import RFC_PUBLICO_GENERAL, SolicitudFactura

    # ─── Idempotencia: no crear duplicados ──────────────────────
    if SolicitudFactura.objects.filter(pago=pago).exists():
        return

    # ─── Determinar datos fiscales ──────────────────────────────
    if cliente.rfc and cliente.razon_social:
        rfc = cliente.rfc
        razon_social = cliente.razon_social
        codigo_postal = cliente.codigo_postal_fiscal or '97238'
        # 616 ("Sin obligaciones fiscales") es exclusivo de persona física —
        # dejarlo como fallback también para una persona moral produciría un
        # CFDI inválido. 601 (General de Ley Personas Morales) es el régimen
        # real más común para una empresa; el contador lo corrige si tributa
        # distinto.
        regimen_fiscal = cliente.regimen_fiscal or (
            '601' if cliente.tipo_persona == 'MORAL' else '616'
        )
        # 616 (el default de Cliente) con G03 (también default) es una
        # combinación que el PAC rechaza: se ajusta el uso al régimen.
        uso_cfdi = uso_cfdi_compatible(regimen_fiscal, cliente.uso_cfdi)
    else:
        rfc = RFC_PUBLICO_GENERAL
        razon_social = 'PUBLICO EN GENERAL'
        codigo_postal = '97238'
        regimen_fiscal = '616'
        uso_cfdi = 'S01'

    # ─── Calcular desglose fiscal proporcional ──────────────────
    monto_pago = Decimal(str(pago.monto))
    desglose = calcular_desglose_proporcional(monto_pago, cotizacion)
    subtotal = desglose['subtotal']
    iva = desglose['iva']
    impuesto_hospedaje = desglose['impuesto_hospedaje']
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

    forma_pago = _forma_pago_openpay(pago) or mapeo_forma_pago.get(pago.metodo, FormaPago.TRANSFERENCIA)

    # ─── Crear la solicitud ─────────────────────────────────────
    solicitud = SolicitudFactura.objects.create(
        cliente=cliente,
        cotizacion=cotizacion,
        pago=pago,
        # Toda solicitud automática viene de una Cotizacion (Evento/Pasadía/
        # Hospedaje/Arrendamiento) — reserva directa de la Quinta.
        linea_negocio='QUINTA',
        monto=monto_pago,
        subtotal=subtotal,
        iva=iva,
        impuesto_hospedaje=impuesto_hospedaje,
        retencion_isr=retencion_isr,
        retencion_iva=retencion_iva,
        concepto="COT-{} {}".format(
            str(cotizacion.id).zfill(4),
            CONCEPTO_POR_SERVICIO.get(cotizacion.tipo_servicio, CONCEPTO_POR_SERVICIO['EVENTO']),
        ),
        rfc=rfc,
        razon_social=razon_social,
        codigo_postal=codigo_postal,
        regimen_fiscal=regimen_fiscal,
        uso_cfdi=uso_cfdi,
        forma_pago=forma_pago,
        fecha_pago=pago.fecha_pago,
        created_by=pago.usuario,
    )

    # ─── Envío automático al contador ────────────────────────────
    # Antes había que entrar al admin y darle a los botones "Email"/"WhatsApp"
    # a mano por cada solicitud. Se manda sola en cuanto el Pago que la generó
    # queda confirmado en la base de datos (on_commit: si el guardado del Pago
    # se revierte, este envío nunca se dispara). Nunca lanza — un fallo aquí
    # no debe tumbar el guardado del Pago que lo originó.
    def _enviar_automatico():
        from .services import enviar_solicitud_al_contador
        try:
            enviar_solicitud_al_contador(solicitud)
        except Exception:
            logger.exception(
                "Envío automático de la solicitud de factura #%s falló", solicitud.pk
            )

    transaction.on_commit(_enviar_automatico)


@receiver(post_save, sender='facturacion.SolicitudFactura')
def enviar_factura_al_cliente(sender, instance, **kwargs):
    """
    En cuanto el contador sube la factura (la solicitud pasa sola a FACTURADA
    en `SolicitudFactura.save()`), se le manda al cliente. Idempotente por la
    clave de `notificar_factura`: reguardar la solicitud no reenvía.
    """
    if instance.estado != 'FACTURADA' or not instance.tiene_factura:
        return

    def _enviar():
        from comunicacion.services_notificaciones import notificar_factura
        try:
            notificar_factura(instance)
        except Exception:
            logger.exception("Envío de la factura SOL-%s al cliente falló", instance.pk)

    transaction.on_commit(_enviar)
