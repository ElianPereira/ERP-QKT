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

Las compras cuyo XML ya no existe (archivos perdidos con Cloudinary) se listan
con proveedor, fecha y total; las que sean personales se indican con `--ids`.

Simula por defecto; `--aplicar` escribe.

Uso:  python manage.py excluir_compras_sueldos [--ids 18,19] [--aplicar]
"""
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from comercial.models import Compra
from comercial.services import REGIMENES_RECEPTOR_NEGOCIO, regimen_receptor_de_xml
from contabilidad.models import MovimientoEstadoCuenta, Poliza
from contabilidad.signals import get_usuario_sistema

MOTIVO = 'CFDI del régimen de sueldos (no RESICO): gasto personal, no del negocio.'


class Command(BaseCommand):
    help = 'Cancela las pólizas y borra las compras con CFDI a un régimen distinto de RESICO (simula por defecto)'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Escribe los cambios.')
        parser.add_argument(
            '--ids', default='',
            help='Compras que se excluyen aunque su XML no se pueda leer (ej. 18,19,23).',
        )

    def handle(self, *args, **options):
        aplicar = options['aplicar']
        try:
            ids = {int(x) for x in options['ids'].split(',') if x.strip()}
        except ValueError as e:
            raise CommandError('--ids debe ser una lista de números separados por coma.') from e
        if not aplicar:
            self.stdout.write(self.style.WARNING('SIMULACIÓN — no se escribe nada\n'))

        self.tipo_compra = ContentType.objects.get_for_model(Compra)
        self.aplicar = aplicar
        self.canceladas = self.ligadas = self.borradas = 0
        personales = ilegibles = 0
        with transaction.atomic():
            for compra in Compra.objects.exclude(archivo_xml='').exclude(archivo_xml__isnull=True).order_by('fecha_emision'):
                if compra.pk in ids:
                    continue
                try:
                    with compra.archivo_xml.open('rb') as f:
                        regimen = regimen_receptor_de_xml(f.read())
                except Exception:
                    ilegibles += 1
                    self.stdout.write(self.style.WARNING(f'{self._linea(compra)}  · XML ilegible'))
                    continue
                if regimen and regimen not in REGIMENES_RECEPTOR_NEGOCIO:
                    personales += 1
                    self._excluir(compra, f'régimen {regimen}')

            elegidas = Compra.objects.filter(pk__in=ids).order_by('fecha_emision')
            faltantes = ids - set(elegidas.values_list('pk', flat=True))
            if faltantes:
                raise CommandError(f'No existen las compras: {", ".join(map(str, sorted(faltantes)))}.')
            for compra in elegidas:
                self._excluir(compra, 'indicada con --ids')

        self.stdout.write(self.style.SUCCESS(
            f'\n{personales} compras de otro régimen y {len(ids)} indicadas con --ids; {self.canceladas} pólizas '
            f'{"canceladas" if aplicar else "por cancelar"}; {self.borradas} compras '
            f'{"borradas" if aplicar else "por borrar"}; {self.ligadas} ligadas al banco (a mano); '
            f'{ilegibles} XML ilegibles.'
        ))

    def _linea(self, compra):
        return (f'  Compra #{compra.pk} {compra.fecha_emision}  {compra.proveedor_display[:40]}'
                f'  ${compra.total:,.2f}')

    def _excluir(self, compra, motivo):
        linea = f'{self._linea(compra)}  {motivo}'
        polizas = list(Poliza.objects.filter(content_type=self.tipo_compra, object_id=compra.pk)
                       .exclude(estado='CANCELADA'))
        if MovimientoEstadoCuenta.objects.filter(movimiento_contable__poliza__in=polizas).exists():
            self.ligadas += 1
            self.stdout.write(linea + '  · ligada al estado de cuenta: reclasificar a mano')
            return
        for poliza in polizas:
            self.canceladas += 1
            linea += f'  · póliza {poliza.tipo}-{poliza.folio} se cancela'
            if self.aplicar:
                poliza.cancelar(get_usuario_sistema(), MOTIVO)
        self.borradas += 1
        self.stdout.write(linea + '  · la compra se borra')
        if self.aplicar:
            compra.delete()
