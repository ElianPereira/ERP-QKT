"""
Cron diario: abre la renovación de los trámites a 45 días de vencer (Fase 3).

Uso:
    python manage.py preparar_renovaciones
    python manage.py preparar_renovaciones --aplicar
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from cumplimiento import services_renovacion as svc


class Command(BaseCommand):
    help = "Abre la renovación prellenada de los trámites por vencer (simula por defecto)."

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Crea las renovaciones y avisa a Dirección.')

    def handle(self, *args, **options):
        hoy = timezone.localdate()
        abiertas = 0
        for tramite, actual, dias in svc.tramites_por_renovar(hoy):
            if not options['aplicar']:
                self.stdout.write(f'[SIMULACIÓN] {tramite.nombre}: vence {actual.fecha_vencimiento:%d/%m/%Y} ({dias} días)')
                continue
            if svc.abrir_renovacion(tramite, actual, dias) is not None:
                abiertas += 1
        etiqueta = '' if options['aplicar'] else '[SIMULACIÓN] '
        self.stdout.write(self.style.SUCCESS(f'{etiqueta}Renovaciones abiertas: {abiertas}.'))
