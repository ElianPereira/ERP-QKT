"""
Cron diario: encuesta de satisfacción un día después del servicio (Fase 3).

Uso:
    python manage.py enviar_encuestas
    python manage.py enviar_encuestas --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from comunicacion import services_encuesta as svc


class Command(BaseCommand):
    help = "Manda la encuesta de satisfacción de los servicios terminados ayer."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Muestra a quién se enviaría sin llamar a Meta ni registrar nada.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        enviadas = omitidas = 0
        for cot in svc.cotizaciones_para_encuesta(timezone.localdate()):
            motivo = svc.motivo_para_no_encuestar(cot)
            if motivo:
                omitidas += 1
                self.stdout.write(f'COT-{cot.id:03d}: se omite ({motivo})')
                continue
            if dry_run:
                self.stdout.write(f'[DRY RUN] COT-{cot.id:03d} → {cot.cliente.nombre}')
                enviadas += 1
            elif svc.enviar_encuesta(cot) is not None:
                enviadas += 1
        etiqueta = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(f'{etiqueta}Encuestas: {enviadas} enviadas, {omitidas} omitidas.'))
