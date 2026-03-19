"""
Servicios de Reportes Contables
================================
Estado de Resultados, Balance General, Libro Mayor, Auxiliar de Cuentas.
Usa la misma lógica de partida doble que BalanzaComprobacionService.

ERP Quinta Ko'ox Tanil
"""
from decimal import Decimal
from datetime import date
from typing import Dict, List, Optional
from django.db.models import Sum, Q


class EstadoResultadosService:
    """
    Genera el Estado de Resultados (P&L) para un período.
    Estructura:
        Ingresos (400)
        - Costo de Ventas (500)
        = Utilidad Bruta
        - Gastos de Operación (600)
        = Utilidad de Operación
        +/- Otros Ingresos/Gastos (402)
        = Utilidad antes de Impuestos
    """

    @classmethod
    def generar(
        cls,
        fecha_inicio: date,
        fecha_fin: date,
        unidad_negocio=None,
        nivel_detalle: int = 4,
    ) -> Dict:
        from contabilidad.models import CuentaContable, MovimientoContable

        filtros = Q(poliza__estado='APLICADA')
        filtros &= Q(poliza__fecha__gte=fecha_inicio, poliza__fecha__lte=fecha_fin)
        if unidad_negocio:
            filtros &= Q(poliza__unidad_negocio=unidad_negocio)

        def _sumar_tipo(tipo: str) -> List[Dict]:
            """Suma movimientos de cuentas de un tipo, devuelve detalle por cuenta."""
            cuentas = CuentaContable.objects.filter(
                tipo=tipo, activa=True, nivel__lte=nivel_detalle
            ).order_by('codigo_sat')

            lineas = []
            for cuenta in cuentas:
                datos = MovimientoContable.objects.filter(
                    filtros, cuenta=cuenta
                ).aggregate(debe=Sum('debe'), haber=Sum('haber'))

                debe = datos['debe'] or Decimal('0.00')
                haber = datos['haber'] or Decimal('0.00')

                # Ingresos: naturaleza acreedora → saldo = haber - debe
                # Costos/Gastos: naturaleza deudora → saldo = debe - haber
                if cuenta.naturaleza == 'A':
                    saldo = haber - debe
                else:
                    saldo = debe - haber

                if saldo != 0:
                    lineas.append({
                        'codigo': cuenta.codigo_sat,
                        'nombre': cuenta.nombre,
                        'nivel': cuenta.nivel,
                        'saldo': saldo,
                    })
            return lineas

        ingresos = _sumar_tipo('INGRESO')
        costos = _sumar_tipo('COSTO')
        gastos = _sumar_tipo('GASTO')

        total_ingresos = sum(l['saldo'] for l in ingresos)
        total_costos = sum(l['saldo'] for l in costos)
        total_gastos = sum(l['saldo'] for l in gastos)

        utilidad_bruta = total_ingresos - total_costos
        utilidad_operacion = utilidad_bruta - total_gastos

        # Otros ingresos/gastos (cuenta 402)
        otros = CuentaContable.objects.filter(
            codigo_sat__startswith='402', activa=True, nivel__lte=nivel_detalle
        ).order_by('codigo_sat')
        otros_lineas = []
        total_otros = Decimal('0.00')
        for cuenta in otros:
            datos = MovimientoContable.objects.filter(
                filtros, cuenta=cuenta
            ).aggregate(debe=Sum('debe'), haber=Sum('haber'))
            debe = datos['debe'] or Decimal('0.00')
            haber = datos['haber'] or Decimal('0.00')
            saldo = haber - debe if cuenta.naturaleza == 'A' else debe - haber
            if saldo != 0:
                otros_lineas.append({
                    'codigo': cuenta.codigo_sat,
                    'nombre': cuenta.nombre,
                    'nivel': cuenta.nivel,
                    'saldo': saldo,
                })
                total_otros += saldo

        utilidad_antes_impuestos = utilidad_operacion + total_otros

        return {
            'fecha_inicio': fecha_inicio,
            'fecha_fin': fecha_fin,
            'ingresos': ingresos,
            'total_ingresos': total_ingresos,
            'costos': costos,
            'total_costos': total_costos,
            'utilidad_bruta': utilidad_bruta,
            'gastos': gastos,
            'total_gastos': total_gastos,
            'utilidad_operacion': utilidad_operacion,
            'otros_ingresos': otros_lineas,
            'total_otros': total_otros,
            'utilidad_antes_impuestos': utilidad_antes_impuestos,
        }


