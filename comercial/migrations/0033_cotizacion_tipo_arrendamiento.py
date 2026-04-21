from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0032_producto_cotizador_fields'),
    ]

    operations = [
        # Renombrar campo en Producto
        migrations.RenameField(
            model_name='producto',
            old_name='cotizador_hospedaje',
            new_name='cotizador_arrendamiento',
        ),
        migrations.AlterField(
            model_name='producto',
            name='cotizador_arrendamiento',
            field=models.BooleanField(default=False, verbose_name='Disponible para Arrendamiento de Mobiliario'),
        ),
        # Actualizar choices de Cotizacion.tipo_servicio
        migrations.AlterField(
            model_name='cotizacion',
            name='tipo_servicio',
            field=models.CharField(
                choices=[
                    ('EVENTO', 'Evento'),
                    ('PASADIA', 'Pasadía'),
                    ('ARRENDAMIENTO', 'Arrendamiento de Mobiliario'),
                ],
                default='EVENTO',
                max_length=15,
                verbose_name='Tipo de servicio',
            ),
        ),
        # Actualizar choices de ContratoServicio.tipo_servicio
        migrations.AlterField(
            model_name='contratoservicio',
            name='tipo_servicio',
            field=models.CharField(
                choices=[
                    ('EVENTO', 'Evento'),
                    ('PASADIA', 'Pasadía'),
                    ('ARRENDAMIENTO', 'Arrendamiento de Mobiliario'),
                ],
                default='EVENTO',
                max_length=20,
            ),
        ),
    ]
