"""
Cron: (1) reintenta el envío inicial de las solicitudes cuyo auto-envío
falló en los dos canales (PENDIENTE) y (2) recuerda al contador las que ya
se enviaron pero siguen sin factura (ENVIADA, nunca llegó a FACTURADA).

Uso:
    python manage.py enviar_recordatorios_contador
    python manage.py enviar_recordatorios_contador --dry-run

Una solicitud en PENDIENTE es porque el envío automático
(facturacion.signals.crear_solicitud_factura_desde_pago, vía
enviar_solicitud_al_contador) falló en los dos canales — este comando
reintenta exactamente esa misma llamada, una vez por corrida, hasta que
alguno de los dos tenga éxito y quede ENVIADA. Sin cadencia de días como
los recordatorios de abajo: mientras siga PENDIENTE es porque nunca se
mandó de verdad, así que se reintenta en cada corrida del cron sin esperar
ningún número de días.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from facturacion.models import SolicitudFactura
from facturacion.services import (
    enviar_solicitud_al_contador,
    enviar_solicitud_por_email,
    enviar_solicitud_por_whatsapp,
    get_usuario_sistema,
)

# Días desde fecha_envio en los que se recuerda, mientras siga sin facturarse.
DIAS_RECORDATORIO = (3, 7, 14)


class Command(BaseCommand):
    help = (
        "Reintenta el envío al contador de las solicitudes PENDIENTE (falló "
        "en los dos canales) y recuerda (email + WhatsApp) las ENVIADA aún "
        "sin facturar."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Muestra qué se reintentaría/recordaría sin mandar nada ni registrar nada.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        hoy = timezone.localdate()

        # ─── (1) Reintento de solicitudes cuyo envío inicial falló ──────────
        fallidas = SolicitudFactura.objects.filter(estado='PENDIENTE')
        reintentadas = 0
        for solicitud in fallidas:
            folio = f"SOL-{solicitud.id:04d}"
            if dry_run:
                self.stdout.write(f"[DRY RUN] {folio} — PENDIENTE, se reintentaría el envío")
                reintentadas += 1
                continue

            email_ok, wa_ok = enviar_solicitud_al_contador(solicitud, usuario=get_usuario_sistema())
            if email_ok or wa_ok:
                reintentadas += 1
            else:
                self.stderr.write(f"{folio}: reintento automático volvió a fallar en los dos canales")

        # ─── (2) Recordatorios de las ya enviadas y aún sin facturar ────────
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
        self.stdout.write(self.style.SUCCESS(
            f"{etiqueta}Reintentos de envío inicial: {reintentadas} — "
            f"Recordatorios al contador procesados: {recordadas}"
        ))
