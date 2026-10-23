"""
Cron por hora: devuelve los depósitos en garantía cuya inspección venció sin
reporte de daños (Fase 3). MUEVE DINERO: sin --aplicar solo simula.

Uso:
    python manage.py devolver_depositos_sin_reporte
    python manage.py devolver_depositos_sin_reporte --aplicar
"""
from django.core.management.base import BaseCommand

from comunicacion import services_inspeccion as svc


class Command(BaseCommand):
    help = "Devuelve el depósito de las inspecciones sin reporte de daños tras el plazo (simula por defecto)."

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Devuelve los depósitos de verdad.')

    def handle(self, *args, **options):
        aplicar = options['aplicar']
        hechas = svc.devolver_sin_reporte(aplicar=aplicar)
        for inspeccion in hechas:
            self.stdout.write(f'{"" if aplicar else "[SIMULACIÓN] "}COT-{inspeccion.deposito.cotizacion_id:03d}: '
                              f'${inspeccion.deposito.recibido:,.2f} recibidos')
        avisadas = svc.revisar_atoradas() if aplicar else 0
        etiqueta = '' if aplicar else '[SIMULACIÓN] '
        self.stdout.write(self.style.SUCCESS(
            f'{etiqueta}Depósitos devueltos: {len(hechas)}; atoradas avisadas: {avisadas}.'))
