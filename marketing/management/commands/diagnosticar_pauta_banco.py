"""
Explica por qué la pauta de una plataforma no cuadra con el banco. SOLO LECTURA.

Lista cada cargo del mes cuyo concepto menciona la plataforma (mismo patrón que
`conciliacion.cargos_bancarios`) y dice: si tiene póliza, en qué estado, qué
cuentas toca, qué regla de conciliación lo reconoce y si la conciliación lo
cuenta como clasificado. No escribe nada y no abre pendientes.

Uso:
    python manage.py diagnosticar_pauta_banco --mes 2026-10
    python manage.py diagnosticar_pauta_banco --mes 2026-10 --plataforma GOOGLE
"""
import calendar
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

from contabilidad.models import (
    ConfiguracionContable,
    MovimientoContable,
    MovimientoEstadoCuenta,
    ReglaConciliacion,
    normalizar_texto_banco,
)
from core_erp import impuestos
from marketing.models import GastoPublicitario
from marketing.services import conciliacion


def _mes(texto):
    try:
        anio, mes = (int(p) for p in texto.split('-'))
        date(anio, mes, 1)
    except ValueError as exc:
        raise CommandError('--mes debe ser AAAA-MM (ej. 2026-10).') from exc
    return anio, mes


class Command(BaseCommand):
    help = "Desglosa los cargos de una plataforma de anuncios en el banco. Solo lectura."

    def add_arguments(self, parser):
        parser.add_argument('--mes', required=True, help='Mes a revisar, AAAA-MM.')
        parser.add_argument('--plataforma', default='META', choices=sorted(conciliacion.PATRONES))

    def handle(self, *args, **opciones):
        anio, mes = _mes(opciones['mes'])
        plataforma = opciones['plataforma']
        patron = conciliacion.PATRONES[plataforma]
        inicio, fin = date(anio, mes, 1), date(anio, mes, calendar.monthrange(anio, mes)[1])
        config = ConfiguracionContable.objects.filter(operacion='GASTO_PUBLICIDAD', activa=True) \
            .select_related('cuenta').first()
        prefijo = config.cuenta.codigo_sat if config else None
        reglas = list(ReglaConciliacion.objects.filter(activa=True))

        gasto = impuestos.centavos(GastoPublicitario.objects.filter(
            fecha__range=(inicio, fin), campana__plataforma=plataforma).aggregate(t=Sum('monto'))['t'] or 0)
        con_iva = conciliacion.PLATAFORMAS_CON_IVA[plataforma]
        esperado = impuestos.con_iva(gasto) if con_iva else gasto
        self.stdout.write(f'{plataforma} {mes:02d}/{anio}: pauta ${gasto} sin IVA'
                          f'{f", ${esperado} con IVA" if con_iva else " (esta plataforma factura con IVA 0 %)"}.')
        self.stdout.write(f'Cuenta de publicidad configurada: {prefijo or "SIN CONFIGURAR"}.')

        movimientos = MovimientoEstadoCuenta.objects.filter(fecha__range=(inicio, fin), cargo__gt=0) \
            .select_related('movimiento_contable__poliza').order_by('fecha', 'id')
        contados = sin_clasificar = 0
        total = impuestos.centavos(0)
        for mov in movimientos:
            if not patron.search(normalizar_texto_banco(mov.descripcion)):
                continue
            poliza = mov.movimiento_contable.poliza if mov.movimiento_contable_id else None
            cuentas = []
            if poliza:
                cuentas = sorted(MovimientoContable.objects.filter(poliza=poliza, debe__gt=0)
                                 .values_list('cuenta__codigo_sat', flat=True))
            clasificado = bool(prefijo and poliza and poliza.estado == 'APLICADA'
                               and any(c.startswith(prefijo) for c in MovimientoContable.objects.filter(
                                   poliza=poliza).values_list('cuenta__codigo_sat', flat=True)))
            regla = next((r.nombre for r in reglas if r.coincide(mov)), 'ninguna')
            if clasificado:
                contados += 1
                total += mov.cargo
            else:
                sin_clasificar += 1
            self.stdout.write(
                f'{mov.fecha:%d/%m} ${mov.cargo:>10,.2f} | póliza: '
                f'{(poliza.estado + " " + poliza.origen) if poliza else "SIN PÓLIZA"} | '
                f'debe: {", ".join(cuentas) or "-"} | regla: {regla} | '
                f'{"CUENTA" if clasificado else "NO CUENTA"} | {mov.descripcion[:60]}')
        externos = conciliacion.pagos_externos(anio, mes, plataforma)
        self.stdout.write(f'Clasificados: {contados} por ${total}. Sin clasificar: {sin_clasificar}.')
        self.stdout.write(f'Pagados con otra tarjeta (pólizas fuera de BBVA): ${externos}.')
        self.stdout.write(f'Diferencia contra la pauta: ${impuestos.centavos(total + externos - esperado)}.')
