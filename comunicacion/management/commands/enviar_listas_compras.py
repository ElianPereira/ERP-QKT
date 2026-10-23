"""
Cron diario: manda la lista de compras de los eventos de los próximos 3 días (Fase 3).

Uso:
    python manage.py enviar_listas_compras
    python manage.py enviar_listas_compras --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from comunicacion import services_compras as svc


class Command(BaseCommand):
    help = "Manda la lista de compras al rol Compras 3 días antes de cada evento."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Solo lista las cotizaciones; no manda nada.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        enviadas = 0
        for cot in svc.cotizaciones_para_compras(timezone.localdate()):
            if dry_run:
                self.stdout.write(f'[DRY RUN] COT-{cot.id:03d} {cot.fecha_evento:%d/%m/%Y} → {svc.rol_destino()}')
                enviadas += 1
            elif svc.enviar_lista(cot) is not None:
                enviadas += 1
        etiqueta = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(f'{etiqueta}Listas de compras: {enviadas}.'))
