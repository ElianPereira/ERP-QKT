"""
Pasa al esquema de cuenta puente las comisiones de Openpay registradas antes:

- Pólizas COMISION_OPENPAY que cargaban gasto e IVA acreditable con cada
  cobro: se cancelan y se reemiten contra «Comisiones Openpay por comprobar».
- Compras de proveedores que cobran descontando de los depósitos (factura
  mensual de Openpay) cuya póliza abonaba Bancos —una salida de dinero que
  nunca ocurrió—: se cancelan y se reemiten contra la cuenta puente, y se les
  quita la cuenta de pago.

Cancela, no borra (mismo criterio que `corregir_polizas_condonacion`). Un
movimiento del estado de cuenta que estuviera ligado a una póliza cancelada
queda sin asignar para volver a emparejarlo.

Simula por defecto; `--aplicar` escribe. `--desde` acota por fecha de póliza
para no tocar periodos que el contador ya cerró.

Uso:  python manage.py regularizar_comisiones_openpay [--desde 2026-09-01] [--aplicar]
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from comercial.models import Compra, OpenpayTransaccion
from contabilidad.models import MovimientoEstadoCuenta, Poliza
from contabilidad.signals import (
    crear_poliza_comision_openpay,
    emitir_poliza_compra,
    get_cuenta,
    get_usuario_sistema,
)

MOTIVO = 'Comisión de Openpay: pasa a «Comisiones Openpay por comprobar» (factura mensual).'


class Command(BaseCommand):
    help = 'Reemite las comisiones y facturas de Openpay contra la cuenta puente (simula por defecto)'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Escribe los cambios.')
        parser.add_argument('--desde', help='Solo pólizas con fecha >= AAAA-MM-DD.')

    def handle(self, *args, **options):
        cuenta_puente = get_cuenta('COMISIONES_POR_COMPROBAR')
        if not cuenta_puente:
            raise CommandError('Falta configurar la operación COMISIONES_POR_COMPROBAR.')

        polizas = Poliza.objects.exclude(estado='CANCELADA').exclude(movimientos__cuenta=cuenta_puente)
        if options['desde']:
            try:
                polizas = polizas.filter(fecha__gte=date.fromisoformat(options['desde']))
            except ValueError as e:
                raise CommandError('--desde debe tener formato AAAA-MM-DD.') from e

        compras = {
            c.pk: c for c in Compra.objects.filter(proveedor__descuenta_de_depositos=True)
        }
        comisiones = list(polizas.filter(origen='COMISION_OPENPAY').order_by('fecha', 'folio'))
        transacciones = OpenpayTransaccion.objects.in_bulk([p.object_id for p in comisiones])
        facturas = list(polizas.filter(origen='COMPRA', object_id__in=list(compras)).order_by('fecha', 'folio'))

        aplicar = options['aplicar']
        if not aplicar:
            self.stdout.write(self.style.WARNING('SIMULACIÓN — no se escribe nada\n'))

        usuario = get_usuario_sistema()
        liberados = 0
        with transaction.atomic():
            for poliza in comisiones + facturas:
                if poliza.origen == 'COMISION_OPENPAY' and poliza.object_id not in transacciones:
                    self.stdout.write(self.style.WARNING(
                        f'  {poliza.tipo}-{poliza.folio:04d}  sin transacción de Openpay: se deja como está'
                    ))
                    continue
                total = sum(m.debe for m in poliza.movimientos.all())
                self.stdout.write(f'  {poliza.tipo}-{poliza.folio:04d}  {poliza.fecha}  ${total:,.2f}  {poliza.concepto}')
                ligados = MovimientoEstadoCuenta.objects.filter(movimiento_contable__poliza=poliza)
                liberados += ligados.count()
                if not aplicar:
                    continue
                ligados.update(movimiento_contable=None, match_automatico=False, confirmado=False)
                poliza.cancelar(usuario, MOTIVO)
                if poliza.origen == 'COMISION_OPENPAY':
                    # Lo que salió del banco es la línea de bancos de la póliza.
                    crear_poliza_comision_openpay(transacciones[poliza.object_id], {'amount': poliza.movimientos.get(haber__gt=0).haber})
                else:
                    compra = compras[poliza.object_id]
                    Compra.objects.filter(pk=compra.pk).update(cuenta_pago=None)
                    compra.cuenta_pago = None
                    emitir_poliza_compra(compra)

        self.stdout.write(self.style.SUCCESS(
            f'\n{len(comisiones)} comisiones y {len(facturas)} facturas de Openpay por reemitir; '
            f'{liberados} movimientos del banco quedan sin asignar para volver a emparejarlos.'
        ))
