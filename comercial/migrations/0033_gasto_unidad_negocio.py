"""
Agrega unidad_negocio a Gasto y la hereda de la Compra padre
para todos los gastos existentes.
"""
import django.db.models.deletion
from django.db import migrations, models


def heredar_unidad_negocio(apps, schema_editor):
    """Copia unidad_negocio de Compra → Gasto para registros históricos."""
    Gasto = apps.get_model("comercial", "Gasto")
    updates = []
    for gasto in Gasto.objects.select_related("compra").filter(
        unidad_negocio__isnull=True,
        compra__unidad_negocio__isnull=False,
    ).iterator(chunk_size=500):
        gasto.unidad_negocio = gasto.compra.unidad_negocio
        updates.append(gasto)
        if len(updates) >= 500:
            Gasto.objects.bulk_update(updates, ["unidad_negocio"])
            updates = []
    if updates:
        Gasto.objects.bulk_update(updates, ["unidad_negocio"])


class Migration(migrations.Migration):

    dependencies = [
        ("comercial", "0032_gasto_categoria_fk"),
        ("contabilidad", "0007_rename_contabilida_codigo__a1b2c3_idx_contabilida_codigo__b58027_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="gasto",
            name="unidad_negocio",
            field=models.ForeignKey(
                blank=True,
                help_text="Se hereda de la Compra al crear. Editable por línea.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="contabilidad.unidadnegocio",
                verbose_name="Unidad de Negocio",
            ),
        ),
        migrations.RunPython(heredar_unidad_negocio, migrations.RunPython.noop),
    ]
