"""
Cron diario: recuerda por WhatsApp las cotizaciones sin pago (Issue #366).

Hasta tres mensajes por cotización (Fase 2a): día 1, día 3
(`ERP_KOOXI_SEGUIMIENTO_DIAS`) y «tu fecha está por ocuparse». Cada uno sale una sola
vez, solo a quien aceptó la finalidad MARKETING, si la fecha sigue libre, y solo si su
plantilla está configurada; nunca dos en 20 h para la misma cotización.

Uso:
    python manage.py enviar_seguimientos
    python manage.py enviar_seguimientos --dry-run
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from comunicacion.services_notificaciones import (
    PLANTILLA_DEL_PASO,
    cotizaciones_para_seguimiento,
    motivo_para_no_seguir,
    notificar_seguimiento,
    paso_pendiente,
    ventanas_de_seguimiento,
)


class Command(BaseCommand):
    help = "Manda el seguimiento por WhatsApp de las cotizaciones sin pago."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Muestra a quién se enviaría sin llamar a Meta ni registrar nada.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if not any(getattr(settings, v, '') for v in PLANTILLA_DEL_PASO.values()) and not dry_run:
            self.stdout.write('Ninguna plantilla de seguimiento está configurada: no se manda nada.')
            return
        hoy = timezone.localdate()
        ventanas = ventanas_de_seguimiento(hoy)
        candidatas = {}
        for paso in ventanas:
            for cot in cotizaciones_para_seguimiento(hoy, paso):
                candidatas[cot.pk] = cot
        enviadas = omitidas = 0
        for cot in sorted(candidatas.values(), key=lambda c: c.pk):
            paso = paso_pendiente(cot, ventanas)
            if paso is None:
                continue
            motivo = motivo_para_no_seguir(cot)
            if motivo:
                omitidas += 1
                self.stdout.write(f'COT-{cot.id:03d}: se omite ({motivo})')
                continue
            if dry_run:
                self.stdout.write(f'[DRY RUN] COT-{cot.id:03d} ({paso}) → {cot.cliente.nombre}')
            else:
                notificar_seguimiento(cot, paso)
            enviadas += 1
        etiqueta = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(f'{etiqueta}Seguimientos: {enviadas} enviados, {omitidas} omitidos.'))
