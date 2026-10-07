"""
Cron diario: recuerda por WhatsApp las cotizaciones sin pago (Issue #366).

Una sola vez por cotización, a los `WA_SEGUIMIENTO_DIAS` de creada, solo a
quien aceptó la finalidad MARKETING y si la fecha sigue libre.

Uso:
    python manage.py enviar_seguimientos
    python manage.py enviar_seguimientos --dry-run
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from comunicacion.models import ComunicacionCliente
from comunicacion.services_notificaciones import (
    cotizaciones_para_seguimiento,
    motivo_para_no_seguir,
    notificar_seguimiento,
)


class Command(BaseCommand):
    help = "Manda el seguimiento por WhatsApp de las cotizaciones sin pago."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Muestra a quién se enviaría sin llamar a Meta ni registrar nada.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if not settings.WA_TEMPLATE_SEGUIMIENTO and not dry_run:
            self.stdout.write('WA_TEMPLATE_SEGUIMIENTO no está configurada: no se manda nada.')
            return
        enviadas = omitidas = 0
        for cot in cotizaciones_para_seguimiento(timezone.localdate()):
            if ComunicacionCliente.objects.filter(
                    clave_idempotencia=f'seguimiento:{cot.pk}:whatsapp').exists():
                continue
            motivo = motivo_para_no_seguir(cot)
            if motivo:
                omitidas += 1
                self.stdout.write(f'COT-{cot.id:03d}: se omite ({motivo})')
                continue
            if dry_run:
                self.stdout.write(f'[DRY RUN] COT-{cot.id:03d} → {cot.cliente.nombre}')
            else:
                notificar_seguimiento(cot)
            enviadas += 1
        etiqueta = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(f'{etiqueta}Seguimientos: {enviadas} enviados, {omitidas} omitidos.'))
