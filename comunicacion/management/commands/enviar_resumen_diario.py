"""
Resumen diario de la bandeja de pendientes (Issue #422): un mensaje por
responsable con sus pendientes abiertos; a Dirección además los resueltos de
ayer, y al encargado el resumen de operaciones. Corre una vez por día y
teléfono aunque se ejecute dos veces.

La agenda la Fase 1c (8:00); aquí solo se corre a mano.

Uso:
    python manage.py enviar_resumen_diario
"""
from django.core.management.base import BaseCommand

from comunicacion import services_pendientes


class Command(BaseCommand):
    help = "Manda a cada responsable el resumen diario de sus pendientes."

    def handle(self, *args, **options):
        enviados = services_pendientes.enviar_resumen_diario()
        self.stdout.write(self.style.SUCCESS(f"Resúmenes enviados: {enviados}"))
