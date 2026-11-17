"""
Regenera las capturas del manual (Issue #507) con Playwright sobre una base
temporal con datos de demostración. Se corre a mano en la sesión que cambia una
pantalla; no en CI ni en producción.

    python manage.py generar_capturas_manual [--solo nombre ...] [--destino carpeta]
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test.runner import DiscoverRunner


class Command(BaseCommand):
    help = 'Regenera static/manual/img/*.webp con datos de demostración (base temporal, nunca la real).'

    def add_arguments(self, parser):
        parser.add_argument('--solo', nargs='+', metavar='NOMBRE', help='Solo estas capturas.')
        parser.add_argument('--destino', default=str(Path(settings.BASE_DIR) / 'static' / 'manual' / 'img'))

    def handle(self, *args, **opciones):
        # El corredor crea una base de pruebas: contra PostgreSQL de producción sería
        # crear una base `test_…` en ese servidor. Solo se corre con SQLite local.
        if connection.vendor != 'sqlite':
            raise CommandError('Solo corre con SQLite local (sin DATABASE_URL): crea y borra una base temporal.')
        from manual import capturas_vivas
        capturas_vivas.OPCIONES.update(destino=opciones['destino'], solo=opciones['solo'])
        corredor = DiscoverRunner(verbosity=1, interactive=False)
        fallos = corredor.run_tests(['manual.capturas_vivas.CapturasManual'])
        if fallos:
            raise CommandError('No se pudieron generar todas las capturas.')
        self.stdout.write(self.style.SUCCESS(f'Capturas en {opciones["destino"]}'))
