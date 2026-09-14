"""
Solo lectura — no modifica nada. Encuentra tablas en la base de datos real
que tienen una llave foránea hacia comercial_producto (o comercial_cotizacion,
por si acaso) pero que NO corresponden a ningún modelo de Django instalado
en este proyecto — es decir, huérfanas de una app o feature que se quitó del
código sin revertir su migración.

Se originó al descubrir `catalogo_tarjetacatalogo`: una tabla real en
producción, con una FK activa hacia comercial_producto, que bloqueaba el
borrado de productos con un 500 (IntegrityError sin capturar, porque Django
no conoce esa tabla y no puede mostrar la pantalla normal de "objetos
protegidos"). El propietario confirmó que es un feature de "PDF de catálogo
con precios actualizados" que se intentó y se retiró — la tabla se quedó
sin que nadie la borrara.

Uso en Railway:

    python manage.py inspeccionar_tablas_huerfanas
"""

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Audita, sin modificar nada, tablas reales sin modelo Django que referencian Producto/Cotizacion."

    def handle(self, *args, **opciones):
        if connection.vendor != 'postgresql':
            self.stdout.write(self.style.ERROR(
                f"Este comando usa information_schema al estilo PostgreSQL — el motor actual "
                f"es '{connection.vendor}' (dev local). Solo tiene sentido correrlo contra producción."
            ))
            return

        tablas_conocidas = {m._meta.db_table for m in apps.get_models()}

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    tc.table_name AS tabla_huerfana,
                    kcu.column_name AS columna_fk,
                    ccu.table_name AS tabla_referenciada,
                    tc.constraint_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                JOIN information_schema.constraint_column_usage ccu
                    ON tc.constraint_name = ccu.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND ccu.table_name IN ('comercial_producto', 'comercial_cotizacion')
                ORDER BY tabla_huerfana;
            """)
            filas = cursor.fetchall()

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'{len(filas)} llave(s) foránea(s) apuntando a comercial_producto/comercial_cotizacion'
        ))

        huerfanas = set()
        for tabla, columna, referenciada, constraint in filas:
            conocida = tabla in tablas_conocidas
            marca = self.style.SUCCESS('conocida') if conocida else self.style.ERROR('HUÉRFANA — sin modelo Django')
            self.stdout.write(f'  {tabla:<40} {columna:<20} → {referenciada:<20} [{marca}]')
            if not conocida:
                huerfanas.add(tabla)

        if not huerfanas:
            self.stdout.write(self.style.SUCCESS('\nNinguna tabla huérfana encontrada.'))
            return

        self.stdout.write(self.style.WARNING(f'\n{len(huerfanas)} tabla(s) huérfana(s) — contenido:'))
        with connection.cursor() as cursor:
            for tabla in sorted(huerfanas):
                cursor.execute(f'SELECT COUNT(*) FROM "{tabla}"')  # noqa: S608 — nombre de tabla viene de information_schema, no de entrada externa
                total = cursor.fetchone()[0]
                self.stdout.write(f'  {tabla}: {total} registro(s)')
                if total:
                    cursor.execute(f'SELECT * FROM "{tabla}" LIMIT 5')  # noqa: S608
                    columnas = [d[0] for d in cursor.description]
                    self.stdout.write(f'    columnas: {columnas}')
                    for fila in cursor.fetchall():
                        self.stdout.write(f'    {fila}')
