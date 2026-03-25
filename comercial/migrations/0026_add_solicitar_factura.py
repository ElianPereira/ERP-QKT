# Migration to add solicitar_factura field to Pago

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0025_recordatorio_pago'),  # Ajusta según tu última migración
    ]

    operations = [
        migrations.AddField(
            model_name='pago',
            name='solicitar_factura',
            field=models.BooleanField(
                default=False,
                verbose_name='¿Solicitar factura?',
                help_text='Genera automáticamente una solicitud de factura al guardar'
            ),
        ),
    ]