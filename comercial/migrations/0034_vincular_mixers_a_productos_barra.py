"""
Data migration: vincula el SubProducto de Refrescos/Mezcladores
a los 3 productos de barra con las cantidades correctas.
"""
from decimal import Decimal
from django.db import migrations


PRODUCTOS_CANTIDADES = [
    ('Cerveza Nacional Para 10', Decimal('0.50')),
    ('Licores Nacionales 10', Decimal('1.00')),
    ('Licores Premium Para 10', Decimal('1.00')),
]

SUBPRODUCTO_BUSCAR = 'Refrescos Y Mezcladores Para 10'


def vincular_mixers(apps, schema_editor):
    SubProducto = apps.get_model('comercial', 'SubProducto')
    Producto = apps.get_model('comercial', 'Producto')
    ComponenteProducto = apps.get_model('comercial', 'ComponenteProducto')

    sub = SubProducto.objects.filter(nombre__icontains=SUBPRODUCTO_BUSCAR).first()
    if not sub:
        print(f'  [SKIP] SubProducto "{SUBPRODUCTO_BUSCAR}" no encontrado')
        return

    for nombre_parcial, cantidad in PRODUCTOS_CANTIDADES:
        prod = Producto.objects.filter(nombre__icontains=nombre_parcial).first()
        if not prod:
            print(f'  [SKIP] Producto "{nombre_parcial}" no encontrado')
            continue

        comp, created = ComponenteProducto.objects.get_or_create(
            producto=prod,
            subproducto=sub,
            defaults={'cantidad': cantidad},
        )
        if not created and comp.cantidad != cantidad:
            comp.cantidad = cantidad
            comp.save()
            print(f'  [UPDATE] {prod.nombre} -> cantidad={cantidad}')
        elif created:
            print(f'  [CREATE] {prod.nombre} -> {sub.nombre} x{cantidad}')
        else:
            print(f'  [OK] {prod.nombre} ya vinculado con cantidad={comp.cantidad}')


def desvincular_mixers(apps, schema_editor):
    SubProducto = apps.get_model('comercial', 'SubProducto')
    Producto = apps.get_model('comercial', 'Producto')
    ComponenteProducto = apps.get_model('comercial', 'ComponenteProducto')

    sub = SubProducto.objects.filter(nombre__icontains=SUBPRODUCTO_BUSCAR).first()
    if not sub:
        return

    for nombre_parcial, _ in PRODUCTOS_CANTIDADES:
        prod = Producto.objects.filter(nombre__icontains=nombre_parcial).first()
        if prod:
            ComponenteProducto.objects.filter(producto=prod, subproducto=sub).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0033_populate_grupo_categoria_barra'),
    ]

    operations = [
        migrations.RunPython(vincular_mixers, desvincular_mixers),
    ]
