"""
Cron diario (18:00): recuerda al cliente, el día anterior, su visita confirmada
a la Quinta (Fase 2a), una sola vez, y le manda al equipo el resumen de las
visitas de mañana.

Uso:
    python manage.py enviar_recordatorios_visitas
"""
from django.core.management.base import BaseCommand

from comunicacion import services_visitas


class Command(BaseCommand):
    help = "Recuerda por WhatsApp las visitas confirmadas de mañana."

    def handle(self, *args, **options):
        enviados = services_visitas.enviar_recordatorios()
        self.stdout.write(self.style.SUCCESS(f'Recordatorios de visita: {enviados} enviados.'))