class BalanceGeneralService:
    """
    Genera el Balance General (Estado de Situación Financiera).
    Estructura:
        ACTIVO (100)
        PASIVO (200)
        CAPITAL (300) + Resultado del ejercicio
        Validación: Activo = Pasivo + Capital
    """

    @classmethod
    def generar(
        cls,
        fecha_corte: date,
        unidad_negocio=None,
        nivel_detalle: int = 3,
    ) -> Dict:
        from contabilidad.models import CuentaContable, MovimientoContable

        filtros = Q(poliza__estado='APLICADA', poliza__fecha__lte=fecha_corte)
        if unidad_negocio:
            filtros &= Q(poliza__unidad_negocio=unidad_negocio)

        def _saldos_tipo(tipo: str) -> List[Dict]:
            cuentas = CuentaContable.objects.filter(
                tipo=tipo, activa=True, nivel__lte=nivel_detalle
            ).order_by('codigo_sat')

            lineas = []
            for cuenta in cuentas:
                datos = MovimientoContable.objects.filter(
                    filtros, cuenta=cuenta
                ).aggregate(debe=Sum('debe'), haber=Sum('haber'))

                debe = datos['debe'] or Decimal('0.00')
                haber = datos['haber'] or Decimal('0.00')

                if cuenta.naturaleza == 'D':
                    saldo = debe - haber
                else:
                    saldo = haber - debe

                if saldo != 0:
                    lineas.append({
                        'codigo': cuenta.codigo_sat,
                        'nombre': cuenta.nombre,
                        'nivel': cuenta.nivel,
                        'saldo': saldo,
                    })
            return lineas

        activos = _saldos_tipo('ACTIVO')
        pasivos = _saldos_tipo('PASIVO')
        capital = _saldos_tipo('CAPITAL')

        total_activo = sum(l['saldo'] for l in activos)
        total_pasivo = sum(l['saldo'] for l in pasivos)
        total_capital = sum(l['saldo'] for l in capital)

        # Resultado del ejercicio (ingresos - costos - gastos acumulados)
        resultado_ejercicio = cls._calcular_resultado_ejercicio(fecha_corte, filtros)

        total_capital_mas_resultado = total_capital + resultado_ejercicio
        total_pasivo_capital = total_pasivo + total_capital_mas_resultado

        # Cuadre
        diferencia = total_activo - total_pasivo_capital
        cuadra = abs(diferencia) < Decimal('0.02')

        return {
            'fecha_corte': fecha_corte,
            'activos': activos,
            'total_activo': total_activo,
            'pasivos': pasivos,
            'total_pasivo': total_pasivo,
            'capital': capital,
            'total_capital': total_capital,
            'resultado_ejercicio': resultado_ejercicio,
            'total_capital_mas_resultado': total_capital_mas_resultado,
            'total_pasivo_capital': total_pasivo_capital,
            'cuadra': cuadra,
            'diferencia': diferencia,
        }

    @classmethod
    def _calcular_resultado_ejercicio(cls, fecha_corte, filtros_base):
        """Calcula resultado del ejercicio: Ingresos - Costos - Gastos."""
        from contabilidad.models import CuentaContable, MovimientoContable

        # Inicio del ejercicio fiscal (1 de enero del año de corte)
        inicio_ejercicio = date(fecha_corte.year, 1, 1)
        filtros = filtros_base & Q(poliza__fecha__gte=inicio_ejercicio)

        resultado = Decimal('0.00')
        for tipo, signo in [('INGRESO', 1), ('COSTO', -1), ('GASTO', -1)]:
            cuentas = CuentaContable.objects.filter(tipo=tipo, activa=True)
            for cuenta in cuentas:
                datos = MovimientoContable.objects.filter(
                    filtros, cuenta=cuenta
                ).aggregate(debe=Sum('debe'), haber=Sum('haber'))
                debe = datos['debe'] or Decimal('0.00')
                haber = datos['haber'] or Decimal('0.00')
                saldo = (haber - debe) if cuenta.naturaleza == 'A' else (debe - haber)
                resultado += saldo * signo

        return resultado


