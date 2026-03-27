from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0005_cargar_configuracion_contable'),
    ]

    operations = [
        migrations.AddField(
            model_name='unidadnegocio',
            name='rfc',
            field=models.CharField(blank=True, max_length=13, verbose_name='RFC'),
        ),
        migrations.AddField(
            model_name='unidadnegocio',
            name='razon_social',
            field=models.CharField(blank=True, max_length=300, verbose_name='Razón Social'),
        ),
    ]
