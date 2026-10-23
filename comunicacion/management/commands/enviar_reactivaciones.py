"""
Cron diario: reactivación anual por aniversario del evento (Fase 3).

Uso:
    python manage.py enviar_reactivaciones
    python manage.py enviar_reactivaciones --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from comunicacion import services_encuesta as svc


class Command(BaseCommand):
    help = "Manda el mensaje de aniversario a quien aceptó promociones."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Muestra a quién se enviaría sin llamar a Meta ni registrar nada.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        hoy = timezone.localdate()
        enviadas = omitidas = 0
        vistos = set()
        for cot in svc.cotizaciones_para_reactivacion(hoy):
            if cot.cliente_id in vistos:  # un mensaje por cliente aunque tenga varios eventos ese año
                continue
            motivo = svc.motivo_para_no_reactivar(cot, hoy)
            if motivo:
                omitidas += 1
                self.stdout.write(f'COT-{cot.id:03d}: se omite ({motivo})')
                continue
            vistos.add(cot.cliente_id)
            if dry_run:
                self.stdout.write(f'[DRY RUN] COT-{cot.id:03d} → {cot.cliente.nombre}')
                enviadas += 1
            elif svc.enviar_reactivacion(cot, hoy) is not None:
                enviadas += 1
        etiqueta = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(f'{etiqueta}Reactivaciones: {enviadas} enviadas, {omitidas} omitidas.'))
