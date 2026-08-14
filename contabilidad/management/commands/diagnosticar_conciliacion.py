"""
Explica de dónde sale la diferencia de una conciliación bancaria.

Es de SOLO LECTURA: no crea, no modifica y no borra nada. Existe para responder
una pregunta concreta que la pantalla de conciliación no respondía —"¿de dónde
salen estos $41,968.96?"— separando las tres cosas que hasta ahora se sumaban
en un único número:

  1. Lo que ya venía descuadrado ANTES del periodo (diferencia arrastrada):
     saldo de libros al cierre del día anterior al primer movimiento del estado
     de cuenta, contra el saldo inicial que imprime el propio banco. Si esto no
     es cero, el problema no está en este estado de cuenta.
  2. Movimientos del banco sin asiento contable en el periodo.
  3. Asientos sobre la cuenta de bancos sin movimiento bancario en el periodo.

Uso:
    python manage.py diagnosticar_conciliacion --cuenta 1 --mes 7 --anio 2026
    python manage.py diagnosticar_conciliacion --estado-cuenta 12
    python manage.py diagnosticar_conciliacion            # el último procesado
"""
from django.core.management.base import BaseCommand, CommandError

from contabilidad.models import EstadoCuentaBancario
from contabilidad.services_estados_cuenta import analizar_conciliacion


def _money(valor):
    return f"${valor:>15,.2f}"


