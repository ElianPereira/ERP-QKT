"""
Avisa al encargado de los prospectos sin respuesta (Issue #468, fase 4). Corre cada 5 minutos.

Uso:
    python manage.py revisar_respuesta_prospectos
"""
from django.core.management.base import BaseCommand

from marketing.services import decisiones


class Command(BaseCommand):
    help = 'Pendiente URGENTE al encargado por cada prospecto que lleva más de N minutos sin respuesta (8:00 a 20:00).'

    def handle(self, *args, **options):
        creados = decisiones.revisar_sin_respuesta()
        self.stdout.write(self.style.SUCCESS(f'{len(creados)} aviso(s) de prospecto sin respuesta.'))
