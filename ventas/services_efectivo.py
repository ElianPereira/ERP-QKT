"""Control del efectivo que la vendedora cobra a clientes (Issue #505, PR 4).

Solo control: el `Pago` en efectivo ya cargó Caja, así que una entrega no genera póliza. Este módulo
NO compensa nada contra comisiones: no existe (ni debe existir) función que lo haga.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from comercial.models import Pago
from nomina.models import Empleado

from .models import CERO, EntregaEfectivo


def pagos_por_entregar(vendedor):
    """Cobros en efectivo registrados por el usuario de este vendedor y aún no entregados."""
    if vendedor is None or not vendedor.usuario_id:
        return Pago.objects.none()
    pagos = Pago.objects.filter(metodo='EFECTIVO', tipo='INGRESO', usuario_id=vendedor.usuario_id,
                                entregas_efectivo__isnull=True, cotizacion__es_historica=False)
    if vendedor.fecha_alta:
        pagos = pagos.filter(fecha_pago__gte=vendedor.fecha_alta)
    return pagos.select_related('cotizacion').order_by('fecha_pago', 'id')


def saldo_por_entregar(vendedor) -> Decimal:
    return pagos_por_entregar(vendedor).aggregate(t=Sum('monto'))['t'] or CERO


def saldos_pendientes():
    """[(vendedor, saldo)] de quienes aún deben entregar efectivo (lectura para el tablero del PR 5)."""
    filas = []
    for vendedor in Empleado.objects.filter(puesto='VENTAS', usuario__isnull=False):
        saldo = saldo_por_entregar(vendedor)
        if saldo > 0:
            filas.append((vendedor, saldo))
    return filas


def validar_entrega(vendedor, pagos) -> Decimal:
    """Valida que los cobros sean de este vendedor y no estén ya entregados; devuelve su suma.
    Bloquea las filas (dentro de una transacción) para que dos registros no cubran el mismo cobro."""
    pagos = list(pagos)
    if not pagos:
        raise ValidationError('Elige al menos un cobro.')
    ids = [p.pk for p in pagos]
    list(Pago.objects.select_for_update().filter(pk__in=ids))
    validos = set(pagos_por_entregar(vendedor).filter(pk__in=ids).values_list('pk', flat=True))
    if set(ids) != validos:
        raise ValidationError('Algún cobro no es de esa persona, no es efectivo o ya fue entregado.')
    return sum((p.monto for p in pagos), CERO)


@transaction.atomic
def registrar_entrega(vendedor, pagos, recibido_por, fecha, notas='', usuario=None):
    """Dirección registra lo que recibió. El monto es la suma de los cobros que cubre."""
    vendedor = Empleado.objects.select_for_update().get(pk=vendedor.pk)
    monto = validar_entrega(vendedor, pagos)
    entrega = EntregaEfectivo.objects.create(
        vendedor=vendedor, fecha=fecha, monto=monto, recibido_por=recibido_por, notas=notas,
        created_by=usuario if getattr(usuario, 'pk', None) else None)
    entrega.pagos.set(pagos)
    return entrega
