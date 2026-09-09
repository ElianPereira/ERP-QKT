"""
Avanza solo el estado de las cotizaciones según lo que ya ocurrió.

Lógica, en este orden (importa):
  0. BORRADOR / COTIZADA sin NINGÚN pago → EXPIRADA, cuando la fecha del
     evento ya pasó o llevan DIAS_EXPIRACION_SIN_PAGO días sin cobrar nada.
  1. CONFIRMADA / COTIZADA con fecha_evento < hoy → EJECUTADA (evento realizado)
  2. EJECUTADA con fecha_evento < hoy y saldo ≤ $0.50  → CERRADA (pagada y lista)

El paso 0 va ANTES del 1 a propósito: sin él, una cotización que nadie pagó y
cuya fecha ya pasó terminaba en EJECUTADA, es decir contada como venta real por
`views.ESTADOS_VENTA_REAL` — un evento que jamás ocurrió inflando el reporte.

Nunca borra nada (mismo criterio que el resto del ERP: soft-deactivation, sin
DELETE físico); EXPIRADA conserva la cotización, sus ítems y su historial, y se
puede revivir a BORRADOR si el cliente reaparece.

Uso:  python manage.py cerrar_cotizaciones
Cron: configurar en Railway como cron job diario.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from comercial.models import Cotizacion


class Command(BaseCommand):
    help = 'Avanza automáticamente el estado de cotizaciones vencidas o con eventos pasados'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Muestra qué haría sin escribir nada en la base de datos.',
        )

    def handle(self, *args, **options):
        simular = options['dry_run']
        if simular:
            self.stdout.write(self.style.WARNING('SIMULACIÓN — no se escribe nada\n'))

        hoy = timezone.localdate()
        expiradas = 0
        ejecutadas = 0
        cerradas = 0

        # Paso 0: cotizaciones que nunca prosperaron.
        # `motivo_expiracion()` decide (incluido el filtro de "sin ningún
        # pago"); aquí solo se acota el queryset a los dos estados que pueden
        # expirar, para no traer el histórico completo.
        candidatas = Cotizacion.objects.filter(
            estado__in=['BORRADOR', 'COTIZADA'],
        ).prefetch_related('pagos')
        for cot in candidatas:
            motivo = cot.motivo_expiracion()
            if not motivo:
                continue
            if not simular:
                Cotizacion.objects.filter(pk=cot.pk).update(estado='EXPIRADA')
            expiradas += 1
            self.stdout.write(f'  EXPIRADA   COT-{cot.pk:03d} ({motivo})')

        # Paso 1: eventos realizados que siguen como CONFIRMADA o COTIZADA.
        # Las recién expiradas ya salieron de estos estados, así que no entran.
        pendientes_ejecutar = Cotizacion.objects.filter(
            fecha_evento__lt=hoy,
            estado__in=['CONFIRMADA', 'COTIZADA'],
        )
        for cot in pendientes_ejecutar:
            if not simular:
                Cotizacion.objects.filter(pk=cot.pk).update(estado='EJECUTADA')
            ejecutadas += 1
            self.stdout.write(f'  EJECUTADA  COT-{cot.pk:03d} ({cot.nombre_evento[:50]})')

        # Paso 2: eventos ejecutados con saldo cubierto → CERRADA.
        # En simulación el paso 1 no escribió, así que estas son solo las que ya
        # estaban en EJECUTADA de antes; es la diferencia esperada entre simular
        # y aplicar, no un error de conteo.
        pendientes_cerrar = Cotizacion.objects.filter(
            fecha_evento__lt=hoy,
            estado='EJECUTADA',
        )
        for cot in pendientes_cerrar:
            if cot.saldo_pendiente() <= Decimal('0.50'):
                if not simular:
                    Cotizacion.objects.filter(pk=cot.pk).update(estado='CERRADA')
                cerradas += 1
                self.stdout.write(f'  CERRADA    COT-{cot.pk:03d} ({cot.nombre_evento[:50]})')

        self.stdout.write(self.style.SUCCESS(
            f'\nResultado: {expiradas} → EXPIRADA, {ejecutadas} → EJECUTADA, '
            f'{cerradas} → CERRADA'
        ))
