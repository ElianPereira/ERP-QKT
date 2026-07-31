"""Etiqueta explícitamente `precio_unitario` como base sin IVA.

Solo cambia verbose_name y help_text: no altera el tipo de columna ni los
datos almacenados.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("comercial", "0066_alter_pago_options"),
    ]

    operations = [
        migrations.AlterField(
            model_name="itemcotizacion",
            name="precio_unitario",
            field=models.DecimalField(
                decimal_places=2,
                default=0.00,
                help_text=(
                    "Base gravable. El IVA se calcula sobre el subtotal en "
                    "calcular_totales(), nunca por línea."
                ),
                max_digits=10,
                verbose_name="Precio unitario (sin IVA)",
            ),
        ),
    ]
