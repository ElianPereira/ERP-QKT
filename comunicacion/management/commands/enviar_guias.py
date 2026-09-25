"""
Cron diario: envía la guía informativa (PDF) antes de un Evento/Pasadía/
Hospedaje confirmado, por email y WhatsApp — Issue #234.

Uso:
    python manage.py enviar_guias
    python manage.py enviar_guias --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from comunicacion.services_notificaciones import (
    cotizaciones_en_ventana_guia,
    guia_ya_enviada,
    notificar_guia_evento,
)


class Command(BaseCommand):
    help = "Envía la guía informativa antes de Evento/Pasadía/Hospedaje confirmados (email + WhatsApp)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Muestra a quién se enviaría sin llamar a Brevo/Meta ni registrar nada.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        # localdate() y no now().date(): mismo motivo que enviar_recordatorios
        # (now().date() da la fecha UTC y corre el envío un día entre las
        # 18:00 y la medianoche de Mérida).
        hoy = timezone.localdate()

        # Toda la ventana (hoy … hoy+3), no solo el día −3: una cotización
        # confirmada tarde o un día en que el cron no corrió se recupera en la
        # siguiente corrida. La que ya la recibió se salta.
        cotizaciones = cotizaciones_en_ventana_guia(hoy).select_related('cliente')

        enviadas = 0
        for cot in cotizaciones:
            cliente = cot.cliente
            if not cliente or (not cliente.email and not cliente.telefono):
                continue
            if guia_ya_enviada(cot):
                continue

            if dry_run:
                self.stdout.write(
                    f"[DRY RUN] COT-{cot.id:03d} ({cot.get_tipo_servicio_display()}) "
                    f"evento {cot.fecha_evento} → {cliente.nombre}"
                )
                enviadas += 1
                continue

            notificar_guia_evento(cot)
            enviadas += 1

        etiqueta = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(f"{etiqueta}Guías procesadas: {enviadas}"))
