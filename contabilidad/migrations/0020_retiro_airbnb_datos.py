"""
Borra todos los datos de Airbnb del ERP (Issue #311, fase 2).

La lógica vive en `contabilidad/retiro_airbnb.py` para que el diagnóstico de
solo lectura (`/admin/contabilidad/reportes/retiro-airbnb/`) y este borrado
usen exactamente el mismo alcance. Si hay datos de la Quinta que dependen de
algo de Airbnb, la migración falla sin borrar nada.

Irreversible: la reversa no restaura nada.
"""
from django.db import migrations


def borrar_datos_airbnb(apps, schema_editor):
    from contabilidad.retiro_airbnb import ejecutar
    ejecutar(apps)


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0019_retiro_airbnb_choices'),
        ('comercial', '0099_retiro_airbnb_textos'),
        ('facturacion', '0009_retiro_airbnb_linea_negocio'),
        ('reportes', '0003_retiro_reportes_airbnb'),
    ]

    operations = [
        migrations.RunPython(borrar_datos_airbnb, migrations.RunPython.noop),
    ]
