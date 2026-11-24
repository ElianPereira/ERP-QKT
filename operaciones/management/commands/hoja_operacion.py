"""
Genera en una carpeta la hoja de operación de los eventos de una o varias fechas: un PDF por
cotización más una agenda (`AGENDA_<min>_<max>.pdf`) con portada-índice por día.

Uso:
    python manage.py hoja_operacion --fechas 2026-11-27,2026-11-28 --salida /ruta/carpeta
"""
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core_erp.documentos import nombre_archivo
from operaciones.services_hoja import cotizaciones_operables, generar_hoja_pdf


def _fechas(texto):
    try:
        fechas = sorted({date.fromisoformat(parte.strip()) for parte in texto.split(',') if parte.strip()})
    except ValueError as exc:
        raise CommandError(f'Fecha inválida en --fechas (usa AAAA-MM-DD): {exc}') from exc
    if not fechas:
        raise CommandError('--fechas no trae ninguna fecha.')
    return fechas


class Command(BaseCommand):
    help = 'Genera los PDF de la hoja de operación (uno por cotización y una agenda) para las fechas dadas.'

    def add_arguments(self, parser):
        parser.add_argument('--fechas', required=True, help='Fechas AAAA-MM-DD separadas por coma.')
        parser.add_argument('--salida', required=True, help='Carpeta donde se guardan los PDF (se crea si falta).')

    def handle(self, *args, **opciones):
        fechas = _fechas(opciones['fechas'])
        cotizaciones = list(cotizaciones_operables(fechas))
        if not cotizaciones:
            raise CommandError('No hay eventos operables (confirmados o ejecutados) en esas fechas.')

        salida = Path(opciones['salida'])
        salida.mkdir(parents=True, exist_ok=True)
        for cotizacion in cotizaciones:
            ruta = salida / nombre_archivo('HojaOperacion', cotizacion.fecha_evento, f'COT-{cotizacion.pk:03d}')
            ruta.write_bytes(generar_hoja_pdf([cotizacion]))
            self.stdout.write(f'{ruta}')
        agenda = salida / f'AGENDA_{fechas[0]:%Y%m%d}_{fechas[-1]:%Y%m%d}.pdf'
        agenda.write_bytes(generar_hoja_pdf(cotizaciones))
        self.stdout.write(f'{agenda}')
        self.stdout.write(self.style.SUCCESS(f'{len(cotizaciones)} hoja(s) y 1 agenda en {salida}'))
