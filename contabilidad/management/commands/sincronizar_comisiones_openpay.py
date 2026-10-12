"""
Conciliación mensual de las comisiones de Openpay contra su API.

Pide a Openpay los cargos del mes, cada uno con su `fee`, y los compara con
las pólizas COMISION_OPENPAY del ERP. Registra las que falten (un webhook que
no llegó o un cobro que no trajo `fee`), siempre contra «Comisiones Openpay por
comprobar», y da el total del mes: es lo que debe amparar la factura mensual de
Openpay. Un cargo cobrado en Openpay sin registro en el ERP solo se reporta:
sin su OpenpayTransaccion no hay a qué ligar la póliza.

Simula por defecto; `--aplicar` escribe. Sin `--mes`, revisa el mes anterior
(corre como cron a inicio de mes). No avisa a nadie (decisión del propietario):
el resultado queda en el log del cron.

Uso:  python manage.py sincronizar_comisiones_openpay [--mes 2026-09] [--aplicar]
"""
import calendar
from datetime import date
from decimal import Decimal

import requests
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from comercial.models import OpenpayTransaccion
from comercial.services_openpay import listar_cargos_openpay
from contabilidad.models import Poliza
from contabilidad.signals import crear_poliza_comision_openpay

# Estados en los que Openpay ya cobró su comisión (un reembolso no la devuelve).
ESTADOS_CON_COMISION = ('completed', 'refunded')


def _periodo(mes):
    if mes:
        try:
            anio, numero = (int(p) for p in mes.split('-'))
            inicio = date(anio, numero, 1)
        except ValueError as e:
            raise CommandError('--mes debe tener formato AAAA-MM.') from e
    else:
        hoy = timezone.localdate()
        inicio = (hoy.replace(day=1) - date.resolution).replace(day=1)
    return inicio, inicio.replace(day=calendar.monthrange(inicio.year, inicio.month)[1])


def _comision(cargo):
    fee = cargo.get('fee') or {}
    return Decimal(str(fee.get('amount') or 0)) + Decimal(str(fee.get('tax') or 0))


class Command(BaseCommand):
    help = 'Compara las comisiones de Openpay del mes contra las pólizas del ERP (simula por defecto)'

    def add_arguments(self, parser):
        parser.add_argument('--mes', help='Mes a revisar, AAAA-MM (default: el anterior).')
        parser.add_argument('--aplicar', action='store_true', help='Registra las comisiones que falten.')

    def handle(self, *args, **options):
        desde, hasta = _periodo(options['mes'])
        aplicar = options['aplicar']
        try:
            cargos = listar_cargos_openpay(desde, hasta)
        except requests.RequestException as e:
            raise CommandError(f'Openpay no respondió el listado de cargos: {e}') from e

        cargos = [c for c in cargos if c.get('status') in ESTADOS_CON_COMISION and _comision(c) > 0]
        transacciones = OpenpayTransaccion.objects.in_bulk([c['id'] for c in cargos], field_name='openpay_id')
        con_poliza = set(Poliza.objects.filter(
            origen='COMISION_OPENPAY', content_type=ContentType.objects.get_for_model(OpenpayTransaccion),
            object_id__in=[t.pk for t in transacciones.values()],
        ).exclude(estado='CANCELADA').values_list('object_id', flat=True))

        if not aplicar:
            self.stdout.write(self.style.WARNING('SIMULACIÓN — no se escribe nada\n'))
        self.stdout.write(f'Openpay, cargos del {desde} al {hasta}:')

        total, faltantes, sin_registro = Decimal('0.00'), [], []
        for cargo in cargos:
            comision = _comision(cargo)
            total += comision
            transaccion = transacciones.get(cargo['id'])
            if transaccion is None:
                estado = 'SIN REGISTRO EN EL ERP'
                sin_registro.append(cargo['id'])
            elif transaccion.pk in con_poliza:
                estado = 'ok'
            else:
                estado = 'faltaba, registrada' if aplicar else 'falta'
                faltantes.append(cargo['id'])
                if aplicar:
                    crear_poliza_comision_openpay(transaccion, cargo['fee'])
            self.stdout.write(f"  {cargo['id']}  {cargo.get('method', '')}  comisión ${comision:,.2f}  {estado}")

        resumen = (
            f'Comisiones de Openpay de {desde:%m/%Y}: ${total:,.2f} en {len(cargos)} cargos; '
            f'es lo que debe traer su factura mensual. '
            f'{len(faltantes)} sin póliza en el ERP; {len(sin_registro)} cobrados en Openpay sin registro en el ERP.'
        )
        self.stdout.write(self.style.SUCCESS(f'\n{resumen}'))
