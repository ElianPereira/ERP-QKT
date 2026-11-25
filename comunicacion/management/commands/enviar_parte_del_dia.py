"""
Parte del día para el equipo de apoyo temporal: servicios de hoy y mañana y
visitas de hoy. Sin equipo de apoyo vigente no manda nada. Lo agenda el
programador a las 8:05.

Uso:
    python manage.py enviar_parte_del_dia
"""
from django.core.management.base import BaseCommand

from comunicacion import services_apoyo


class Command(BaseCommand):
    help = "Manda el parte del día al equipo de apoyo vigente."

    def handle(self, *args, **options):
        enviados = services_apoyo.enviar_parte_del_dia()
        self.stdout.write(self.style.SUCCESS(f"Partes enviados: {enviados}"))
