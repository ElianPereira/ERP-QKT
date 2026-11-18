from django.core.management.base import BaseCommand

from ventas.services_comisiones import barrido


class Command(BaseCommand):
    help = ('Recalcula las comisiones de contratación y de visitas del personal de ventas. Idempotente: '
            'solo emite la diferencia. Respaldo diario de las señales.')

    def handle(self, *args, **opciones):
        r = barrido()
        self.stdout.write(f"Cotizaciones: {r['cotizaciones']} · movimientos: {r['movimientos']} · "
                          f"visitas: {r['visitas']} · errores: {r['errores']}")
        if r['errores']:
            raise SystemExit(1)
