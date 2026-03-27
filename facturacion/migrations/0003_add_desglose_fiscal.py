from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0002_update_solicitud"),
    ]

    operations = [
        migrations.AddField(
            model_name="solicitudfactura",
            name="subtotal",
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=12, verbose_name="Subtotal"
            ),
        ),
        migrations.AddField(
            model_name="solicitudfactura",
            name="iva",
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=12, verbose_name="IVA"
            ),
        ),
        migrations.AddField(
            model_name="solicitudfactura",
            name="retencion_isr",
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=12, verbose_name="Retención ISR"
            ),
        ),
        migrations.AddField(
            model_name="solicitudfactura",
            name="retencion_iva",
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=12, verbose_name="Retención IVA"
            ),
        ),
    ]
