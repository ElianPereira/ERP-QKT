"""
Acciones de dinero que Dirección ordena por WhatsApp (Fase 2b, Issue #433 · DINERO).

Catálogo cerrado de tres acciones, todas con la mecánica de
`services_ordenes_wa` (teléfono de Dirección, botón de un solo uso, 10 min,
revalidación al confirmar):

  - devolución del depósito en garantía, completa y sin retenciones, registrada
    con `services_deposito.liquidar` (nunca llama a Openpay: lo ya devuelto por
    transferencia o efectivo solo se asienta);
  - descuento manual existente (`DescuentoService.aplicar`; no cortesías);
  - reembolso ya hecho fuera del sistema (`Pago` REEMBOLSO por `full_clean()` +
    `save()`, como el admin; tampoco llama a Openpay).

Arriba de `ERP_KOOXI_DINERO_MAX` no hay propuesta: se hace en el admin (2FA). El
tope se compara al proponer y otra vez al confirmar. Todo con Decimal y
ROUND_HALF_UP (`core_erp.impuestos`).
"""
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from core_erp import impuestos

from . import services_deposito
from .models import AccionWhatsApp, Cotizacion, DepositoGarantia, Descuento, MovimientoDeposito, Pago
from .services_descuentos import ContextoDescuento, DescuentoService
from .services_ordenes_wa import (
    OrdenError,
    Resultado,
    crear_propuesta,
    dinero,
    direccion_activa,
    folio,
    rechazar,
)

MEDIOS_DEVOLUCION = {'TRANSFERENCIA': 'transferencia', 'EFECTIVO': 'efectivo'}
MEDIOS_REEMBOLSO = {'TRANSFERENCIA': 'transferencia', 'EFECTIVO': 'efectivo'}
EN_ADMIN = 'Hazlo desde el admin.'


def _tope() -> Decimal:
    return impuestos.centavos(settings.ERP_KOOXI_DINERO_MAX)


def _revisar_tope(monto: Decimal) -> None:
    if monto > _tope():
        raise OrdenError(f'{dinero(monto)} rebasa el tope de {dinero(_tope())} para órdenes por WhatsApp. {EN_ADMIN}')


def _cotizacion(cotizacion_id) -> Cotizacion:
    cot = Cotizacion.objects.select_related('cliente').filter(pk=_pk(cotizacion_id)).first()
    if cot is None:
        raise OrdenError('No encontré esa cotización. Revisa el folio.')
    return cot


