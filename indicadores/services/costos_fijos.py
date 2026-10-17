"""
Costos fijos mensuales (Issue #402)
===================================
**Costos fijos (CF)** = promedio de los últimos `meses_promedio` meses *cerrados*
(a partir de `inicio_datos`) de las cuentas de tipo GASTO (6xx) de pólizas
APLICADAS, mes por mes con `EstadoResultadosService`. Los costos 5xx no son
fijos: ya viven en el costo variable. Si el propietario captura
`costo_fijo_mensual_override`, ese manda y la fuente es «presupuestado».

**Nómina**: el signal de pólizas de nómina se retiró (2026-07-13), así que puede
no estar en 6xx. Si las pólizas del mes tienen movimiento en la cuenta de
`SUELDOS_SALARIOS`, no se suma nada; si no, se suma el total de recibos del mes.
Nunca las dos.

La cuenta 601.99 «Gastos por clasificar» entra a CF, pero su saldo se reporta
aparte como alerta de calidad de dato.
"""
from datetime import date
from typing import Dict, List, Optional

from contabilidad import categorias_gasto as cat
from reportes.services.contabilidad import EstadoResultadosService
from reportes.services.operacion import NominaPeriodoService

from ..models import ParametrosNegocio
from .base import D0, mes_anterior, mes_rango, q2

NOMINA = 'NOMINA'
GRUPOS = {
    cat.OPERACION: 'Operación', cat.ADMINISTRACION: 'Administración', cat.VENTA: 'Venta',
    cat.FINANCIERO: 'Financiero', NOMINA: 'Nómina', cat.POR_CLASIFICAR: 'Por clasificar',
    cat.OTROS: 'Otros',
}
_OPERACIONES_NOMINA = ('SUELDOS_SALARIOS', 'IMSS_PATRONAL')


def meses_cerrados(hasta: date, p: ParametrosNegocio) -> List[tuple]:
    """Los últimos `meses_promedio` meses completos al `hasta`, sin ir antes de `inicio_datos`."""
    meses, anio, mes = [], hasta.year, hasta.month
    while len(meses) < p.meses_promedio and (anio, mes) >= (p.inicio_datos.year, p.inicio_datos.month):
        inicio, fin = mes_rango(anio, mes)
        if inicio < p.inicio_datos:
            break
        if fin <= hasta:
            meses.append((inicio, fin))
        anio, mes = mes_anterior(anio, mes)
    return sorted(meses)


def _grupos_por_cuenta() -> Dict[str, str]:
    """`codigo_sat` de cada cuenta de gasto → grupo del catálogo de categorías."""
    from contabilidad.models import ConfiguracionContable

    grupo_de_operacion = {operacion: grupo for _, _, grupo, operacion in cat.CATALOGO}
    for grupo, operacion in cat.NO_DEDUCIBLE_POR_GRUPO.items():
        grupo_de_operacion[operacion] = grupo
    for operacion in _OPERACIONES_NOMINA:
        grupo_de_operacion[operacion] = NOMINA
    mapa = {}
    configuraciones = ConfiguracionContable.objects.filter(
        activa=True, operacion__in=list(grupo_de_operacion)).select_related('cuenta')
    for config in configuraciones:
        mapa[config.cuenta.codigo_sat] = grupo_de_operacion[config.operacion]
    return mapa


def nomina_en_polizas(inicio: date, fin: date) -> bool:
    """¿Hay movimiento en la cuenta de sueldos en pólizas aplicadas del rango?"""
    from contabilidad.models import ConfiguracionContable, MovimientoContable

    cuenta = ConfiguracionContable.obtener_cuenta('SUELDOS_SALARIOS')
    if cuenta is None:
        return False
    return MovimientoContable.objects.filter(
        cuenta=cuenta, poliza__estado='APLICADA', poliza__fecha__gte=inicio, poliza__fecha__lte=fin,
    ).exists()


class CostosFijosService:

    @classmethod
    def generar(cls, hasta: date, parametros: Optional[ParametrosNegocio] = None) -> Dict:
        p = parametros or ParametrosNegocio.vigente()
        mapa = _grupos_por_cuenta()
        meses, avisos = [], []
        for inicio, fin in meses_cerrados(hasta, p):
            estado = EstadoResultadosService.generar(inicio, fin, nivel_detalle=9)
            por_grupo = {g: D0 for g in GRUPOS}
            for linea in estado['gastos']:
                por_grupo[mapa.get(linea['codigo'], cat.OTROS)] += linea['saldo']
            en_polizas = nomina_en_polizas(inicio, fin)
            nomina_recibos = D0
            if not en_polizas:
                nomina_recibos = NominaPeriodoService.generar(inicio, fin)['total']
                por_grupo[NOMINA] += nomina_recibos
            meses.append({
                'inicio': inicio, 'fin': fin, 'etiqueta': f'{inicio.month:02d}/{inicio.year}',
                'por_grupo': {g: q2(v) for g, v in por_grupo.items()},
                'total': q2(sum(por_grupo.values(), D0)),
                'fuente_nomina': 'contabilidad' if en_polizas else 'recibos',
                'nomina_recibos': q2(nomina_recibos),
                'por_clasificar': q2(por_grupo[cat.POR_CLASIFICAR]),
            })

        promedio = q2(sum((m['total'] for m in meses), D0) / len(meses)) if meses else D0
        por_clasificar = q2(sum((m['por_clasificar'] for m in meses), D0))
        if p.costo_fijo_mensual_override is not None:
            fuente, cf = 'presupuestado', q2(p.costo_fijo_mensual_override)
        elif meses:
            fuente, cf = 'contabilidad', promedio
        else:
            fuente, cf = 'sin_datos', D0
            avisos.append('Aún no hay un mes cerrado con contabilidad desde el inicio de datos: '
                          'captura un costo fijo presupuestado en Parámetros.')
        if por_clasificar > 0:
            avisos.append(f'Hay ${por_clasificar:,.2f} en Gastos por clasificar (601.99) dentro de los costos fijos.')
        promedio_grupos = {
            g: q2(sum((m['por_grupo'][g] for m in meses), D0) / len(meses)) if meses else D0 for g in GRUPOS}
        return {
            'hasta': hasta, 'meses': meses, 'promedio': promedio, 'cf': cf, 'fuente': fuente,
            'por_grupo': promedio_grupos, 'grupos': GRUPOS, 'por_clasificar': por_clasificar,
            'avisos': avisos,
        }
