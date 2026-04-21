"""
Migración manual: Gasto.categoria CharField → FK a CategoriaGasto.

Pasos:
1. Renombra 'categoria' → 'categoria_legacy' (preserva datos)
2. Agrega nueva FK 'categoria' apuntando a CategoriaGasto (nullable)
3. Copia datos: mapea el valor string de categoria_legacy al FK
4. Elimina 'categoria_legacy'
"""
import django.db.models.deletion
from django.db import migrations, models


def migrate_categoria_data(apps, schema_editor):
    """Mapea el CharField legacy a la FK nueva usando la clave."""
    Gasto = apps.get_model("comercial", "Gasto")
    CategoriaGasto = apps.get_model("configuracion", "CategoriaGasto")

    categorias_map = {c.clave: c for c in CategoriaGasto.objects.all()}

    gastos = Gasto.objects.all().only("pk", "categoria_legacy")
    updates = []
    for gasto in gastos.iterator(chunk_size=500):
        clave = gasto.categoria_legacy or "SIN_CLASIFICAR"
        cat = categorias_map.get(clave)
        if cat:
            gasto.categoria = cat
            updates.append(gasto)
        if len(updates) >= 500:
            Gasto.objects.bulk_update(updates, ["categoria"])
            updates = []
    if updates:
        Gasto.objects.bulk_update(updates, ["categoria"])


def reverse_categoria_data(apps, schema_editor):
    """Copia el FK de vuelta al CharField legacy."""
    Gasto = apps.get_model("comercial", "Gasto")
    gastos = Gasto.objects.select_related("categoria").all()
    updates = []
    for gasto in gastos.iterator(chunk_size=500):
        gasto.categoria_legacy = (
            gasto.categoria.clave if gasto.categoria else "SIN_CLASIFICAR"
        )
        updates.append(gasto)
        if len(updates) >= 500:
            Gasto.objects.bulk_update(updates, ["categoria_legacy"])
            updates = []
    if updates:
        Gasto.objects.bulk_update(updates, ["categoria_legacy"])


class Migration(migrations.Migration):

    dependencies = [
        ("comercial", "0031_espacio_cotizacion_tipo_servicio_asignacionpersonal_and_more"),
        ("configuracion", "0002_seed_categorias_gasto"),
    ]

    operations = [
        # 1. Renombrar el CharField existente
        migrations.RenameField(
            model_name="gasto",
            old_name="categoria",
            new_name="categoria_legacy",
        ),
        # 2. Agregar la nueva FK (nullable)
        migrations.AddField(
            model_name="gasto",
            name="categoria",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="gastos",
                to="configuracion.categoriagasto",
                verbose_name="Categoría",
            ),
        ),
        # 3. Copiar datos del CharField al FK
        migrations.RunPython(migrate_categoria_data, reverse_categoria_data),
        # 4. Eliminar el CharField legacy
        migrations.RemoveField(
            model_name="gasto",
            name="categoria_legacy",
        ),
    ]
