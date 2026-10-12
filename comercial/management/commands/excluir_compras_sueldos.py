"""
Detecta las Compras cargadas desde un CFDI emitido a otro régimen del mismo
RFC (605 Sueldos y salarios): son gastos personales, no del negocio. La carga
masiva ya las rechaza; este comando limpia las que entraron antes.

Lee `RegimenFiscalReceptor` del XML guardado de cada Compra. Con `--aplicar`
cancela su póliza (conserva folio y movimientos, sale de saldos y reportes) y
borra la Compra, para que tampoco cuente en los reportes de gastos (borrado
físico autorizado por el propietario el 2026-10-12). Si algún renglón del
estado de cuenta está ligado a esa póliza, el gasto salió de la cuenta del
negocio: no se toca nada, se reporta para reclasificarlo a mano.

Simula por defecto; `--aplicar` escribe.

Uso:  python manage.py excluir_compras_sueldos [--aplicar]
"""
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction

from comercial.models import Compra
from comercial.services import REGIMENES_RECEPTOR_NEGOCIO, regimen_receptor_de_xml
from contabilidad.models import MovimientoEstadoCuenta, Poliza
from contabilidad.signals import get_usuario_sistema

MOTIVO = 'CFDI emitido al régimen de sueldos (no RESICO): gasto personal, no del negocio.'


class Command(BaseCommand):
    help = 'Cancela las pólizas y borra las compras con CFDI a un régimen distinto de RESICO (simula por defecto)'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Escribe los cambios.')

    def handle(self, *args, **options):
        aplicar = options['aplicar']
        if not aplicar:
            self.stdout.write(self.style.WARNING('SIMULACIÓN — no se escribe nada\n'))

        tipo_compra = ContentType.objects.get_for_model(Compra)
        personales = ilegibles = canceladas = ligadas = borradas = 0
        with transaction.atomic():
            for compra in Compra.objects.exclude(archivo_xml='').exclude(archivo_xml__isnull=True).order_by('fecha_emision'):
                try:
                    with compra.archivo_xml.open('rb') as f:
                        regimen = regimen_receptor_de_xml(f.read())
                except Exception as e:
                    ilegibles += 1
                    self.stdout.write(self.style.WARNING(f'  Compra #{compra.pk}: XML ilegible ({e})'))
                    continue
                if not regimen or regimen in REGIMENES_RECEPTOR_NEGOCIO:
                    continue

                personales += 1
                linea = (f'  Compra #{compra.pk} {compra.fecha_emision}  {compra.proveedor_display[:40]}'
                         f'  ${compra.total:,.2f}  régimen {regimen}')
                polizas = list(Poliza.objects.filter(content_type=tipo_compra, object_id=compra.pk)
                               .exclude(estado='CANCELADA'))
                if MovimientoEstadoCuenta.objects.filter(movimiento_contable__poliza__in=polizas).exists():
                    ligadas += 1
                    self.stdout.write(linea + '  · ligada al estado de cuenta: reclasificar a mano')
                    continue
                for poliza in polizas:
                    canceladas += 1
                    linea += f'  · póliza {poliza.tipo}-{poliza.folio} se cancela'
                    if aplicar:
                        poliza.cancelar(get_usuario_sistema(), MOTIVO)
                borradas += 1
                self.stdout.write(linea + '  · la compra se borra')
                if aplicar:
                    compra.delete()

        self.stdout.write(self.style.SUCCESS(
            f'\n{personales} compras de otro régimen; {canceladas} pólizas '
            f'{"canceladas" if aplicar else "por cancelar"}; {borradas} compras '
            f'{"borradas" if aplicar else "por borrar"}; {ligadas} ligadas al banco (a mano); '
            f'{ilegibles} XML ilegibles.'
        ))
