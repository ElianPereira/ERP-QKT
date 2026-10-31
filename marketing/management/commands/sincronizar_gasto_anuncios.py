"""
Gasto publicitario de Meta (Issue #468, fase 2).

Uso:
    python manage.py sincronizar_gasto_anuncios                       # últimos 3 días
    python manage.py sincronizar_gasto_anuncios --desde 2026-10-01 --hasta 2026-10-31
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from marketing.services import conciliacion, gasto
from marketing.services.proveedores import ProveedorError, proveedor_meta

DIA_CONCILIACION = 5  # Meta suele cerrar el cargo del mes anterior en los primeros días


def _fecha(texto, opcion):
    try:
        return date.fromisoformat(texto)
    except ValueError as exc:
        raise CommandError(f'{opcion} debe ser AAAA-MM-DD.') from exc


class Command(BaseCommand):
    help = 'Sincroniza el gasto diario de Meta y concilia el mes anterior contra el banco.'

    def add_arguments(self, parser):
        parser.add_argument('--desde', help='AAAA-MM-DD (por defecto, hace 3 días).')
        parser.add_argument('--hasta', help='AAAA-MM-DD (por defecto, hoy).')

    def handle(self, *args, **options):
        desde = _fecha(options['desde'], '--desde') if options['desde'] else None
        hasta = _fecha(options['hasta'], '--hasta') if options['hasta'] else None
        proveedor = proveedor_meta()
        if not proveedor.configurado():
            self.stdout.write('Meta Ads sin credenciales: sincronización apagada.')
            return
        try:
            r = gasto.sincronizar(desde, hasta, proveedor=proveedor)
            gasto.revisar_credencial(proveedor)
        except ProveedorError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Gasto: {r['filas']} filas nuevas o corregidas."))
        hoy = timezone.localdate()
        if hoy.day >= DIA_CONCILIACION:
            anterior = hoy.replace(day=1) - timedelta(days=1)
            c = conciliacion.conciliar_mes(anterior.year, anterior.month)
            self.stdout.write(f"Conciliación {anterior:%m/%Y}: {c['estado']} (diferencia ${c['diferencia']}).")
