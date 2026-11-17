"""
Cron diario: solicitud de autorización del Aviso de Privacidad a quien ya reservó o
pagó y no la ha dado (Issue #506). Una sola solicitud por cliente y versión del Aviso.

Uso:
    python manage.py pedir_consentimientos_pendientes
    python manage.py pedir_consentimientos_pendientes --dry-run
"""
from django.core.management.base import BaseCommand

from comercial import services_consentimiento as svc


class Command(BaseCommand):
    help = "Pide por WhatsApp la autorización del Aviso de Privacidad a clientes con reservación o pago."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Solo cuenta a quién se le pediría; no manda ni registra nada.')

    def handle(self, *args, **options):
        r = svc.pedir_consentimientos_pendientes(dry_run=options['dry_run'])
        etiqueta = '[DRY RUN] ' if options['dry_run'] else ''
        self.stdout.write(self.style.SUCCESS(
            f"{etiqueta}Consentimientos: {r['candidatos']} sin aceptar, {r['enviadas']} enviadas, "
            f"{r['ya_enviadas']} ya enviadas, {r['sin_plantilla']} sin plantilla, {r['fallidas']} fallidas, "
            f"{r['sin_contacto']} sin teléfono ni correo."))
