from django.core.management import call_command
from django.db import migrations

TABLA = 'qkt_cache'


def crear_tabla_cache(apps, schema_editor):
    # createcachetable es idempotente: si la tabla ya existe, no hace nada.
    call_command(
        'createcachetable',
        TABLA,
        database=schema_editor.connection.alias,
        verbosity=0,
    )


def borrar_tabla_cache(apps, schema_editor):
    schema_editor.execute(f'DROP TABLE IF EXISTS {TABLA}')


class Migration(migrations.Migration):
    dependencies = [('comercial', '0068_alter_compra_archivo_pdf_alter_compra_archivo_xml_and_more')]
    operations = [migrations.RunPython(crear_tabla_cache, borrar_tabla_cache)]
