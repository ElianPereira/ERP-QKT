"""Depósito en garantía (Issue #318, fase 3).

Reglas del propietario: 10% del precio en Evento, $500 por habitación en
Hospedaje (más $500 con mascota), nada en Pasadía; se cobra junto con el
saldo y se devuelve en 7 días naturales tras el servicio. Lo retenido por
daños es indemnización (sin IVA); lo retenido por tiempo extra o limpieza es
servicio (con IVA). Las pólizas las genera `contabilidad.signals` al crear
cada MovimientoDeposito.
"""
import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core_erp.impuestos import centavos

logger = logging.getLogger(__name__)

TOLERANCIA = Decimal('0.50')


class DepositoError(ValueError):
    """Error con mensaje apto para mostrarse a quien opera."""


def asegurar_deposito(cotizacion, monto, usuario=None):
    """Crea el depósito de la cotización o ajusta su monto mientras no se
    haya recibido nada. Devuelve el depósito (o None si el monto es 0)."""
    from .models import DepositoGarantia

    monto = centavos(Decimal(monto or 0))
    deposito = DepositoGarantia.objects.filter(cotizacion=cotizacion).first()
    if deposito is None:
        if monto <= 0:
            return None
        return DepositoGarantia.objects.create(
            cotizacion=cotizacion, monto=monto, created_by=usuario, updated_by=usuario,
        )
    if deposito.monto != monto and not deposito.movimientos.exists():
        deposito.monto = monto
        deposito.updated_by = usuario
        deposito.save(update_fields=['monto', 'updated_by', 'updated_at'])
    return deposito


def registrar_recepcion(deposito, *, monto, metodo, referencia='', fecha=None,
                        usuario=None, transaccion=None, validar_tope=True):
    """Registra dinero recibido para el depósito. Idempotente por transacción."""
    from .models import DepositoGarantia, MovimientoDeposito

    monto = centavos(Decimal(monto))
    if monto <= 0:
        raise DepositoError("El monto debe ser mayor a cero.")
    if transaccion is not None:
        existente = MovimientoDeposito.objects.filter(transaccion_openpay=transaccion).first()
        if existente:
            return existente
    with transaction.atomic():
        deposito = DepositoGarantia.objects.select_for_update().get(pk=deposito.pk)
        if deposito.liquidado:
            raise DepositoError("Este depósito ya se liquidó.")
        if validar_tope and monto > deposito.por_recibir + TOLERANCIA:
            raise DepositoError(f"El monto excede lo pendiente del depósito (${deposito.por_recibir:,.2f}).")
        return MovimientoDeposito.objects.create(
            deposito=deposito, tipo='RECEPCION', monto=monto,
            fecha=fecha or timezone.localdate(), metodo=metodo,
            referencia=referencia[:100], transaccion_openpay=transaccion,
            created_by=usuario,
        )


def registrar_recepcion_openpay(registro, monto):
    """Cargo de Openpay confirmado con destino DEPOSITO. El dinero ya entró:
    se registra aunque rebase lo pendiente, y si pasa eso se avisa."""
    deposito = getattr(registro.cotizacion, 'deposito_garantia', None)
    if deposito is None:
        raise DepositoError("La cotización no tiene depósito en garantía configurado.")
    excede = centavos(Decimal(monto)) > deposito.por_recibir + TOLERANCIA
    mov = registrar_recepcion(
        deposito, monto=monto, metodo='PLATAFORMA', referencia=registro.openpay_id,
        transaccion=registro, validar_tope=False,
    )
    if excede:
        logger.warning("Depósito COT-%s: Openpay cobró más de lo pendiente (%s).",
                       deposito.cotizacion_id, registro.openpay_id)
    return mov


def recepciones_con_tarjeta(deposito):
    """Cargos con tarjeta vía Openpay: los únicos que Openpay puede reembolsar."""
    return [
        m for m in deposito.movimientos.filter(tipo='RECEPCION').select_related('transaccion_openpay')
        if m.transaccion_openpay and m.transaccion_openpay.metodo == 'card'
    ]


def liquidar(deposito, **kwargs):
    """Devuelve lo que queda en custodia y retiene lo que se indique (ver
    `_liquidar`). Todo bajo un bloqueo de la fila del depósito: un doble clic
    no debe poder reembolsar dos veces en Openpay."""
    with transaction.atomic():
        return _liquidar(deposito, **kwargs)


