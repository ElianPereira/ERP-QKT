"""
Barrido diario de prospectos de marketing (Issue #468, fase 1).

Uso:
    python manage.py sincronizar_prospectos
    python manage.py sincronizar_prospectos --desde 2026-07-01   # reconstruye histórico
"""
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from marketing.services import barrido


class Command(BaseCommand):
    help = 'Sincroniza el embudo de prospectos, clasifica motivos de pérdida y anonimiza los vencidos.'

    def add_arguments(self, parser):
        parser.add_argument('--desde', help='AAAA-MM-DD: reconstruye el histórico desde esa fecha.')

    def handle(self, *args, **options):
        desde = None
        if options['desde']:
            try:
                desde = timezone.make_aware(datetime.strptime(options['desde'], '%Y-%m-%d'))
            except ValueError as exc:
                raise CommandError('--desde debe ser AAAA-MM-DD.') from exc
        r = barrido.barrer(desde=desde)
        self.stdout.write(self.style.SUCCESS(
            f"Prospectos: {r['creados']} creados, {r['abiertos']} abiertos, "
            f"{r['motivos']} motivos clasificados, {r['anonimizados']} anonimizados."))
