"""
Gasto publicitario de Meta y Google Ads (Issue #468, fases 2 y 8).

Uso:
    python manage.py sincronizar_gasto_anuncios                       # últimos 3 días
    python manage.py sincronizar_gasto_anuncios --desde 2026-10-01 --hasta 2026-10-31
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from marketing.services import conciliacion, gasto
from marketing.services.proveedores import ProveedorError, proveedor_google, proveedor_meta

DIA_CONCILIACION = 5  # Meta suele cerrar el cargo del mes anterior en los primeros días


def _fecha(texto, opcion):
    try:
        return date.fromisoformat(texto)
    except ValueError as exc:
        raise CommandError(f'{opcion} debe ser AAAA-MM-DD.') from exc


class Command(BaseCommand):
    help = 'Sincroniza el gasto diario de Meta y de Google Ads y concilia el mes anterior contra el banco.'

    def add_arguments(self, parser):
        parser.add_argument('--desde', help='AAAA-MM-DD (por defecto, hace 3 días).')
        parser.add_argument('--hasta', help='AAAA-MM-DD (por defecto, hoy).')

    def handle(self, *args, **options):
        desde = _fecha(options['desde'], '--desde') if options['desde'] else None
        hasta = _fecha(options['hasta'], '--hasta') if options['hasta'] else None
        errores, activos = [], 0
        for proveedor in (proveedor_meta(), proveedor_google()):
            if not proveedor.configurado():
                self.stdout.write(f'{proveedor.nombre} sin credenciales: sincronización apagada.')
                continue
            activos += 1
            try:  # un proveedor caído no detiene al otro
                r = gasto.sincronizar(desde, hasta, proveedor=proveedor)
                gasto.revisar_credencial(proveedor)
            except ProveedorError as exc:
                errores.append(f'{proveedor.nombre}: {exc}')
                continue
            self.stdout.write(self.style.SUCCESS(f"{proveedor.nombre}: {r['filas']} filas nuevas o corregidas."))
            hoy = timezone.localdate()
            if hoy.day >= DIA_CONCILIACION:
                anterior = hoy.replace(day=1) - timedelta(days=1)
                c = conciliacion.conciliar_mes(anterior.year, anterior.month, plataforma=proveedor.plataforma)
                self.stdout.write(f"Conciliación {proveedor.nombre} {anterior:%m/%Y}: {c['estado']} "
                                  f"(diferencia ${c['diferencia']}).")
        if errores:
            raise CommandError(' | '.join(errores))
        if not activos:
            self.stdout.write('Ninguna plataforma de anuncios configurada.')
