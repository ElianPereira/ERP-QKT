"""
Cron diario: pide al encargado la inspección de los depósitos en garantía de los
servicios terminados ayer (Fase 3). No mueve dinero; solo abre la inspección.

Uso:
    python manage.py solicitar_inspecciones_deposito
    python manage.py solicitar_inspecciones_deposito --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from comunicacion import services_inspeccion as svc


class Command(BaseCommand):
    help = "Pide la inspección de los depósitos en garantía de los servicios que ya terminaron."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Solo lista los depósitos; no manda nada.')

    def handle(self, *args, **options):
        pedidas = 0
        for deposito in svc.depositos_para_inspeccion(timezone.localdate()):
            if options['dry_run']:
                self.stdout.write(f'[DRY RUN] COT-{deposito.cotizacion_id:03d}: ${deposito.en_custodia:,.2f} en custodia')
                pedidas += 1
            elif svc.solicitar_inspeccion(deposito) is not None:
                pedidas += 1
        etiqueta = '[DRY RUN] ' if options['dry_run'] else ''
        self.stdout.write(self.style.SUCCESS(f'{etiqueta}Inspecciones pedidas: {pedidas}.'))
