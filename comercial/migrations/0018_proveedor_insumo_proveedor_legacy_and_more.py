from django.db import migrations, models
import django.db.models.deletion


def migrar_proveedores_texto_a_fk(apps, schema_editor):
    """
    Paso de datos: toma el texto de proveedor_legacy, crea registros
    en la tabla Proveedor y vincula cada Insumo.
    """
    Insumo = apps.get_model('comercial', 'Insumo')
    Proveedor = apps.get_model('comercial', 'Proveedor')

    for insumo in Insumo.objects.exclude(proveedor_legacy='').exclude(proveedor_legacy__isnull=True):
        nombre_limpio = insumo.proveedor_legacy.strip()
        if not nombre_limpio:
            continue
        proveedor, _ = Proveedor.objects.get_or_create(nombre=nombre_limpio)
        insumo.proveedor = proveedor
        insumo.save(update_fields=['proveedor'])


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0017_insumo_presentacion_insumo_proveedor_plantillabarra'),
    ]

    operations = [
        # 1. Crear tabla Proveedor
        migrations.CreateModel(
            name='Proveedor',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=200, unique=True, verbose_name='Nombre / Razón Social')),
                ('contacto', models.CharField(blank=True, max_length=200, verbose_name='Persona de Contacto')),
                ('telefono', models.CharField(blank=True, max_length=20)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('notas', models.TextField(blank=True, help_text='Horarios, condiciones de pago, dirección, etc.', verbose_name='Notas')),
                ('activo', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Proveedor',
                'verbose_name_plural': 'Proveedores',
                'ordering': ['nombre'],
            },
        ),

        # 2. Renombrar el campo texto viejo a proveedor_legacy
        migrations.RenameField(
            model_name='insumo',
            old_name='proveedor',
            new_name='proveedor_legacy',
        ),

        # 3. Hacer proveedor_legacy no editable
        migrations.AlterField(
            model_name='insumo',
            name='proveedor_legacy',
            field=models.CharField(blank=True, editable=False, max_length=200, verbose_name='Proveedor (texto antiguo)'),
        ),

        # 4. Crear el nuevo campo FK (nullable, sin datos aún)
        migrations.AddField(
            model_name='insumo',
            name='proveedor',
            field=models.ForeignKey(
                blank=True, null=True,
                help_text='Selecciona el proveedor de este insumo',
                on_delete=django.db.models.deletion.SET_NULL,
                to='comercial.proveedor',
                verbose_name='Proveedor',
            ),
        ),

        # 5. Migrar datos: texto → registros Proveedor → vincular FK
        migrations.RunPython(
            migrar_proveedores_texto_a_fk,
            migrations.RunPython.noop,
        ),
    ]