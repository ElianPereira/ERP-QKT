"""
Pregunta al contador por los CFDI emitidos que no se pudieron ligar solos (Issue #431). Un mensaje al día.

Lo agenda el programador (`core_erp/programador.py`); aquí también se corre a mano.

Uso:
    python manage.py preguntar_cfdi_contador
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Manda al contador un mensaje agrupado con los CFDI emitidos ambiguos."

    def handle(self, *args, **options):
        from comunicacion import services_contador

        enviados = services_contador.preguntar_contador()
        self.stdout.write(self.style.SUCCESS(f"Consulta al contador enviada: {enviados}"))
