from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0031_espacio_cotizacion_tipo_servicio_asignacionpersonal_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='producto',
            name='visible_cotizador',
            field=models.BooleanField(default=False, verbose_name='Mostrar en cotizador web'),
        ),
        migrations.AddField(
            model_name='producto',
            name='grupo_cotizador',
            field=models.CharField(blank=True, choices=[('ENTRETENIMIENTO', 'Entretenimiento'), ('COMIDA', 'Comida'), ('MOBILIARIO', 'Mobiliario'), ('DECORACION', 'Decoración'), ('INFANTIL', 'Infantil'), ('OTRO', 'Otros')], max_length=20, verbose_name='Grupo en cotizador'),
        ),
        migrations.AddField(
            model_name='producto',
            name='icono',
            field=models.CharField(blank=True, help_text='Emoji, ej: 🎧', max_length=10),
        ),
        migrations.AddField(
            model_name='producto',
            name='descripcion_corta',
            field=models.CharField(blank=True, help_text='Texto debajo del nombre en el cotizador', max_length=120, verbose_name='Descripción corta'),
        ),
        migrations.AddField(
            model_name='producto',
            name='orden_cotizador',
            field=models.PositiveIntegerField(default=0, verbose_name='Orden en cotizador'),
        ),
        migrations.AddField(
            model_name='producto',
            name='grupo_exclusion',
            field=models.CharField(blank=True, help_text='Productos con el mismo valor son mutuamente exclusivos. Ej: DJ', max_length=30, verbose_name='Grupo de exclusión'),
        ),
        migrations.AddField(
            model_name='producto',
            name='cantidad_por_persona',
            field=models.BooleanField(default=False, help_text='Si activo, cantidad = ceil(personas / factor)', verbose_name='Cantidad según personas'),
        ),
        migrations.AddField(
            model_name='producto',
            name='factor_personas',
            field=models.PositiveIntegerField(default=1, help_text='Ej: 10 → una unidad cada 10 personas', verbose_name='Factor divisor'),
        ),
        migrations.AddField(
            model_name='producto',
            name='cotizador_evento',
            field=models.BooleanField(default=False, verbose_name='Disponible para Evento'),
        ),
        migrations.AddField(
            model_name='producto',
            name='cotizador_pasadia',
            field=models.BooleanField(default=False, verbose_name='Disponible para Pasadía'),
        ),
        migrations.AddField(
            model_name='producto',
            name='cotizador_hospedaje',
            field=models.BooleanField(default=False, verbose_name='Disponible para Hospedaje'),
        ),
    ]
