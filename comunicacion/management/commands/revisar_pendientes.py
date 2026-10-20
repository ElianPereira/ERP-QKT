"""
Revisa la bandeja de pendientes (Issue #422): escala a Dirección lo del
encargado que venció y manda la copia «cada hora» a Dirección si la tiene.

La agenda la Fase 1c (cada 15 minutos); aquí solo se corre a mano.

Uso:
    python manage.py revisar_pendientes
"""
from django.core.management.base import BaseCommand

from comunicacion import services_pendientes


class Command(BaseCommand):
    help = "Escala los pendientes vencidos del encargado y manda la copia por hora a Dirección."

    def handle(self, *args, **options):
        escalados = services_pendientes.escalar_vencidos()
        copiados = services_pendientes.copiar_cada_hora()
        self.stdout.write(self.style.SUCCESS(
            f"Escalados a Dirección: {escalados} — Incluidos en la copia por hora: {copiados}"))
