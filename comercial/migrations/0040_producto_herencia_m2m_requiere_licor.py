from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0039_producto_herencia_inventario'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='producto',
            name='hereda_inventario_de',
        ),
        migrations.AddField(
            model_name='producto',
            name='hereda_inventario_de',
            field=models.ManyToManyField(
                blank=True,
                help_text='Productos base cuyos subproductos NO se duplicarán al calcular inventario',
                limit_choices_to={'es_upgrade': False},
                related_name='upgrades',
                to='comercial.producto',
                verbose_name='Hereda inventario de',
            ),
        ),
        migrations.AddField(
            model_name='producto',
            name='requiere_licor',
            field=models.BooleanField(
                default=False,
                help_text="Si está activo, la cotización debe incluir 'Licores Nacionales' o 'Licores Premium'",
                verbose_name='¿Requiere licor base en la cotización?',
            ),
        ),
    ]
