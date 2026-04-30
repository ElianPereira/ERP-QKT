from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0034_simplify_estados'),
    ]

    operations = [
        migrations.CreateModel(
            name='ImagenLanding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('seccion', models.CharField(choices=[('HERO', 'Banner principal'), ('EVENTO', 'Servicio — Eventos'), ('PASADIA', 'Servicio — Pasadía'), ('HOSPEDAJE', 'Servicio — Hospedaje'), ('GALERIA', 'Galería de fotos')], max_length=20, verbose_name='Sección')),
                ('imagen', models.ImageField(upload_to='landing/', verbose_name='Imagen')),
                ('titulo', models.CharField(blank=True, max_length=120, verbose_name='Título / descripción interna')),
                ('alt_text', models.CharField(blank=True, help_text='Describe la imagen para accesibilidad y SEO', max_length=200, verbose_name='Texto alternativo')),
                ('orden', models.PositiveIntegerField(default=0, verbose_name='Orden')),
                ('activo', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Imagen de landing',
                'verbose_name_plural': 'Imágenes de landing',
                'ordering': ['seccion', 'orden'],
            },
        ),
        migrations.CreateModel(
            name='TestimonioLanding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=100, verbose_name='Nombre del cliente')),
                ('evento', models.CharField(help_text='Ej: Boda · 150 invitados', max_length=100, verbose_name='Tipo de evento')),
                ('texto', models.TextField(verbose_name='Testimonio')),
                ('estrellas', models.PositiveIntegerField(default=5, verbose_name='Estrellas (1-5)')),
                ('activo', models.BooleanField(default=True)),
                ('orden', models.PositiveIntegerField(default=0)),
            ],
            options={
                'verbose_name': 'Testimonio',
                'verbose_name_plural': 'Testimonios',
                'ordering': ['orden'],
            },
        ),
    ]
