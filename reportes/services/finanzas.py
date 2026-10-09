"""
Reportes financieros (Issue #373, fase 5)
==========================================
Gastos por categoría, flujo de efectivo y conciliación bancaria. Solo lectura;
el flujo y la conciliación salen de pólizas APLICADAS, igual que el resto de
los reportes contables.
"""
from datetime import date, timedelta
from typing import Dict

from django.db.models import Sum

from .operacion import CERO


class GastosCategoriaService:
    """Compras por fecha de emisión, agrupadas por la categoría que define su cuenta de gasto."""

    @classmethod
    def generar(cls, fecha_inicio: date, fecha_fin: date) -> Dict:
        from comercial.models import CATEGORIAS_GASTO, Compra

        compras = list(Compra.objects.filter(
            fecha_emision__gte=fecha_inicio, fecha_emision__lte=fecha_fin,
        ).select_related('proveedor').order_by('fecha_emision', 'id'))
        etiquetas = dict(CATEGORIAS_GASTO)
        categorias = {}
        t = dict.fromkeys(('base', 'iva', 'retenciones', 'total', 'con_factura', 'sin_factura'), CERO)
        for c in compras:
            c.base = c.subtotal - c.descuento
            c.retenciones = c.ret_isr + c.ret_iva
            c.con_factura = bool(c.uuid)
            fila = categorias.setdefault(c.categoria, {
                'nombre': etiquetas.get(c.categoria, c.categoria), 'compras': 0,
                **dict.fromkeys(t, CERO),
            })
            fila['compras'] += 1
            for clave, valor in (('base', c.base), ('iva', c.iva), ('retenciones', c.retenciones),
                                 ('total', c.total)):
                fila[clave] += valor
                t[clave] += valor
            clave = 'con_factura' if c.con_factura else 'sin_factura'
            fila[clave] += c.total
            t[clave] += c.total
        filas = sorted(categorias.values(), key=lambda f: -f['total'])
        for f in filas:
            f['porcentaje'] = (f['total'] * 100 / t['total']).quantize(CERO) if t['total'] else CERO
        return {
            'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin,
            'categorias': filas, 'compras': compras, 'totales': t,
            'sin_clasificar': categorias.get('SIN_CLASIFICAR', {}).get('compras', 0),
        }


class FlujoEfectivoService:
    """
    Entradas y salidas de cada cuenta bancaria activa: saldo según libros al
    día anterior al periodo, movimientos de pólizas aplicadas agrupados por su
    origen (cobros, compras, nómina…) y por mes, y saldo final.
    """

    @classmethod
    def generar(cls, fecha_inicio: date, fecha_fin: date) -> Dict:
        from contabilidad.models import CuentaBancaria, MovimientoContable, Poliza

        etiquetas = dict(Poliza.ORIGEN_CHOICES)
        cuentas = []
        origenes, meses = {}, {}
        entradas = salidas = inicial = CERO
        for cb in CuentaBancaria.objects.filter(activa=True, cuenta_contable__isnull=False).order_by('nombre'):
            saldo_inicial = cb.saldo_a_fecha(fecha_inicio - timedelta(days=1))
            movs = MovimientoContable.objects.filter(
                cuenta=cb.cuenta_contable, poliza__estado='APLICADA',
                poliza__fecha__gte=fecha_inicio, poliza__fecha__lte=fecha_fin,
            )
            ent = sal = CERO
            for fila in movs.values('poliza__origen').annotate(debe=Sum('debe'), haber=Sum('haber')):
                o = origenes.setdefault(fila['poliza__origen'], {
                    'nombre': etiquetas.get(fila['poliza__origen'], fila['poliza__origen']),
                    'entradas': CERO, 'salidas': CERO,
                })
                o['entradas'] += fila['debe'] or CERO
                o['salidas'] += fila['haber'] or CERO
                ent += fila['debe'] or CERO
                sal += fila['haber'] or CERO
            for fila in movs.values('poliza__fecha__year', 'poliza__fecha__month').annotate(
                    debe=Sum('debe'), haber=Sum('haber')):
                clave = (fila['poliza__fecha__year'], fila['poliza__fecha__month'])
                m = meses.setdefault(clave, {'mes': date(*clave, 1), 'entradas': CERO, 'salidas': CERO})
                m['entradas'] += fila['debe'] or CERO
                m['salidas'] += fila['haber'] or CERO
            cuentas.append({
                'nombre': str(cb), 'saldo_inicial': saldo_inicial, 'entradas': ent, 'salidas': sal,
                'saldo_final': saldo_inicial + ent - sal,
            })
            inicial += saldo_inicial
            entradas += ent
            salidas += sal

        for o in origenes.values():
            o['neto'] = o['entradas'] - o['salidas']
        acumulado = inicial
        filas_mes = []
        for clave in sorted(meses):
            m = meses[clave]
            m['neto'] = m['entradas'] - m['salidas']
            acumulado += m['neto']
            m['saldo'] = acumulado
            filas_mes.append(m)
        return {
            'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin,
            'cuentas': cuentas,
            'origenes': sorted(origenes.values(), key=lambda o: -(o['entradas'] + o['salidas'])),
            'meses': filas_mes,
            'saldo_inicial': inicial, 'entradas': entradas, 'salidas': salidas,
            'neto': entradas - salidas, 'saldo_final': inicial + entradas - salidas,
        }


class ConciliacionBancariaService:
    """Conciliaciones de los meses del periodo y los movimientos del banco aún sin asiento."""

    @classmethod
    def generar(cls, fecha_inicio: date, fecha_fin: date) -> Dict:
        from contabilidad.models import ConciliacionBancaria, MovimientoEstadoCuenta

        desde, hasta = (fecha_inicio.year, fecha_inicio.month), (fecha_fin.year, fecha_fin.month)
        conciliaciones = [
            c for c in ConciliacionBancaria.objects.filter(
                anio__gte=fecha_inicio.year, anio__lte=fecha_fin.year,
            ).select_related('cuenta_bancaria').order_by('anio', 'mes', 'cuenta_bancaria__nombre')
            if desde <= (c.anio, c.mes) <= hasta
        ]
        pendientes = list(MovimientoEstadoCuenta.objects.filter(
            movimiento_contable__isnull=True, fecha__gte=fecha_inicio, fecha__lte=fecha_fin,
        ).select_related('estado_cuenta__cuenta_bancaria').order_by('fecha', 'id'))
        return {
            'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin,
            'conciliaciones': conciliaciones,
            'conciliadas': sum(1 for c in conciliaciones if c.estado == 'CONCILIADA'),
            'con_diferencia': sum(1 for c in conciliaciones if c.diferencia),
            'pendientes': pendientes,
            'pendientes_cargos': sum((m.cargo for m in pendientes), CERO),
            'pendientes_abonos': sum((m.abono for m in pendientes), CERO),
        }