class Command(BaseCommand):
    help = "Desglosa la diferencia de una conciliación bancaria. Solo lectura."

    def add_arguments(self, parser):
        parser.add_argument('--estado-cuenta', type=int, help="ID del EstadoCuentaBancario.")
        parser.add_argument('--cuenta', type=int, help="ID de la CuentaBancaria.")
        parser.add_argument('--mes', type=int, help="Mes del periodo (1-12).")
        parser.add_argument('--anio', type=int, help="Año del periodo.")

    def handle(self, *args, **opciones):
        estado_cuenta = self._localizar(opciones)
        datos = analizar_conciliacion(estado_cuenta)

        self._encabezado(estado_cuenta, datos)
        self._bloque_arrastre(datos)
        self._bloque_banco_sin_asiento(datos)
        self._bloque_libros_sin_banco(datos)
        self._bloque_cuadre(datos)

    # ------------------------------------------------------------------
    # Selección del estado de cuenta
    # ------------------------------------------------------------------

    def _localizar(self, opciones):
        qs = EstadoCuentaBancario.objects.filter(estado='PROCESADO')

        if opciones.get('estado_cuenta'):
            try:
                return EstadoCuentaBancario.objects.get(pk=opciones['estado_cuenta'])
            except EstadoCuentaBancario.DoesNotExist:
                raise CommandError(f"No existe el estado de cuenta #{opciones['estado_cuenta']}.")

        if opciones.get('cuenta'):
            qs = qs.filter(cuenta_bancaria_id=opciones['cuenta'])
        if opciones.get('mes'):
            qs = qs.filter(periodo_mes=opciones['mes'])
        if opciones.get('anio'):
            qs = qs.filter(periodo_anio=opciones['anio'])

        estado_cuenta = qs.order_by('-periodo_anio', '-periodo_mes', '-id').first()
        if not estado_cuenta:
            raise CommandError(
                "No se encontró ningún estado de cuenta PROCESADO con esos criterios. "
                "Cárgalo y procésalo desde /admin/contabilidad/estadocuentabancario/."
            )
        return estado_cuenta

    # ------------------------------------------------------------------
    # Bloques del reporte
    # ------------------------------------------------------------------

    def _regla(self, titulo=''):
        if titulo:
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n{titulo}"))
        self.stdout.write('-' * 78)

    def _encabezado(self, estado_cuenta, datos):
        self.stdout.write('=' * 78)
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"  Diagnóstico de conciliación — {datos['cuenta_bancaria']}"
        ))
        self.stdout.write(
            f"  Estado de cuenta #{estado_cuenta.pk} · periodo "
            f"{estado_cuenta.periodo_mes:02d}/{estado_cuenta.periodo_anio} · "
            f"del {datos['fecha_inicio_periodo']:%d/%m/%Y} al {datos['fecha_corte']:%d/%m/%Y}"
        )
        self.stdout.write('=' * 78)
        self.stdout.write("  Este comando no modifica nada: solo lee y explica.")

    def _bloque_arrastre(self, datos):
        self._regla("1. ¿Venía descuadrado desde antes del periodo?")
        arrastrada = datos['diferencia_arrastrada']
        self.stdout.write(
            f"  Saldo del banco al iniciar el periodo   {_money(datos['saldo_inicial_banco'])}"
        )
        self.stdout.write(
            f"  Saldo en libros al iniciar el periodo   {_money(datos['saldo_libros_inicio'])}"
        )
        etiqueta = f"  DIFERENCIA ARRASTRADA                  {_money(arrastrada)}"
        if abs(arrastrada) < 0.01:
            self.stdout.write(self.style.SUCCESS(etiqueta))
            self.stdout.write("  Los libros arrancaban el periodo cuadrados con el banco.")
        else:
            self.stdout.write(self.style.ERROR(etiqueta))
            self.stdout.write(
                "  Este importe NO se origina en este estado de cuenta: ya existía\n"
                "  antes del primer movimiento. Causas habituales: pólizas anteriores\n"
                "  al primer estado de cuenta cargado, o asientos sobre la cuenta de\n"
                "  bancos que nunca pasaron por este banco. Se corrige con un saldo de\n"
                "  apertura certificado, no tocando este periodo."
            )

    def _bloque_banco_sin_asiento(self, datos):
        self._regla("2. Movimientos del banco sin asiento contable")
        filas = datos['detalle_banco_sin_asiento'].order_by('fecha', 'id')
        if not filas.exists():
            self.stdout.write(self.style.SUCCESS("  Ninguno: todo el estado de cuenta está emparejado."))
            return
        for mov in filas:
            tipo = 'CARGO' if mov.cargo > 0 else 'ABONO'
            monto = mov.cargo if mov.cargo > 0 else mov.abono
            self.stdout.write(
                f"  {mov.fecha:%d/%m/%Y}  {tipo}  {_money(monto)}  {mov.descripcion[:40]}"
            )
        self.stdout.write(
            f"  Suma de cargos {_money(datos['cargos_banco_no_registrados'])}   "
            f"Suma de abonos {_money(datos['abonos_banco_no_registrados'])}"
        )
        self.stdout.write("  Falta registrar estos movimientos en una póliza, o emparejarlos a mano.")

    def _bloque_libros_sin_banco(self, datos):
        self._regla("3. Asientos sobre la cuenta de bancos sin movimiento bancario")
        filas = datos['detalle_libros_sin_banco'].order_by('poliza__fecha', 'id')
        if not filas.exists():
            self.stdout.write(self.style.SUCCESS("  Ninguno: toda la contabilidad del periodo tiene respaldo bancario."))
            return
        for mov in filas:
            tipo = 'CARGO' if mov.debe > 0 else 'ABONO'
            monto = mov.debe if mov.debe > 0 else mov.haber
            folio = f"{mov.poliza.tipo}-{str(mov.poliza.folio).zfill(4)}"
            self.stdout.write(
                f"  {mov.poliza.fecha:%d/%m/%Y}  {folio:<8}  {tipo}  {_money(monto)}  "
                f"{(mov.concepto or mov.poliza.concepto)[:34]}"
            )
        self.stdout.write(
            f"  Depósitos en tránsito {_money(datos['abonos_empresa_no_abonados'])}   "
            f"Salidas no cobradas {_money(datos['cargos_empresa_no_cobrados'])}"
        )
        self.stdout.write(
            "  Si el banco los reflejará en el siguiente estado de cuenta, son partidas\n"
            "  en tránsito y la conciliación sigue cuadrando. Si no, la póliza está mal."
        )

    def _bloque_cuadre(self, datos):
        self._regla("4. Cuadre del periodo")
        libros_ajustado = (
            datos['saldo_segun_libros']
            - datos['diferencia_arrastrada']
            - datos['cargos_banco_no_registrados']
            + datos['abonos_banco_no_registrados']
        )
        banco_ajustado = (
            datos['saldo_segun_banco']
            + datos['abonos_empresa_no_abonados']
            - datos['cargos_empresa_no_cobrados']
        )
        diferencia = libros_ajustado - banco_ajustado

        self.stdout.write(f"  Saldo en libros al corte                {_money(datos['saldo_segun_libros'])}")
        self.stdout.write(f"  (-) diferencia arrastrada               {_money(datos['diferencia_arrastrada'])}")
        self.stdout.write(f"  (-) cargos del banco sin registrar      {_money(datos['cargos_banco_no_registrados'])}")
        self.stdout.write(f"  (+) abonos del banco sin registrar      {_money(datos['abonos_banco_no_registrados'])}")
        self.stdout.write(f"  = LIBROS AJUSTADO                       {_money(libros_ajustado)}")
        self.stdout.write('')
        self.stdout.write(f"  Saldo del banco al corte                {_money(datos['saldo_segun_banco'])}")
        self.stdout.write(f"  (+) depósitos en tránsito               {_money(datos['abonos_empresa_no_abonados'])}")
        self.stdout.write(f"  (-) salidas que el banco no ha cobrado  {_money(datos['cargos_empresa_no_cobrados'])}")
        self.stdout.write(f"  = BANCO AJUSTADO                        {_money(banco_ajustado)}")
        self._regla()

        if abs(diferencia) < 0.01:
            self.stdout.write(self.style.SUCCESS(f"  DIFERENCIA DEL PERIODO                  {_money(diferencia)}  ✓ cuadra"))
        else:
            self.stdout.write(self.style.ERROR(f"  DIFERENCIA DEL PERIODO                  {_money(diferencia)}"))
            self.stdout.write(
                "  Queda un residuo que no explican las partidas anteriores. Revisa que\n"
                "  cada emparejamiento apunte al asiento correcto y que ninguna póliza\n"
                "  del periodo esté capturada con importe distinto al del banco."
            )
