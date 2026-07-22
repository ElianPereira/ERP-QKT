import django.db.models.deletion
from django.db import migrations, models


def vincular_proveedores_existentes(apps, schema_editor):
    """Backfill: las Compras que ya existían (con proveedor_nombre/rfc_emisor
    de texto) no se vuelven a guardar solas con el nuevo Compra.save(), así
    que aquí se resuelve/crea su Proveedor del catálogo una sola vez,
    replicando la misma lógica (RFC primero, luego nombre, si no existe se
    crea)."""
    Compra = apps.get_model('comercial', 'Compra')
    Proveedor = apps.get_model('comercial', 'Proveedor')

    cache_por_rfc = {}
    cache_por_nombre = {}

    for compra in Compra.objects.filter(proveedor__isnull=True).exclude(proveedor_nombre=''):
        nombre = (compra.proveedor_nombre or '').strip()
        rfc = (compra.rfc_emisor or '').strip().upper()
        if not nombre:
            continue

        proveedor = None
        if rfc:
            proveedor = cache_por_rfc.get(rfc)
            if proveedor is None:
                proveedor = Proveedor.objects.filter(rfc=rfc).first()
                if proveedor:
                    cache_por_rfc[rfc] = proveedor

        if proveedor is None:
            clave_nombre = nombre.lower()
            proveedor = cache_por_nombre.get(clave_nombre)
            if proveedor is None:
                proveedor = Proveedor.objects.filter(nombre__iexact=nombre).first()
                if proveedor is None:
                    proveedor = Proveedor.objects.create(nombre=nombre, rfc=rfc)
                elif rfc and not proveedor.rfc:
                    proveedor.rfc = rfc
                    proveedor.save(update_fields=['rfc'])
                cache_por_nombre[clave_nombre] = proveedor
            if rfc:
                cache_por_rfc[rfc] = proveedor

        compra.proveedor = proveedor
        compra.save(update_fields=['proveedor'])


def revertir(apps, schema_editor):
    # No se desvincula nada al revertir: el campo `proveedor` (FK) se elimina
    # de todas formas al revertir la operación AddField subsecuente.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("comercial", "0057_compra_es_deducible"),
    ]

    operations = [
        migrations.AddField(
            model_name="proveedor",
            name="rfc",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Se usa para emparejar automáticamente las facturas (XML) de Compras con este proveedor.",
                max_length=13,
                verbose_name="RFC",
            ),
        ),
        migrations.RenameField(
            model_name="compra",
            old_name="proveedor",
            new_name="proveedor_nombre",
        ),
        migrations.AlterField(
            model_name="compra",
            name="proveedor_nombre",
            field=models.CharField(
                blank=True,
                help_text="Se autocompleta con el nombre del Emisor al subir el XML. No se edita directamente: usa el campo 'Proveedor' de abajo.",
                max_length=200,
                verbose_name="Proveedor (texto de la factura)",
            ),
        ),
        migrations.AddField(
            model_name="compra",
            name="proveedor",
            field=models.ForeignKey(
                blank=True,
                help_text="Se busca/crea automáticamente en el catálogo de Proveedores por RFC o nombre al guardar. Puedes corregirlo a mano si el emparejamiento automático no fue el correcto.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="compras",
                to="comercial.proveedor",
                verbose_name="Proveedor",
            ),
        ),
        migrations.RunPython(vincular_proveedores_existentes, revertir),
    ]
