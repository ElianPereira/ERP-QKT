"""
Borra las tablas huérfanas de "catalogo_*": un feature de PDF de catálogo
con precios sincronizados que el propietario confirmó como retirado (no
funcionó la idea). El código se quitó en su momento pero la migración que
creó las tablas nunca se revirtió en la base de datos real — se quedaron
sin ningún modelo Django que las administre, con una FK activa hacia
comercial_producto que bloqueaba con un 500 el borrado de cualquier
producto que esas tablas referenciaran (Django no conoce la tabla, así
que ni siquiera puede mostrar la pantalla normal de "objetos protegidos").

Confirmado con `python manage.py inspeccionar_tablas_huerfanas` contra
producción: solo dos tablas con ese prefijo, `catalogo_paquetecatalogo`
(3 filas, todas con producto_id NULL) y `catalogo_tarjetacatalogo` (13
filas, algunas sí enlazadas a un producto real). El propietario autorizó
borrar el contenido sin conservarlo — el feature completo se descartó.

Descubre las tablas por prefijo en vez de nombrarlas a mano: así cubre
también cualquier tabla del mismo feature que no haya aparecido en el
reporte anterior (ej. una tabla de "secciones" referenciada por
`seccion_id` en tarjetacatalogo), sin adivinar su nombre exacto.
CASCADE porque esas tablas huérfanas pueden referenciarse entre sí
(tarjetacatalogo → seccion), y ninguna tiene un modelo Django que
dependa de mantener esa integridad.
"""

from django.db import migrations


def borrar_tablas_huerfanas_catalogo(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        # Estas tablas solo existen en la base de datos real de producción
        # (creadas fuera de las migraciones de este proyecto) — en dev/CI
        # con SQLite nunca existieron, no hay nada que borrar.
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name LIKE 'catalogo\\_%' ESCAPE '\\'
        """)
        tablas = [fila[0] for fila in cursor.fetchall()]

        for tabla in tablas:
            cursor.execute(f'DROP TABLE IF EXISTS "{tabla}" CASCADE')  # noqa: S608 — nombre viene de information_schema, no de entrada externa


def sin_reversa(apps, schema_editor):
    # Irreversible a propósito: son tablas huérfanas de un feature retirado,
    # sin modelo Django — no hay nada real que "recrear" hacia atrás.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0084_remove_combotaquiza_created_by_and_more'),
    ]

    operations = [
        migrations.RunPython(borrar_tablas_huerfanas_catalogo, sin_reversa),
    ]