def _liquidar(deposito, *, retencion_danos=Decimal('0'), retencion_servicio=Decimal('0'),
              desglose='', metodo_devolucion='TRANSFERENCIA', referencia='',
              reembolsar_openpay=False, usuario=None, fecha=None):
    """Devuelve lo que queda en custodia y, si aplica, retiene con desglose.

    Con `reembolsar_openpay` la devolución se hace en Openpay sobre los cargos
    con tarjeta del depósito; los depósitos por SPEI, efectivo o
    transferencia se devuelven por transferencia y aquí solo se registran.
    """
    from .models import DepositoGarantia, MovimientoDeposito
    from .services_openpay import reembolsar_monto_openpay

    retencion_danos = centavos(Decimal(retencion_danos or 0))
    retencion_servicio = centavos(Decimal(retencion_servicio or 0))
    if retencion_danos < 0 or retencion_servicio < 0:
        raise DepositoError("Las retenciones no pueden ser negativas.")
    retenido = retencion_danos + retencion_servicio
    if retenido > 0 and not (desglose or '').strip():
        raise DepositoError("Toda retención requiere desglose por escrito (con evidencia y valuación).")

    deposito = DepositoGarantia.objects.select_for_update().get(pk=deposito.pk)
    if deposito.liquidado:
        raise DepositoError("Este depósito ya se liquidó.")
    custodia = deposito.en_custodia
    if custodia <= 0:
        raise DepositoError("No hay dinero del depósito en custodia.")
    if retenido > custodia:
        raise DepositoError(f"La retención excede lo que hay en custodia (${custodia:,.2f}).")
    devolucion = custodia - retenido
    fecha = fecha or timezone.localdate()

    referencias_reembolso = []
    if devolucion > 0 and reembolsar_openpay:
        cargos = recepciones_con_tarjeta(deposito)
        disponible = sum((m.monto for m in cargos), Decimal('0.00'))
        if disponible < devolucion:
            raise DepositoError(
                f"Solo ${disponible:,.2f} del depósito se pagó con tarjeta en Openpay; "
                "devuelve el resto por transferencia."
            )
        pendiente = devolucion
        for mov in cargos:
            if pendiente <= 0:
                break
            parte = min(mov.monto, pendiente)
            resultado = reembolsar_monto_openpay(mov.transaccion_openpay, parte)
            if not resultado['ok']:
                raise DepositoError(f"Openpay rechazó el reembolso: {resultado['mensaje']}")
            referencias_reembolso.append(mov.transaccion_openpay.openpay_id)
            pendiente -= parte
        metodo_devolucion = 'PLATAFORMA'
        referencia = referencia or ', '.join(referencias_reembolso)

    if referencias_reembolso:
        logger.info("Depósito COT-%s: reembolsado en Openpay (%s).",
                    deposito.cotizacion_id, ', '.join(referencias_reembolso))

    base = {'deposito': deposito, 'fecha': fecha, 'created_by': usuario}
    if retencion_danos > 0:
        MovimientoDeposito.objects.create(
            tipo='RETENCION_DANOS', monto=retencion_danos, metodo='NO_APLICA',
            desglose=desglose, **base,
        )
    if retencion_servicio > 0:
        MovimientoDeposito.objects.create(
            tipo='RETENCION_SERVICIO', monto=retencion_servicio, metodo='NO_APLICA',
            desglose=desglose, **base,
        )
    if devolucion > 0:
        MovimientoDeposito.objects.create(
            tipo='DEVOLUCION', monto=devolucion, metodo=metodo_devolucion,
            referencia=(referencia or '')[:100], desglose=desglose, **base,
        )

    transaction.on_commit(lambda: _avisar_liquidacion(deposito.pk))
    return {'devuelto': devolucion, 'retenido': retenido}


def _avisar_liquidacion(deposito_pk):
    """Correo al cliente con lo devuelto y lo retenido. Nunca deshace la liquidación."""
    from comunicacion.services import enviar_email

    from .models import DepositoGarantia

    try:
        deposito = DepositoGarantia.objects.select_related('cotizacion__cliente').get(pk=deposito_pk)
        cotizacion = deposito.cotizacion
        enviar_email(
            cotizacion=cotizacion, tipo='REEMBOLSO', trigger='MANUAL',
            destinatario=cotizacion.cliente.email or '',
            asunto=f"Tu depósito en garantía — COT-{cotizacion.pk:03d}",
            template='comunicacion/email/deposito_liquidado.html',
            context={
                'cotizacion': cotizacion, 'deposito': deposito,
                'movimientos': deposito.movimientos.exclude(tipo='RECEPCION'),
            },
            clave_idempotencia=f'deposito:{deposito.pk}:liquidado',
        )
    except Exception:
        logger.exception("No se pudo avisar la liquidación del depósito %s", deposito_pk)
