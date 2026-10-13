"""
Clasificación de Compras por proveedor.

La categoría de una Compra define la cuenta de gasto de su póliza. Se asigna
sola desde la configuración del proveedor (`Proveedor.categoria_gasto` /
`cuenta_gasto`), y el proveedor la aprende la primera vez que alguien
clasifica una de sus compras. Reclasificar solo reapunta la línea de gasto:
importes, IVA, banco y folio no cambian, así que la póliza sigue cuadrada.
"""
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from .models import CuentaContable, MovimientoContable, Poliza
from .signals import cuenta_gasto_de_compra, get_cuenta


def _polizas_de_compra(compra):
    return Poliza.objects.filter(
        content_type=ContentType.objects.get_for_model(compra), object_id=compra.pk, origen='COMPRA',
    ).exclude(estado='CANCELADA')


def _lineas_de_gasto(compra):
    """Cargos de las pólizas de la Compra que no son IVA ni su cuenta de pago."""
    fijas = {c.pk for c in (get_cuenta('IVA_ACREDITABLE'),) if c}
    if compra.cuenta_pago_id and compra.cuenta_pago.cuenta_contable_id:
        fijas.add(compra.cuenta_pago.cuenta_contable_id)
    return MovimientoContable.objects.filter(poliza__in=_polizas_de_compra(compra), debe__gt=0) \
        .exclude(cuenta_id__in=fijas)


def reclasificar_compra(compra, cuenta=None):
    """
    Reapunta la línea de gasto de la póliza de la Compra a `cuenta` (por
    defecto, la que le toca por proveedor/categoría) y pasa sus líneas sin
    clasificar a la categoría de la Compra. Devuelve cuántas líneas contables
    cambiaron.
    """
    cuenta = cuenta or cuenta_gasto_de_compra(compra)
    if cuenta is None:
        return 0
    with transaction.atomic():
        if compra.categoria != 'SIN_CLASIFICAR':
            compra.gastos.filter(categoria='SIN_CLASIFICAR').update(categoria=compra.categoria)
        return _lineas_de_gasto(compra).exclude(cuenta=cuenta).update(cuenta=cuenta)


def recordar_en_proveedor(compra):
    """
    El proveedor aprende la categoría de la Compra si aún no tiene una, y la
    aplica a sus otras compras sin clasificar. Devuelve cuántas compras más
    quedaron clasificadas.
    """
    proveedor = compra.proveedor if compra.proveedor_id else None
    if not proveedor or proveedor.categoria_gasto or compra.categoria == 'SIN_CLASIFICAR':
        return 0
    proveedor.categoria_gasto = compra.categoria
    proveedor.save(update_fields=['categoria_gasto'])
    return aplicar_clasificacion_proveedor(proveedor)


def aplicar_clasificacion_proveedor(proveedor):
    """
    Aplica la configuración del proveedor a sus compras: las sin clasificar
    toman su categoría, y las de su categoría se reapuntan a su cuenta. Las
    que alguien clasificó distinto no se tocan. Devuelve cuántas compras
    cambiaron.
    """
    categorias = ['SIN_CLASIFICAR'] + ([proveedor.categoria_gasto] if proveedor.categoria_gasto else [])
    cambiadas = 0
    for compra in proveedor.compras.filter(categoria__in=categorias).select_related('cuenta_pago', 'proveedor'):
        cambio = False
        if compra.categoria == 'SIN_CLASIFICAR' and proveedor.categoria_gasto:
            compra.categoria = proveedor.categoria_gasto
            compra.save(update_fields=['categoria'])
            cambio = True
        if reclasificar_compra(compra) or cambio:
            cambiadas += 1
    return cambiadas


# Cuentas que dejaron de recibir movimientos con el catálogo de categorías
# (Issue #390): lo asentado ahí fuera de una Compra pasa a su cuenta nueva.
CUENTAS_RETIRADAS = {
    '601.03.04': '602.03',     # Comisiones bancarias → gasto financiero
    '601.05': '601.05.02',     # No deducibles (sin grupo) → administración
}


def reclasificar_historico(desde=None, aplicar=False):
    """
    Reapunta lo ya asentado al catálogo de categorías: la línea de gasto de
    cada Compra a la cuenta que hoy le toca (categoría + deducibilidad) y los
    asientos de otras pólizas en `CUENTAS_RETIRADAS` a su cuenta nueva. Solo
    cambia la cuenta: importes, folios y cuadre no se tocan. Sin `aplicar`
    solo devuelve lo que cambiaría.

    Devuelve {'compras': [(compra, [(cuenta_actual, cuenta_nueva, importe)])],
              'asientos': [(movimiento, cuenta_nueva)]}.
    """
    from comercial.models import Compra

    resultado = {'compras': [], 'asientos': []}
    compras = Compra.objects.exclude(proveedor__cobra_de_la_cuenta=True) \
        .select_related('proveedor', 'proveedor__cuenta_gasto', 'cuenta_pago')
    if desde:
        compras = compras.filter(fecha_emision__gte=desde)
    for compra in compras.order_by('fecha_emision', 'pk'):
        nueva = cuenta_gasto_de_compra(compra)
        if nueva is None:
            continue
        cambios = [(mov.cuenta, nueva, mov.debe)
                   for mov in _lineas_de_gasto(compra).exclude(cuenta=nueva).select_related('cuenta')]
        if cambios:
            resultado['compras'].append((compra, cambios))
            if aplicar:
                reclasificar_compra(compra, cuenta=nueva)

    destinos = {c.codigo_sat: c for c in CuentaContable.objects.filter(codigo_sat__in=CUENTAS_RETIRADAS.values())}
    asientos = MovimientoContable.objects.filter(cuenta__codigo_sat__in=CUENTAS_RETIRADAS) \
        .exclude(poliza__estado='CANCELADA').exclude(poliza__origen='COMPRA') \
        .select_related('cuenta', 'poliza').order_by('poliza__fecha', 'pk')
    if desde:
        asientos = asientos.filter(poliza__fecha__gte=desde)
    with transaction.atomic():
        for mov in asientos:
            nueva = destinos.get(CUENTAS_RETIRADAS[mov.cuenta.codigo_sat])
            if nueva is None:
                continue
            resultado['asientos'].append((mov, nueva))
            if aplicar:
                MovimientoContable.objects.filter(pk=mov.pk).update(cuenta=nueva)
    return resultado
