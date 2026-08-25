"""
Cron: recuerda al contador las solicitudes de factura que ya se enviaron
pero siguen sin factura (ENVIADA, nunca llegó a FACTURADA).

Uso:
    python manage.py enviar_recordatorios_contador
    python manage.py enviar_recordatorios_contador --dry-run

No toca solicitudes en PENDIENTE: esas se mandan solas al crearse
(facturacion.signals.crear_solicitud_factura_desde_pago, vía
enviar_solicitud_al_contador) — si una sigue en PENDIENTE es porque ese
primer envío falló en los dos canales, y este comando no reintenta un
envío que nunca se confirmó como hecho.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from facturacion.models import SolicitudFactura
from facturacion.services import enviar_solicitud_por_email, enviar_solicitud_por_whatsapp

# Días desde fecha_envio en los que se recuerda, mientras siga sin facturarse.
DIAS_RECORDATORIO = (3, 7, 14)


class Command(BaseCommand):
    help = "Recuerda al contador (email + WhatsApp) las solicitudes de factura enviadas y aún no facturadas."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Muestra a quién se recordaría sin mandar nada ni registrar nada.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        hoy = timezone.localdate()

        pendientes = SolicitudFactura.objects.filter(estado='ENVIADA', fecha_envio__isnull=False)

        recordadas = 0
        for solicitud in pendientes:
            dias_transcurridos = (hoy - solicitud.fecha_envio.date()).days
            if dias_transcurridos not in DIAS_RECORDATORIO:
                continue

            # No repetir el mismo recordatorio si el comando ya corrió hoy.
            if solicitud.ultimo_recordatorio_enviado and solicitud.ultimo_recordatorio_enviado.date() == hoy:
                continue

            folio = f"SOL-{solicitud.id:04d}"

            if dry_run:
                self.stdout.write(
                    f"[DRY RUN] {folio} — {dias_transcurridos} día(s) sin facturarse, se recordaría"
                )
                recordadas += 1
                continue

            email_ok, email_error = enviar_solicitud_por_email(solicitud)
            wa_ok, wa_error = enviar_solicitud_por_whatsapp(solicitud)

            if email_ok or wa_ok:
                SolicitudFactura.objects.filter(pk=solicitud.pk).update(
                    ultimo_recordatorio_enviado=timezone.now()
                )
                recordadas += 1
            if not email_ok:
                self.stderr.write(f"{folio}: recordatorio por email falló: {email_error}")
            if not wa_ok:
                self.stderr.write(f"{folio}: recordatorio por WhatsApp falló: {wa_error}")

        etiqueta = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(f"{etiqueta}Recordatorios al contador procesados: {recordadas}"))
