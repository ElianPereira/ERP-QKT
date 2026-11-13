"""Conciliación mensual del gasto publicitario contra el banco (Issue #468, fase 2).

Solo LECTURA de contabilidad. Criterio:

* Gasto esperado = suma del mes de `GastoPublicitario` de la plataforma (Meta y Google lo reportan sin IVA).
  Meta factura con IVA 0 % (Meta Platforms Ireland: el informe de facturación trae «VAT Rate: 0%»), así que el
  cargo a la tarjeta es igual al gasto; Google sí lleva IVA (`core_erp.impuestos.con_iva`, aún sin verificar
  con una factura real).
* Cargo bancario = cargos del mes de `MovimientoEstadoCuenta` cuyo concepto
  menciona Meta/Facebook y cuya póliza APLICADA ya pasó por la cuenta de la
  operación `GASTO_PUBLICIDAD` (o una subcuenta). Los cargos de Meta que aún no
  están clasificados no cuentan, pero se reportan en `sin_clasificar`. A los cargos del banco se suman los
  pagos hechos con una tarjeta que no es de la cuenta bancaria del ERP (`registrar_pauta_externa`): pólizas
  APLICADAS cuyo concepto empieza con `PREFIJOS_EXTERNOS[plataforma]` y que cargan la cuenta de publicidad.
* Meta no cobra por día: cobra al llegar a un umbral de gasto o a fin de mes,
  así que un mes puede traer un cargo de más o de menos que se corrige en el
  siguiente. Por eso se tolera el MAYOR entre un porcentaje del esperado y un
  piso en pesos (ambos parámetros; ver settings) y solo se concilian meses ya
  cerrados. Gasto y cargos en cero, o sin estado de cuenta del mes, no se evalúan.
"""
import calendar
import re
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum

from comunicacion.services_pendientes import registrar_pendiente
from contabilidad.models import (
    ConfiguracionContable,
    MovimientoContable,
    MovimientoEstadoCuenta,
    normalizar_texto_banco,
)
from core_erp import impuestos

from ..models import GastoPublicitario

PATRON_META = re.compile(r'\b(FACEBOOK|FACEBK|META)\b')
# «GOOGLE *ADS123», «GOOGLE ADS», «GOOGLEADS»; no atrapa Workspace ni Cloud.
PATRON_GOOGLE = re.compile(r'GOOGLE\s*\*?\s*ADS')
PATRONES = {'META': PATRON_META, 'GOOGLE': PATRON_GOOGLE}
NOMBRES = {'META': 'Meta', 'GOOGLE': 'Google Ads'}
# Meta factura con IVA 0 % (informe de facturación de Meta Platforms Ireland); Google, con IVA.
PLATAFORMAS_CON_IVA = {'META': False, 'GOOGLE': True}
# Concepto de las pólizas de pauta pagada con una tarjeta ajena a la cuenta bancaria del ERP.
PREFIJOS_EXTERNOS = {'META': 'Pauta Meta pagada fuera de BBVA', 'GOOGLE': 'Pauta Google Ads pagada fuera de BBVA'}


def _decimal(valor) -> Decimal:
    return Decimal(str(valor))


def cargos_bancarios(anio: int, mes: int, plataforma: str = 'META') -> dict:
    patron = PATRONES[plataforma]
    inicio, fin = date(anio, mes, 1), date(anio, mes, calendar.monthrange(anio, mes)[1])
    config = ConfiguracionContable.objects.filter(operacion='GASTO_PUBLICIDAD', activa=True).select_related('cuenta').first()
    total, cantidad, sin_clasificar = Decimal('0.00'), 0, 0
    movimientos = MovimientoEstadoCuenta.objects.filter(fecha__range=(inicio, fin), cargo__gt=0) \
        .select_related('movimiento_contable__poliza')
    for mov in movimientos:
        if not patron.search(normalizar_texto_banco(mov.descripcion)):
            continue
        poliza = mov.movimiento_contable.poliza if mov.movimiento_contable_id else None
        clasificado = bool(config and poliza and poliza.estado == 'APLICADA' and MovimientoContable.objects.filter(
            poliza=poliza, cuenta__codigo_sat__startswith=config.cuenta.codigo_sat).exists())
        if clasificado:
            total += mov.cargo
            cantidad += 1
        else:
            sin_clasificar += 1
    return {'total': impuestos.centavos(total), 'cantidad': cantidad, 'sin_clasificar': sin_clasificar,
            'hay_movimientos': MovimientoEstadoCuenta.objects.filter(fecha__range=(inicio, fin)).exists()}


