"""
Management command DEPRECADO: enviar_recordatorios_pagos

Los recordatorios de pago viven ahora en un único comando:

    python manage.py enviar_recordatorios

Este archivo se conserva solo como shim porque el Cron Job de Railway lo invoca
por este nombre y su configuración vive fuera del repositorio. Borrarlo dejaría
el Cron fallando en silencio.

Antes tenía su propia implementación de WhatsApp, su propio calendario (el día
del vencimiento) y su propia tabla de auditoría (`RecordatorioPago`). Mantener
las dos versiones significaba mandarle al cliente dos recordatorios distintos
con reglas distintas, así que ahora delega. `RecordatorioPago` deja de
escribirse y queda como histórico; la auditoría nueva está en
`ComunicacionCliente`.
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "DEPRECADO — usa `enviar_recordatorios`. Delega en ese comando."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simula el envío sin llamar a las APIs ni guardar registros.',
        )
        parser.add_argument(
            '--dias-anticipacion',
            type=int,
            default=None,
            help='Ignorado: el calendario ahora lo fija `enviar_recordatorios` (DIAS_AVISO).',
        )

    def handle(self, *args, **options):
        self.stderr.write(self.style.WARNING(
            "`enviar_recordatorios_pagos` está deprecado y delega en "
            "`enviar_recordatorios`. Actualiza el Cron de Railway para llamar "
            "directamente al comando nuevo."
        ))
        if options.get('dias_anticipacion') is not None:
            self.stderr.write(self.style.WARNING(
                "--dias-anticipacion se ignora: el calendario vive en "
                "comunicacion.management.commands.enviar_recordatorios.DIAS_AVISO"
            ))
        call_command('enviar_recordatorios', dry_run=options['dry_run'])
