"""
Nómina de la semana a Dirección con [Aprobar] (Issue #431). Aprobar solo deja constancia.

Lo agenda el programador (`core_erp/programador.py`); aquí también se corre a mano.

Uso:
    python manage.py enviar_nomina_semanal
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Manda a Dirección el total de nómina de la semana para su aprobación."

    def handle(self, *args, **options):
        from comunicacion import services_direccion

        enviados = services_direccion.enviar_nomina_semanal()
        self.stdout.write(self.style.SUCCESS(f"Nómina semanal enviada: {enviados}"))
