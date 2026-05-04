from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0035_landing_imagenes_testimonios'),
    ]

    operations = [
        migrations.AddField(
            model_name='imagenlanding',
            name='posicion_vertical',
            field=models.CharField(
                choices=[
                    ('top', 'Arriba'),
                    ('20%', 'Arriba-centro'),
                    ('center', 'Centro'),
                    ('80%', 'Abajo-centro'),
                    ('bottom', 'Abajo'),
                ],
                default='center',
                help_text='Qué parte de la imagen se muestra: arriba, centro o abajo',
                max_length=10,
                verbose_name='Enfoque vertical',
            ),
        ),
    ]