def pagos_externos(anio: int, mes: int, plataforma: str = 'META') -> Decimal:
    """Pauta del mes pagada con una tarjeta que no es de la cuenta bancaria del ERP: lo que cargan a la cuenta
    de publicidad no deducible (`NO_DEDUCIBLE_VENTA`, 601.05.03: sin CFDI) las pólizas APLICADAS con el concepto
    de `PREFIJOS_EXTERNOS`."""
    config = ConfiguracionContable.objects.filter(operacion='NO_DEDUCIBLE_VENTA', activa=True).select_related('cuenta').first()
    if not config:
        return Decimal('0.00')
    inicio, fin = date(anio, mes, 1), date(anio, mes, calendar.monthrange(anio, mes)[1])
    total = MovimientoContable.objects.filter(
        poliza__estado='APLICADA', poliza__fecha__range=(inicio, fin),
        poliza__concepto__startswith=PREFIJOS_EXTERNOS[plataforma],
        cuenta__codigo_sat__startswith=config.cuenta.codigo_sat,
    ).aggregate(t=Sum('debe'))['t'] or Decimal('0.00')
    return impuestos.centavos(total)


def conciliar_mes(anio: int, mes: int, tolerancia_pct=None, tolerancia_minima=None, plataforma: str = 'META') -> dict:
    """Compara gasto del mes (con IVA) contra cargos bancarios clasificados y deja
    `estado_conciliacion` en las filas del mes. Con diferencia fuera de tolerancia
    abre un pendiente para DIRECCION (uno por mes)."""
    pct = _decimal(settings.ERP_MARKETING_TOLERANCIA_PCT if tolerancia_pct is None else tolerancia_pct)
    piso = _decimal(settings.ERP_MARKETING_TOLERANCIA_MINIMA if tolerancia_minima is None else tolerancia_minima)
    filas = GastoPublicitario.objects.filter(fecha__year=anio, fecha__month=mes, campana__plataforma=plataforma)
    gasto = impuestos.centavos(filas.aggregate(t=Sum('monto'))['t'] or 0)
    esperado = impuestos.con_iva(gasto) if PLATAFORMAS_CON_IVA[plataforma] else gasto
    banco = cargos_bancarios(anio, mes, plataforma)
    externos = pagos_externos(anio, mes, plataforma)
    pagado = impuestos.centavos(banco['total'] + externos)
    resultado = {'anio': anio, 'mes': mes, 'gasto': gasto, 'esperado': esperado, 'banco': banco['total'],
                 'externos': externos, 'sin_clasificar': banco['sin_clasificar'], 'estado': GastoPublicitario.PENDIENTE,
                 'diferencia': impuestos.centavos(pagado - esperado)}
    if (gasto == 0 and pagado == 0) or not banco['hay_movimientos']:
        return resultado
    tolerancia = max(piso, impuestos.centavos(esperado * pct))
    cuadra = abs(resultado['diferencia']) <= tolerancia
    resultado['estado'] = GastoPublicitario.CONCILIADO if cuadra else GastoPublicitario.DIFERENCIA
    filas.update(estado_conciliacion=resultado['estado'])
    if not cuadra:
        registrar_pendiente(
            titulo=f'Pauta de {NOMBRES[plataforma]} no cuadra con el banco ({mes:02d}/{anio})', rol='DIRECCION', tipo='CRON',
            detalle=(f'Gasto de {NOMBRES[plataforma]}{" con IVA" if PLATAFORMAS_CON_IVA[plataforma] else ""}: ${esperado}\n'
                     f'Cargos del banco: ${banco["total"]}\nPagados con otra tarjeta: ${externos}\n'
                     f'Diferencia: ${resultado["diferencia"]}\nCargos de {NOMBRES[plataforma]} sin clasificar: {banco["sin_clasificar"]}'),
            clave_idempotencia=(f'marketing_conciliacion:{anio}-{mes:02d}' if plataforma == 'META'
                                else f'marketing_conciliacion:{plataforma.lower()}:{anio}-{mes:02d}'))
    return resultado
