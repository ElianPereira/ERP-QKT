"""
Cron diario: envía la guía informativa (PDF) antes de un Evento/Pasadía/
Hospedaje confirmado, por email y WhatsApp — Issue #234.

Uso:
    python manage.py enviar_guias
    python manage.py enviar_guias --dry-run
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from comercial.models import Cotizacion
from comunicacion.services_notificaciones import notificar_guia_evento

# Un solo aviso, 3 días antes de fecha_evento (check-in en el caso de
# Hospedaje). A diferencia de los recordatorios de pago, la guía no se repite
# en varios cortes: un solo elemento en esta lista es suficiente.
DIAS_AVISO = (3,)

# Arrendamiento de Mobiliario no tiene un sitio físico al que llegar el día
# del evento, así que no le aplica ninguna guía.
TIPOS_CON_GUIA = ('EVENTO', 'PASADIA', 'HOSPEDAJE')


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
        objetivos = [hoy + timedelta(days=d) for d in DIAS_AVISO]

        cotizaciones = Cotizacion.objects.filter(
            estado='CONFIRMADA',
            tipo_servicio__in=TIPOS_CON_GUIA,
            fecha_evento__in=objetivos,
        ).select_related('cliente')

        enviadas = 0
        for cot in cotizaciones:
            cliente = cot.cliente
            if not cliente or (not cliente.email and not cliente.telefono):
                continue

            if dry_run:
                self.stdout.write(
                    f"[DRY RUN] COT-{cot.id:03d} ({cot.get_tipo_servicio_display()}) "
                    f"evento {cot.fecha_evento} → {cliente.nombre}"
                )
                enviadas += 1
                continue

            # La idempotencia es responsabilidad del servicio (clave por
            # cotización + canal, sin fecha de ejecución), así que correr el
            # cron dos veces el mismo día no duplica.
            notificar_guia_evento(cot)
            enviadas += 1

        etiqueta = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(f"{etiqueta}Guías procesadas: {enviadas}"))
