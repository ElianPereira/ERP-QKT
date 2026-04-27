"""
Cierra automáticamente cotizaciones cuyo evento ya ocurrió.

Lógica:
  1. CONFIRMADA / COTIZADA con fecha_evento < hoy → EJECUTADA (evento realizado)
  2. EJECUTADA con fecha_evento < hoy y saldo ≤ $0.50  → CERRADA (pagada y lista)

Uso:  python manage.py cerrar_cotizaciones
Cron: configurar en Railway como cron job diario.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from comercial.models import Cotizacion


class Command(BaseCommand):
    help = 'Avanza automáticamente el estado de cotizaciones con eventos pasados'

    def handle(self, *args, **options):
        hoy = timezone.now().date()
        ejecutadas = 0
        cerradas = 0

        # Paso 1: eventos realizados que siguen como CONFIRMADA o COTIZADA
        pendientes_ejecutar = Cotizacion.objects.filter(
            fecha_evento__lt=hoy,
            estado__in=['CONFIRMADA', 'COTIZADA'],
        )
        for cot in pendientes_ejecutar:
            Cotizacion.objects.filter(pk=cot.pk).update(estado='EJECUTADA')
            ejecutadas += 1
            self.stdout.write(f'  EJECUTADA  COT-{cot.pk:03d} ({cot.nombre_evento[:50]})')

        # Paso 2: eventos ejecutados con saldo cubierto → CERRADA
        pendientes_cerrar = Cotizacion.objects.filter(
            fecha_evento__lt=hoy,
            estado='EJECUTADA',
        )
        for cot in pendientes_cerrar:
            if cot.saldo_pendiente() <= Decimal('0.50'):
                Cotizacion.objects.filter(pk=cot.pk).update(estado='CERRADA')
                cerradas += 1
                self.stdout.write(f'  CERRADA    COT-{cot.pk:03d} ({cot.nombre_evento[:50]})')

        self.stdout.write(self.style.SUCCESS(
            f'\nResultado: {ejecutadas} → EJECUTADA, {cerradas} → CERRADA'
        ))
