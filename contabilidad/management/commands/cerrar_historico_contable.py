"""
Arranca la contabilidad del ERP en una fecha, dejando fuera todo lo anterior.

Para cuando los periodos previos ya los cerró el contador **fuera** del ERP: las
pólizas anteriores del sistema no son los libros, son captura parcial, y lo
único que hacen es arrastrar descuadres a la conciliación bancaria.

**Cancela, no borra.** Una póliza CANCELADA conserva sus movimientos y su
auditoría pero queda fuera de todo saldo y todo reporte, porque en el ERP entero
solo suma `estado='APLICADA'`. Borrarlas sí sería destructivo: están ligadas por
content_type a pagos, compras, recibos de nómina y pagos de Airbnb que siguen
vivos.

Simula por defecto. Uso:
    python manage.py cerrar_historico_contable --hasta 2026-06-30
    python manage.py cerrar_historico_contable --hasta 2026-06-30 --aplicar
"""
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from contabilidad.services import cerrar_historico_contable


class Command(BaseCommand):
    help = "Cancela toda la contabilidad del ERP hasta una fecha. Simula salvo --aplicar."

    def add_arguments(self, parser):
        parser.add_argument('--hasta', required=True,
                            help="Fecha de corte inclusive, YYYY-MM-DD (ej. 2026-06-30).")
        parser.add_argument('--aplicar', action='store_true',
                            help="Escribe. Sin esto solo reporta.")
        parser.add_argument('--usuario',
                            help="Username que queda asentado como responsable. "
                                 "Por defecto, el primer superusuario.")

    def handle(self, *args, **opciones):
        usuario = self._responsable(opciones.get('usuario'))

        try:
            informe = cerrar_historico_contable(
                opciones['hasta'], usuario=usuario, aplicar=opciones['aplicar'],
            )
        except ValueError as e:
            raise CommandError(str(e))

        self._reportar(informe, usuario)

    def _responsable(self, username):
        if username:
            try:
                return User.objects.get(username=username)
            except User.DoesNotExist:
                raise CommandError(f"No existe el usuario '{username}'.")
        usuario = User.objects.filter(is_superuser=True).order_by('id').first()
        if not usuario:
            raise CommandError(
                "No hay ningún superusuario para asentar como responsable del cierre. "
                "Pasa --usuario."
            )
        return usuario

    def _reportar(self, informe, usuario):
        corte = informe['fecha_corte']
        self.stdout.write('=' * 78)
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"  Cierre del histórico contable hasta el {corte:%d/%m/%Y}"
        ))
        self.stdout.write('=' * 78)

        if not informe['total']:
            self.stdout.write(self.style.SUCCESS(
                "  No hay ninguna póliza hasta esa fecha: no hay nada que cerrar."
            ))
            return

        self.stdout.write(f"  Pólizas afectadas: {informe['total']}")
        self.stdout.write(f"  Responsable asentado: {usuario}")

        self.stdout.write(self.style.MIGRATE_HEADING("\n  Por periodo"))
        for periodo, cuantas in informe['por_periodo'].items():
            self.stdout.write(f"    {periodo}   {cuantas:>5}")

        self.stdout.write(self.style.MIGRATE_HEADING("\n  Por origen"))
        for origen, cuantas in informe['por_origen'].items():
            self.stdout.write(f"    {origen:<32} {cuantas:>5}")

        self.stdout.write(self.style.MIGRATE_HEADING("\n  Saldo de bancos a la fecha de corte"))
        for fila in informe['cuentas']:
            self.stdout.write(
                f"    {str(fila['cuenta']):<34} "
                f"antes ${fila['saldo_antes']:>14,.2f}   "
                f"después ${fila['saldo_despues']:>14,.2f}"
            )

        self.stdout.write('-' * 78)
        if informe['aplicado']:
            self.stdout.write(self.style.SUCCESS(
                f"  {informe['canceladas']} póliza(s) canceladas. Sus movimientos se "
                "conservan; quedan fuera de saldos y reportes."
            ))
            self.stdout.write(
                "  Siguiente paso: captura el saldo de apertura certificado de cada\n"
                f"  cuenta al {corte:%d/%m/%Y} en /admin/contabilidad/saldoapertura/,\n"
                "  leyéndolo del estado de cuenta del banco."
            )
        else:
            self.stdout.write(self.style.WARNING(
                "  SIMULACIÓN: no se modificó nada. Vuelve a correrlo con --aplicar."
            ))
