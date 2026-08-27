"""
Cron único del módulo de operaciones (Issue #257) — pensado para correr cada
10 minutos, a diferencia de los demás crons del ERP que corren una vez al
día. En cada corrida:

1. Genera las tareas de mantenimiento recurrente que tocan hoy (idempotente).
2. Manda lo que ya esté en su horario y siga pendiente: checklist operativo,
   resumen al propietario.
3. Reintenta cualquier envío que haya quedado FALLIDO — sin cadencia de
   espera, igual que `enviar_recordatorios_contador` con las solicitudes
   PENDIENTE: mientras algo siga sin mandarse, se reintenta en cada corrida.

Requiere un Cron Job aparte en Railway con periodicidad de 10 minutos (los
demás crons del ERP corren una vez al día); ver Issue #257.

Uso:
    python manage.py procesar_tareas_operativas
    python manage.py procesar_tareas_operativas --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from operaciones.services import generar_tareas_mantenimiento, procesar_pendientes


class Command(BaseCommand):
    help = "Genera mantenimiento recurrente y envía/reintenta los mensajes de operaciones vencidos."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Solo genera las tareas de mantenimiento; no envía ni reintenta nada.',
        )

    def handle(self, *args, **options):
        hoy = timezone.localdate()
        creadas = generar_tareas_mantenimiento(hoy)

        if options['dry_run']:
            self.stdout.write(f"[DRY RUN] Tareas de mantenimiento generadas para hoy: {creadas}")
            return

        contadores = procesar_pendientes()
        self.stdout.write(self.style.SUCCESS(
            f"Mantenimiento generado: {creadas} — "
            f"Avisos de horario enviados: {contadores['aviso_horario']} — "
            f"Checklists operativos enviados: {contadores['operativo']} — "
            f"Resúmenes al propietario enviados: {contadores['resumen']}"
        ))
