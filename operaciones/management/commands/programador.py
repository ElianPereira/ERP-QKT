"""
Programador único (Issue #423): reemplaza a los crons sueltos de Railway. Un
solo servicio lo corre cada 5 minutos; la tabla de tareas y sus horarios está
en `core_erp/programador.py` y cada corrida queda en «Ejecuciones».

Uso:
    python manage.py programador                  # una vuelta
    python manage.py programador --simular        # lista qué correría, sin ejecutar nada
    python manage.py programador --solo enviar_guias
"""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core_erp.programador import ZONA
from operaciones import services_programador as svc

ETIQUETAS = {
    'corrio': 'OK', 'error': 'ERROR', 'tomada': 'ya tomada por otro proceso',
    'fuera_de_tolerancia': 'fuera de tolerancia (no se recupera)', 'simulada': 'correría',
    'ya_corrida': 'ya corrida',
}


class Command(BaseCommand):
    help = "Corre las tareas programadas que ya vencieron (una vez por ranura)."

    def add_arguments(self, parser):
        parser.add_argument('--solo', help='Corre únicamente esta tarea.')
        parser.add_argument('--simular', action='store_true',
                            help='Lista qué correría y no ejecuta ni registra nada.')

    def handle(self, *args, **options):
        try:
            resultados = svc.correr_vuelta(timezone.now(), solo=options['solo'], simular=options['simular'])
        except ValueError as exc:
            raise CommandError(str(exc))
        for r in resultados:
            ranura = r.ranura.astimezone(ZONA)
            self.stdout.write(f'{r.tarea} · ranura {ranura:%d/%m %H:%M} · {ETIQUETAS.get(r.accion, r.accion)}')
        if not resultados:
            self.stdout.write('Sin tareas.')
