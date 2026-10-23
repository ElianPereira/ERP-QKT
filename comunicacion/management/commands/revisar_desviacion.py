"""
Alerta de desviación (Issue #431): desde el día 15, venta del mes por debajo del 70 % de la meta.

Lo agenda el programador (`core_erp/programador.py`); aquí también se corre a mano.

Uso:
    python manage.py revisar_desviacion
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Abre un pendiente URGENTE para Dirección si la venta del mes va por debajo del 70 % de la meta."

    def handle(self, *args, **options):
        from comunicacion import services_direccion

        pendiente = services_direccion.revisar_desviacion()
        self.stdout.write(self.style.SUCCESS(
            f"Desviación: pendiente {pendiente.pk}" if pendiente else "Sin desviación que avisar"))
