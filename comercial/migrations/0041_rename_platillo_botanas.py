from django.db import migrations


def rename_platillo(apps, schema_editor):
    Producto = apps.get_model('comercial', 'Producto')
    Producto.objects.filter(nombre='Platillo Y Botanas').update(nombre='Platillo Gourmet Y Botanas')


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0040_producto_herencia_m2m_requiere_licor'),
    ]

    operations = [
        migrations.RunPython(rename_platillo, migrations.RunPython.noop),
    ]
