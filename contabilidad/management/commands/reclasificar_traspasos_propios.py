"""
Pasa a la cuenta puente de traspasos (102.03) los fondeos entre cuentas propias que ya estaban asentados como
retiros/aportaciones del dueño o partidas por identificar. No cancela ni cambia importes: solo reapunta la
contrapartida. Lo que no puede resolver (periodo cerrado, otra cuenta) se lista.

Simula por defecto; `--aplicar` escribe. `--desde` acota por fecha del movimiento.

Uso:  python manage.py reclasificar_traspasos_propios [--desde 2026-11-01] [--aplicar]
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from contabilidad.services_traspasos import reclasificar_traspasos


class Command(BaseCommand):
    help = 'Reapunta a la cuenta puente los traspasos entre cuentas propias ya asentados (simula por defecto)'

    def add_arguments(self, parser):
        parser.add_argument('--desde', help='Fecha inicial AAAA-MM-DD del movimiento.')
        parser.add_argument('--aplicar', action='store_true', help='Escribe los cambios (sin esto solo simula).')

    def handle(self, *args, **opciones):
        desde = None
        if opciones['desde']:
            try:
                desde = date.fromisoformat(opciones['desde'])
            except ValueError as e:
                raise CommandError('--desde debe ser AAAA-MM-DD.') from e
        aplicar = opciones['aplicar']
        resultado = reclasificar_traspasos(desde=desde, aplicar=aplicar)

        verbo = 'Reapuntados' if aplicar else 'Se reapuntarían'
        self.stdout.write(self.style.MIGRATE_HEADING(f"{verbo}: {len(resultado['reapuntados'])}"))
        for mov, anterior in resultado['reapuntados']:
            self.stdout.write(f"  {mov.fecha:%d/%m/%Y}  ${mov.cargo or mov.abono:,.2f}  {anterior.codigo_sat} -> puente  "
                              f"({mov.estado_cuenta.cuenta_bancaria})")
        if resultado['sin_resolver']:
            self.stdout.write(self.style.WARNING(f"Sin resolver: {len(resultado['sin_resolver'])}"))
            for mov, motivo in resultado['sin_resolver']:
                etiqueta = f"{mov.fecha:%d/%m/%Y} ${mov.cargo or mov.abono:,.2f}" if mov else '-'
                self.stdout.write(f"  {etiqueta}: {motivo}")
        if not aplicar:
            self.stdout.write('Simulación: usa --aplicar para escribir.')
