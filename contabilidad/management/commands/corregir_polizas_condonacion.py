"""
Cancela las pólizas que generaron las condonaciones antes de que el signal
dejara de contabilizarlas: cargaban el banco y abonaban anticipo e IVA por un
dinero que nunca entró.

Cancela, no borra (mismo criterio que `cerrar_historico_contable`): la póliza
conserva folio y movimientos y sale de saldos y reportes. Si el evento ya se
había reconocido como ingreso, reemite el reconocimiento para que el anticipo
quede en cero (ajuste inverso por lo condonado). Las solicitudes de factura
que nacieron de una condonación solo se listan: cancelarlas o pedir la nota
de crédito es decisión del contador.

Simula por defecto; `--aplicar` escribe. `--desde` acota por fecha del pago
para no tocar periodos que el contador ya cerró.

Uso:  python manage.py corregir_polizas_condonacion [--desde 2026-07-01] [--aplicar]
"""
from datetime import date

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from comercial.models import Pago
from contabilidad.models import Poliza
from contabilidad.signals import (
    ESTADOS_INGRESO_DEVENGADO,
    crear_poliza_reconocimiento_ingreso,
    get_usuario_sistema,
)

MOTIVO = 'Condonación: no entró dinero, no se contabiliza (corrección del signal).'


class Command(BaseCommand):
    help = 'Cancela las pólizas de pagos por condonación (simula por defecto)'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Escribe los cambios.')
        parser.add_argument('--desde', help='Solo condonaciones con fecha de pago >= AAAA-MM-DD.')

    def handle(self, *args, **options):
        pagos = Pago.objects.filter(metodo='CONDONACION').select_related('cotizacion').order_by('fecha_pago')
        if options['desde']:
            try:
                pagos = pagos.filter(fecha_pago__gte=date.fromisoformat(options['desde']))
            except ValueError as e:
                raise CommandError('--desde debe tener formato AAAA-MM-DD.') from e

        aplicar = options['aplicar']
        if not aplicar:
            self.stdout.write(self.style.WARNING('SIMULACIÓN — no se escribe nada\n'))

        polizas = Poliza.objects.filter(
            content_type=ContentType.objects.get_for_model(Pago),
            object_id__in=pagos.values_list('pk', flat=True), estado='APLICADA',
        )
        por_pago = {p.object_id: p for p in polizas}
        cotizaciones = {}
        canceladas = 0
        with transaction.atomic():
            for pago in pagos:
                poliza = por_pago.get(pago.pk)
                facturas = list(pago.solicitudes_factura.exclude(estado='CANCELADA'))
                if not poliza and not facturas:
                    continue
                linea = f'  COT-{pago.cotizacion_id:03d} {pago.fecha_pago}  ${pago.monto:,.2f}'
                if poliza:
                    linea += f'  póliza {poliza.tipo}-{poliza.folio}'
                    canceladas += 1
                    if aplicar:
                        poliza.cancelar(get_usuario_sistema(), MOTIVO)
                    cotizaciones[pago.cotizacion_id] = pago.cotizacion
                for sf in facturas:
                    linea += f'  · solicitud de factura #{sf.pk} ({sf.get_estado_display()}): revisar con el contador'
                self.stdout.write(linea)

            reconocidas = 0
            for cot in cotizaciones.values():
                if cot.estado in ESTADOS_INGRESO_DEVENGADO and aplicar:
                    reconocidas += bool(crear_poliza_reconocimiento_ingreso(cot))
        self.stdout.write(self.style.SUCCESS(
            f'\n{canceladas} pólizas de condonación por cancelar'
            + (f'; {reconocidas} ajustes de ingreso reconocido.' if aplicar else '.')
        ))
