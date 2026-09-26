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

from .models import Poliza
from .signals import cuenta_gasto_de_compra, get_cuenta


def _polizas_de_compra(compra):
    return Poliza.objects.filter(
        content_type=ContentType.objects.get_for_model(compra), object_id=compra.pk, origen='COMPRA',
    ).exclude(estado='CANCELADA')


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
    fijas = {c.pk for c in (get_cuenta('IVA_ACREDITABLE'),) if c}
    if compra.cuenta_pago_id and compra.cuenta_pago.cuenta_contable_id:
        fijas.add(compra.cuenta_pago.cuenta_contable_id)

    cambiadas = 0
    with transaction.atomic():
        if compra.categoria != 'SIN_CLASIFICAR':
            compra.gastos.filter(categoria='SIN_CLASIFICAR').update(categoria=compra.categoria)
        for poliza in _polizas_de_compra(compra):
            cambiadas += poliza.movimientos.filter(debe__gt=0).exclude(cuenta_id__in=fijas) \
                .exclude(cuenta=cuenta).update(cuenta=cuenta)
    return cambiadas


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
