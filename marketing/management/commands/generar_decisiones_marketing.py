"""
Reglas diarias de decisiones de marketing (Issue #468, fase 4). No ejecuta nada: solo propone.

Uso:
    python manage.py generar_decisiones_marketing
"""
from django.core.management.base import BaseCommand

from marketing.services import decisiones


class Command(BaseCommand):
    help = 'Corre las reglas de marketing y deja las recomendaciones como hallazgos y decisiones pendientes.'

    def handle(self, *args, **options):
        r = decisiones.generar()
        self.stdout.write(self.style.SUCCESS(
            f'{len(r.creadas)} decisión(es) nueva(s), {r.expiradas} expirada(s), {r.reabiertas} reabierta(s).'))
        for motivo in r.insuficientes:
            self.stdout.write(f'Datos insuficientes · {motivo}')
