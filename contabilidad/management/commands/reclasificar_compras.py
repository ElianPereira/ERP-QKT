"""
Reapunta lo ya asentado al catálogo de categorías de gasto (Issue #390): la
línea de gasto de cada Compra a la cuenta que hoy le toca por su categoría y
por si es deducible, y los asientos de otras pólizas en cuentas retiradas
(601.03.04 Comisiones bancarias → 602.03; 601.05 → 601.05.02) a su cuenta
nueva. Solo cambia la cuenta: importes, folios y cuadre quedan igual.

Simula por defecto; `--aplicar` escribe. `--desde` acota por fecha (emisión
de la Compra / fecha de la póliza) para no tocar periodos que el contador ya
cerró.

Uso:  python manage.py reclasificar_compras [--desde 2026-07-01] [--aplicar]
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from contabilidad.services_compras import reclasificar_historico


class Command(BaseCommand):
    help = 'Reapunta compras y asientos al catálogo de categorías de gasto (simula por defecto)'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Escribe los cambios.')
        parser.add_argument('--desde', help='Solo compras y pólizas con fecha >= AAAA-MM-DD.')

    def handle(self, *args, **opts):
        desde = None
        if opts['desde']:
            try:
                desde = date.fromisoformat(opts['desde'])
            except ValueError as exc:
                raise CommandError('--desde debe ser AAAA-MM-DD') from exc

        with transaction.atomic():
            resultado = reclasificar_historico(desde=desde, aplicar=opts['aplicar'])

        for compra, cambios in resultado['compras']:
            for actual, nueva, importe in cambios:
                self.stdout.write(
                    f"Compra #{compra.pk} {compra.fecha_emision} {compra.proveedor_display or ''} "
                    f"[{compra.get_categoria_display()}{'' if compra.es_deducible else ', sin CFDI'}]: "
                    f"{actual.codigo_sat} → {nueva.codigo_sat} ${importe:,.2f}")
        for mov, nueva in resultado['asientos']:
            self.stdout.write(
                f"Póliza {mov.poliza.tipo}-{mov.poliza.folio:04d} {mov.poliza.fecha}: "
                f"{mov.cuenta.codigo_sat} → {nueva.codigo_sat} ${mov.debe or mov.haber:,.2f}")

        lineas = sum(len(c) for _, c in resultado['compras'])
        resumen = (f"{len(resultado['compras'])} compra(s) ({lineas} línea(s)) y "
                   f"{len(resultado['asientos'])} asiento(s) de otras pólizas")
        if opts['aplicar']:
            self.stdout.write(self.style.SUCCESS(f"Reclasificados: {resumen}."))
        else:
            self.stdout.write(self.style.WARNING(f"Simulación: {resumen} cambiarían. Usa --aplicar para escribir."))
