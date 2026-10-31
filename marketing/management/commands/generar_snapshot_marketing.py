"""
Instantánea diaria de los indicadores de marketing (Issue #468, fase 3).

Uso:
    python manage.py generar_snapshot_marketing                     # hoy
    python manage.py generar_snapshot_marketing --fecha 2026-10-31
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from marketing.services import snapshots


class Command(BaseCommand):
    help = 'Guarda la instantánea de CPL, CAC, ROAS y tasa de cierre del día (total, servicio y campaña).'

    def add_arguments(self, parser):
        parser.add_argument('--fecha', help='AAAA-MM-DD (por defecto, hoy).')

    def handle(self, *args, **options):
        fecha = None
        if options['fecha']:
            try:
                fecha = date.fromisoformat(options['fecha'])
            except ValueError as exc:
                raise CommandError('--fecha debe ser AAAA-MM-DD.') from exc
        r = snapshots.generar(fecha)
        self.stdout.write(self.style.SUCCESS(f"Instantánea {r['fecha']:%d/%m/%Y}: {r['filas']} filas."))
