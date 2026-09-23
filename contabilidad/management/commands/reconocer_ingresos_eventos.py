"""
Pasa a ingreso el anticipo de los eventos que ya se ejecutaron antes de que
existiera el reconocimiento automático (`crear_poliza_reconocimiento_ingreso`).

Simula por defecto (mismo criterio que `corregir_polizas_airbnb_iva` y
`cerrar_historico_contable`); `--aplicar` escribe. Idempotente: una cotización
ya reconocida no genera nada. `--desde` acota por fecha del evento, para no
tocar periodos que el contador ya cerró fuera del ERP.

Uso:  python manage.py reconocer_ingresos_eventos [--desde 2026-07-01] [--aplicar]
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from comercial.models import Cotizacion
from contabilidad.signals import (
    ESTADOS_INGRESO_DEVENGADO,
    anticipo_por_reconocer,
    crear_poliza_reconocimiento_ingreso,
)


class Command(BaseCommand):
    help = 'Reconoce como ingreso el anticipo de eventos ya ejecutados (simula por defecto)'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Escribe las pólizas.')
        parser.add_argument('--desde', help='Solo eventos con fecha >= AAAA-MM-DD.')

    def handle(self, *args, **options):
        qs = Cotizacion.objects.filter(estado__in=ESTADOS_INGRESO_DEVENGADO).order_by('fecha_evento')
        if options['desde']:
            try:
                qs = qs.filter(fecha_evento__gte=date.fromisoformat(options['desde']))
            except ValueError as e:
                raise CommandError('--desde debe tener formato AAAA-MM-DD.') from e

        aplicar = options['aplicar']
        if not aplicar:
            self.stdout.write(self.style.WARNING('SIMULACIÓN — no se escribe nada\n'))

        total = 0
        with transaction.atomic():
            for cot in qs:
                pendiente = anticipo_por_reconocer(cot)
                if pendiente == 0:
                    continue
                total += 1
                self.stdout.write(f'  COT-{cot.pk:03d} {cot.fecha_evento}  ${pendiente:,.2f}')
                if aplicar:
                    crear_poliza_reconocimiento_ingreso(cot)
        self.stdout.write(self.style.SUCCESS(f'\n{total} cotizaciones con anticipo por reconocer.'))
