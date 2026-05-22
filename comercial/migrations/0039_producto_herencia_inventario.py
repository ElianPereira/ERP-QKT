from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0038_producto_es_paquete_productocomponente'),
    ]

    operations = [
        migrations.AddField(
            model_name='producto',
            name='es_upgrade',
            field=models.BooleanField(
                default=False,
                verbose_name='¿Es un upgrade?',
                help_text='Marca si este producto amplía a otro (sus subproductos base no se duplican en inventario)',
            ),
        ),
        migrations.AddField(
            model_name='producto',
            name='hereda_inventario_de',
            field=models.ForeignKey(
                blank=True,
                help_text='Producto base cuyos subproductos NO se duplicarán al calcular inventario',
                limit_choices_to={'es_upgrade': False},
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='upgrades',
                to='comercial.producto',
                verbose_name='Hereda inventario de',
            ),
        ),
    ]
