"""
Resumen de cada lunes a Dirección: venta contra meta, ocupación, caja, pendientes y «Detecté» (Issue #431).

Lo agenda el programador (`core_erp/programador.py`); aquí también se corre a mano.

Uso:
    python manage.py enviar_resumen_semanal
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Manda a Dirección el resumen semanal con hasta 3 hallazgos."

    def handle(self, *args, **options):
        from comunicacion import services_direccion

        enviados = services_direccion.enviar_resumen_semanal()
        self.stdout.write(self.style.SUCCESS(f"Resumen semanal enviado: {enviados}"))