def _pk(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _cliente(cot: Cotizacion) -> str:
    return str(cot.cliente)


def _origen(accion: AccionWhatsApp) -> str:
    return f'por WhatsApp desde el número de {accion.rol} ({accion.telefono}), orden #{accion.pk}'


# ─────────────────────── Devolución del depósito en garantía ────────────────

def _revisar_devolucion(cotizacion_id, medio: str):
    if medio not in MEDIOS_DEVOLUCION:
        raise OrdenError('La devolución solo se registra como transferencia o efectivo. '
                         'Lo que se devuelve por tarjeta se hace desde el admin.')
    cot = _cotizacion(cotizacion_id)
    deposito = getattr(cot, 'deposito_garantia', None)
    if deposito is None:
        raise OrdenError(f'{folio(cot)} no tiene depósito en garantía.')
    if deposito.liquidado:
        raise OrdenError(f'El depósito de {folio(cot)} ya se liquidó.')
    custodia = impuestos.centavos(deposito.en_custodia)
    if custodia <= 0:
        raise OrdenError(f'El depósito de {folio(cot)} no tiene dinero en custodia.')
    _revisar_tope(custodia)
    return cot, deposito, custodia


def proponer_devolucion_deposito(telefono: str, cotizacion_id, medio: str) -> Optional[Resultado]:
    """Devolver todo lo que hay en custodia, sin retenciones. El importe lo fija
    el ERP, no el modelo ni el mensaje."""
    if not direccion_activa(telefono):
        return None
    parametros = {'medio': medio}
    try:
        cot, deposito, custodia = _revisar_devolucion(cotizacion_id, medio)
    except OrdenError as e:
        return rechazar(telefono, 'DEVOLUCION_DEPOSITO', str(e), parametros=parametros,
                        cotizacion=Cotizacion.objects.filter(pk=_pk(cotizacion_id)).first())
    resumen = (f'Registrar la devolución del depósito en garantía de {folio(cot)} ({_cliente(cot)}): '
               f'{dinero(custodia)} por {MEDIOS_DEVOLUCION[medio]}, sin retenciones. El ERP solo lo registra: '
               'la transferencia o el efectivo los entregas tú.')
    return crear_propuesta(telefono, 'DEVOLUCION_DEPOSITO', resumen, cotizacion=cot, monto=custodia,
                           parametros=parametros)


def _ejecutar_devolucion(accion: AccionWhatsApp) -> str:
    medio = accion.parametros.get('medio', '')
    DepositoGarantia.objects.select_for_update().filter(cotizacion_id=accion.cotizacion_id).first()
    cot, deposito, custodia = _revisar_devolucion(accion.cotizacion_id, medio)
    if custodia != accion.monto:
        raise OrdenError(f'La custodia cambió ({dinero(custodia)} en vez de {dinero(accion.monto)}). '
                         'Pídelo de nuevo.')
    services_deposito.liquidar(
        deposito, metodo_devolucion=medio, referencia=f'WA-ORDEN-{accion.pk}', reembolsar_openpay=False,
        desglose=f'Devolución completa registrada {_origen(accion)}.')
    accion.movimiento_deposito = (
        MovimientoDeposito.objects.filter(deposito=deposito, tipo='DEVOLUCION').order_by('-pk').first())
    return f'✅ Listo: registré la devolución de {dinero(custodia)} del depósito de {folio(cot)}.'


# ─────────────────────────── Descuento manual existente ─────────────────────

def _descuento_manual(nombre: str) -> Descuento:
    candidatos = list(Descuento.objects.filter(
        modo='MANUAL', activo=True, es_cortesia=False, nombre__iexact=(nombre or '').strip())[:2])
    if len(candidatos) != 1:
        raise OrdenError('No encontré un descuento manual activo con ese nombre (o hay varios). '
                         'Las cortesías se aplican desde el admin.')
    return candidatos[0]


def _revisar_descuento(cotizacion_id, nombre: str):
    cot = _cotizacion(cotizacion_id)
    if cot.estado not in ('BORRADOR', 'COTIZADA', 'CONFIRMADA'):
        raise OrdenError(f'{folio(cot)} está {cot.get_estado_display().lower()}: ya no admite descuentos.')
    descuento = _descuento_manual(nombre)
    if cot.descuentos_aplicados.filter(activo=True, descuento=descuento).exists():
        raise OrdenError(f'«{descuento.nombre}» ya está aplicado en {folio(cot)}.')
    base = DescuentoService.monto_aplicable(cot, descuento)
    if base <= 0:
        raise OrdenError('Ese descuento no descontaría nada en esta cotización.')
    if base >= _disponible(cot):
        raise OrdenError('Dejaría la cotización en $0.00: eso es una cortesía, aplícala desde el admin.')
    ahorro = impuestos.con_iva(base)   # lo que el cliente deja de pagar, IVA incluido
    _revisar_tope(ahorro)
    return cot, descuento, base, ahorro


def _disponible(cot: Cotizacion) -> Decimal:
    """Base (sin IVA) que aún se puede descontar: subtotal menos lo ya descontado."""
    subtotal = ContextoDescuento.de_cotizacion(cot).subtotal
    actual = Cotizacion.objects.filter(pk=cot.pk).values_list('descuento', flat=True).get()
    return subtotal - (actual or Decimal('0'))


def proponer_descuento(telefono: str, cotizacion_id, descuento: str) -> Optional[Resultado]:
    if not direccion_activa(telefono):
        return None
    parametros = {'descuento': (descuento or '').strip()[:120]}
    try:
        cot, regla, base, ahorro = _revisar_descuento(cotizacion_id, descuento)
    except OrdenError as e:
        return rechazar(telefono, 'DESCUENTO', str(e), parametros=parametros,
                        cotizacion=Cotizacion.objects.filter(pk=_pk(cotizacion_id)).first())
    parametros.update(descuento_id=regla.pk, base=str(base))
    resumen = (f'Aplicar el descuento «{regla.nombre}» a {folio(cot)} ({_cliente(cot)}): '
               f'el cliente ahorra {dinero(ahorro)} con IVA incluido.')
    return crear_propuesta(telefono, 'DESCUENTO', resumen, cotizacion=cot, monto=ahorro, parametros=parametros)


def _ejecutar_descuento(accion: AccionWhatsApp) -> str:
    # Con la cotización bloqueada: otro descuento o un cambio de conceptos en
    # estos minutos cambia el importe y la orden ya no es la que se vio.
    Cotizacion.objects.select_for_update().get(pk=accion.cotizacion_id)
    cot, regla, base, ahorro = _revisar_descuento(accion.cotizacion_id, accion.parametros.get('descuento', ''))
    if regla.pk != accion.parametros.get('descuento_id') or ahorro != accion.monto:
        raise OrdenError(f'El importe cambió ({dinero(ahorro)} en vez de {dinero(accion.monto)}). Pídelo de nuevo.')
    accion.descuento_aplicado = DescuentoService.aplicar(cot, regla, usuario=None, modo='MANUAL')
    return f'✅ Listo: apliqué «{regla.nombre}» a {folio(cot)}. El cliente ahorra {dinero(ahorro)} con IVA incluido.'


# ─────────────────── Reembolso ya hecho fuera del sistema ───────────────────

def _monto(valor) -> Decimal:
    try:
        return impuestos.centavos(Decimal(str(valor).replace(',', '').replace('$', '')))
    except (InvalidOperation, ValueError, TypeError) as e:
        raise OrdenError('No entendí el monto.') from e


def _pago_reembolso(cot: Cotizacion, monto: Decimal, medio: str, referencia: str, notas: str) -> Pago:
    return Pago(cotizacion=cot, tipo='REEMBOLSO', metodo=medio, monto=monto, fecha_pago=timezone.localdate(),
                referencia=referencia, notas=notas[:255])


def _revisar_reembolso(cotizacion_id, monto, medio: str):
    if medio not in MEDIOS_REEMBOLSO:
        raise OrdenError('El reembolso solo se registra como transferencia o efectivo.')
    cot = _cotizacion(cotizacion_id)
    monto = _monto(monto)
    if monto <= 0:
        raise OrdenError('El monto debe ser mayor a cero.')
    _revisar_tope(monto)
    try:
        _pago_reembolso(cot, monto, medio, '', '').full_clean()   # no excede lo cobrado neto
    except ValidationError as e:
        raise OrdenError('; '.join(e.messages)) from e
    return cot, monto


def proponer_reembolso_externo(telefono: str, cotizacion_id, monto, medio: str) -> Optional[Resultado]:
    """Reembolso que Dirección ya entregó por fuera: solo se asienta. No mueve
    dinero ni llama a Openpay."""
    if not direccion_activa(telefono):
        return None
    parametros = {'medio': medio}
    try:
        cot, monto = _revisar_reembolso(cotizacion_id, monto, medio)
    except OrdenError as e:
        return rechazar(telefono, 'REEMBOLSO_EXTERNO', str(e), parametros=parametros,
                        cotizacion=Cotizacion.objects.filter(pk=_pk(cotizacion_id)).first())
    resumen = (f'Registrar el reembolso de {dinero(monto)} a {folio(cot)} ({_cliente(cot)}) por '
               f'{MEDIOS_REEMBOLSO[medio]}, que ya entregaste. El ERP solo lo asienta; no manda dinero.')
    return crear_propuesta(telefono, 'REEMBOLSO_EXTERNO', resumen, cotizacion=cot, monto=monto, parametros=parametros)


def _ejecutar_reembolso(accion: AccionWhatsApp) -> str:
    Cotizacion.objects.select_for_update().get(pk=accion.cotizacion_id)
    cot, monto = _revisar_reembolso(accion.cotizacion_id, accion.monto, accion.parametros.get('medio', ''))
    pago = _pago_reembolso(
        cot, monto, accion.parametros['medio'], f'REEMB-WA-{accion.pk}',
        f'Reembolso hecho fuera del sistema, registrado {_origen(accion)}')
    pago.full_clean()
    pago.save()   # corren los signals de póliza, igual que desde el admin
    accion.pago = pago
    return f'✅ Listo: registré el reembolso de {dinero(monto)} en {folio(cot)}.'


EJECUTORES = {
    'DEVOLUCION_DEPOSITO': _ejecutar_devolucion,
    'DESCUENTO': _ejecutar_descuento,
    'REEMBOLSO_EXTERNO': _ejecutar_reembolso,
}
