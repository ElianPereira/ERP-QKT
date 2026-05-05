from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0036_imagenlanding_posicion_vertical'),
    ]

    operations = [
        migrations.AddField(
            model_name='imagenlanding',
            name='categoria_galeria',
            field=models.CharField(
                blank=True,
                choices=[
                    ('BODAS', 'Bodas'),
                    ('EVENTOS', 'Eventos Sociales'),
                    ('PASADIA', 'Pasadía'),
                    ('ESPACIOS', 'Espacios'),
                    ('GENERAL', 'General'),
                ],
                default='GENERAL',
                help_text='Solo aplica a imágenes de la sección Galería',
                max_length=20,
                verbose_name='Categoría (galería)',
            ),
        ),
        migrations.CreateModel(
            name='EspacioLanding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=100, verbose_name='Nombre del espacio')),
                ('imagen', models.ImageField(upload_to='landing/', verbose_name='Imagen')),
                ('capacidad', models.CharField(help_text='Ej: Hasta 200 invitados', max_length=80, verbose_name='Capacidad')),
                ('descripcion', models.CharField(blank=True, max_length=200, verbose_name='Descripción corta')),
                ('posicion_vertical', models.CharField(
                    choices=[('top', 'Arriba'), ('20%', 'Arriba-centro'), ('center', 'Centro'), ('80%', 'Abajo-centro'), ('bottom', 'Abajo')],
                    default='center', max_length=10, verbose_name='Enfoque vertical',
                )),
                ('orden', models.PositiveIntegerField(default=0)),
                ('activo', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Espacio',
                'verbose_name_plural': 'Página Web — Espacios',
                'ordering': ['orden'],
            },
        ),
        migrations.CreateModel(
            name='PreguntaFrecuente',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('pregunta', models.CharField(max_length=200, verbose_name='Pregunta')),
                ('respuesta', models.TextField(verbose_name='Respuesta')),
                ('orden', models.PositiveIntegerField(default=0)),
                ('activo', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Pregunta frecuente',
                'verbose_name_plural': 'Página Web — Preguntas Frecuentes',
                'ordering': ['orden'],
            },
        ),
    ]
