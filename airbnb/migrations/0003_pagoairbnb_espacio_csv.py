# Generated manually 2026-04-10

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('airbnb', '0002_alter_reservaairbnb_estado'),
    ]

    operations = [
        migrations.AddField(
            model_name='pagoairbnb',
            name='espacio_csv',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Nombre del listing tal como llegó en el CSV de Airbnb',
                max_length=300,
                verbose_name='Espacio (CSV)',
            ),
            preserve_default=False,
        ),
    ]
