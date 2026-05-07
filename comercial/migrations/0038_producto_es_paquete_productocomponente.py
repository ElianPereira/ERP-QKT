from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0037_espacios_faq_galeria_categorias'),
    ]

    operations = [
        migrations.AddField(
            model_name='producto',
            name='es_paquete',
            field=models.BooleanField(
                default=False,
                help_text='Marca esto si este producto está compuesto por otros PRODUCTOS (no subproductos)',
                verbose_name='¿Es un paquete?',
            ),
        ),
        migrations.CreateModel(
            name='ProductoComponente',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cantidad', models.DecimalField(
                    decimal_places=2,
                    help_text='Cuántas unidades de este producto se incluyen en el paquete',
                    max_digits=10,
                    verbose_name='Cantidad',
                )),
                ('producto_padre', models.ForeignKey(
                    limit_choices_to={'es_paquete': True},
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='productos_incluidos',
                    to='comercial.producto',
                    verbose_name='Paquete',
                )),
                ('producto_hijo', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='incluido_en_paquetes',
                    to='comercial.producto',
                    verbose_name='Producto Incluido',
                )),
            ],
            options={
                'verbose_name': 'Componente de Paquete',
                'verbose_name_plural': 'Componentes de Paquete',
                'unique_together': {('producto_padre', 'producto_hijo')},
            },
        ),
    ]
