"""
Cada 10 min (programador): el día de la visita confirmada, recuerda al cliente
2 h antes y avisa al equipo 45 min antes, salvo que el cliente ya haya dicho que
llegó. Cada aviso sale una sola vez.

Uso:
    python manage.py enviar_avisos_previos_visitas
"""
from django.core.management.base import BaseCommand

from comunicacion import services_visitas


class Command(BaseCommand):
    help = "Avisos del mismo día de las visitas confirmadas (cliente 2 h antes, equipo 45 min antes)."

    def handle(self, *args, **options):
        enviados = services_visitas.enviar_avisos_previos()
        self.stdout.write(self.style.SUCCESS(f'Avisos previos de visita al equipo: {enviados}.'))
