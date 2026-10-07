"""
Cron semanal: agrupa lo que el agente de WhatsApp no supo contestar, deja
preguntas frecuentes en borrador (inactivas) con una respuesta sugerida y
avisa al propietario por WhatsApp (Issue #366).

Uso:
    python manage.py resumir_preguntas_agente            # solo cuenta
    python manage.py resumir_preguntas_agente --aplicar  # crea borradores y avisa
"""
from django.core.management.base import BaseCommand

from comunicacion.services_preguntas import resumir_preguntas


class Command(BaseCommand):
    help = 'Resume las dudas sin respuesta del agente y deja preguntas frecuentes en borrador.'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true',
                            help='Crea los borradores y avisa (llama a la API de Anthropic).')

    def handle(self, *args, **opciones):
        r = resumir_preguntas(aplicar=opciones['aplicar'])
        if not opciones['aplicar']:
            self.stdout.write(f"{r['dudas']} dudas sin resumir. Agrega --aplicar para crear los borradores.")
            return
        self.stdout.write(self.style.SUCCESS(
            f"{r['dudas']} dudas → {r['borradores']} borradores ({r['por_completar']} por completar)."))