class LibroMayorService:
    """
    Genera el Libro Mayor: movimientos de una cuenta contable en un período.
    Muestra saldo inicial, cada movimiento con saldo acumulado, y saldo final.
    """

    @classmethod
    def generar(
        cls,
        cuenta_id: int,
        fecha_inicio: date,
        fecha_fin: date,
        unidad_negocio=None,
    ) -> Dict:
        from contabilidad.models import CuentaContable, MovimientoContable

        cuenta = CuentaContable.objects.get(pk=cuenta_id)

        filtros_base = Q(poliza__estado='APLICADA')
        if unidad_negocio:
            filtros_base &= Q(poliza__unidad_negocio=unidad_negocio)

        # Saldo inicial (antes del período)
        datos_ini = MovimientoContable.objects.filter(
            filtros_base, cuenta=cuenta, poliza__fecha__lt=fecha_inicio
        ).aggregate(debe=Sum('debe'), haber=Sum('haber'))

        debe_ini = datos_ini['debe'] or Decimal('0.00')
        haber_ini = datos_ini['haber'] or Decimal('0.00')
        saldo_inicial = (debe_ini - haber_ini) if cuenta.naturaleza == 'D' else (haber_ini - debe_ini)

        # Movimientos del período
        movimientos = MovimientoContable.objects.filter(
            filtros_base, cuenta=cuenta,
            poliza__fecha__gte=fecha_inicio, poliza__fecha__lte=fecha_fin
        ).select_related('poliza').order_by('poliza__fecha', 'poliza__folio', 'id')

        lineas = []
        saldo_acum = saldo_inicial
        total_debe = Decimal('0.00')
        total_haber = Decimal('0.00')

        for mov in movimientos:
            if cuenta.naturaleza == 'D':
                saldo_acum = saldo_acum + mov.debe - mov.haber
            else:
                saldo_acum = saldo_acum - mov.debe + mov.haber

            total_debe += mov.debe
            total_haber += mov.haber

            lineas.append({
                'fecha': mov.poliza.fecha,
                'tipo_poliza': mov.poliza.get_tipo_display(),
                'folio': mov.poliza.folio,
                'concepto': mov.concepto or mov.poliza.concepto,
                'referencia': mov.referencia,
                'debe': mov.debe,
                'haber': mov.haber,
                'saldo': saldo_acum,
            })

        return {
            'cuenta': cuenta,
            'fecha_inicio': fecha_inicio,
            'fecha_fin': fecha_fin,
            'saldo_inicial': saldo_inicial,
            'movimientos': lineas,
            'total_debe': total_debe,
            'total_haber': total_haber,
            'saldo_final': saldo_acum,
        }


class AuxiliarCuentasService:
    """
    Genera el Auxiliar de Cuentas: resumen de saldos de todas las subcuentas
    de una cuenta padre, útil para desglosar rubros.
    """

    @classmethod
    def generar(
        cls,
        cuenta_padre_id: int,
        fecha_inicio: date,
        fecha_fin: date,
        unidad_negocio=None,
    ) -> Dict:
        from contabilidad.models import CuentaContable, MovimientoContable

        padre = CuentaContable.objects.get(pk=cuenta_padre_id)
        subcuentas = CuentaContable.objects.filter(
            codigo_sat__startswith=padre.codigo_sat,
            activa=True,
            permite_movimientos=True,
        ).order_by('codigo_sat')

        filtros_base = Q(poliza__estado='APLICADA')
        if unidad_negocio:
            filtros_base &= Q(poliza__unidad_negocio=unidad_negocio)

        lineas = []
        total_debe = Decimal('0.00')
        total_haber = Decimal('0.00')
        total_saldo = Decimal('0.00')

        for cuenta in subcuentas:
            # Saldo inicial
            ini = MovimientoContable.objects.filter(
                filtros_base, cuenta=cuenta, poliza__fecha__lt=fecha_inicio
            ).aggregate(debe=Sum('debe'), haber=Sum('haber'))
            d_ini = ini['debe'] or Decimal('0.00')
            h_ini = ini['haber'] or Decimal('0.00')
            saldo_ini = (d_ini - h_ini) if cuenta.naturaleza == 'D' else (h_ini - d_ini)

            # Movimientos del período
            per = MovimientoContable.objects.filter(
                filtros_base, cuenta=cuenta,
                poliza__fecha__gte=fecha_inicio, poliza__fecha__lte=fecha_fin
            ).aggregate(debe=Sum('debe'), haber=Sum('haber'))
            cargos = per['debe'] or Decimal('0.00')
            abonos = per['haber'] or Decimal('0.00')

            if cuenta.naturaleza == 'D':
                saldo_final = saldo_ini + cargos - abonos
            else:
                saldo_final = saldo_ini - cargos + abonos

            num_movs = MovimientoContable.objects.filter(
                filtros_base, cuenta=cuenta,
                poliza__fecha__gte=fecha_inicio, poliza__fecha__lte=fecha_fin
            ).count()

            if saldo_ini != 0 or cargos != 0 or abonos != 0:
                lineas.append({
                    'codigo': cuenta.codigo_sat,
                    'nombre': cuenta.nombre,
                    'saldo_inicial': saldo_ini,
                    'cargos': cargos,
                    'abonos': abonos,
                    'saldo_final': saldo_final,
                    'num_movimientos': num_movs,
                })
                total_debe += cargos
                total_haber += abonos
                total_saldo += saldo_final

        return {
            'cuenta_padre': padre,
            'fecha_inicio': fecha_inicio,
            'fecha_fin': fecha_fin,
            'lineas': lineas,
            'total_debe': total_debe,
            'total_haber': total_haber,
            'total_saldo': total_saldo,
        }
