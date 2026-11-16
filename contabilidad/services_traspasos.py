"""
Traspasos entre cuentas propias: reclasificación del histórico.

Antes de la cuenta puente (TRASPASOS_EN_TRANSITO), un fondeo entre las dos cuentas BBVA quedaba como retiro o
aportación del dueño, o sin clasificar. Aquí se reapunta esa línea a la puente sin cancelar ni tocar importes
(mismo criterio que `reclasificar_compra`): la póliza conserva folio y cuadre.
"""
from django.db import transaction

from .models import ConfiguracionContable, MovimientoEstadoCuenta, SaldoApertura
from .services_reglas_banco import _poliza_del_movimiento, es_traspaso_propio

OPERACIONES_A_REAPUNTAR = ('RETIROS_DUENO', 'APORTACIONES_DUENO', 'PARTIDAS_POR_IDENTIFICAR')


def cuentas_a_reapuntar():
    return {c.pk for c in (ConfiguracionContable.obtener_cuenta(o) for o in OPERACIONES_A_REAPUNTAR) if c}


def reclasificar_traspasos(desde=None, aplicar=False):
    """
    Reapunta a la cuenta puente la contrapartida de los movimientos del banco que son traspasos entre
    cuentas propias y hoy están en retiros/aportaciones del dueño o partidas por identificar.
    Sin `aplicar` solo informa. Devuelve {'reapuntados': [(mov, cuenta_anterior)], 'sin_resolver': [(mov, motivo)]}.
    """
    resultado = {'reapuntados': [], 'sin_resolver': []}
    puente = ConfiguracionContable.obtener_cuenta('TRASPASOS_EN_TRANSITO')
    if puente is None:
        resultado['sin_resolver'].append((None, 'Falta la configuración contable TRASPASOS_EN_TRANSITO.'))
        return resultado
    origen = cuentas_a_reapuntar()

    movimientos = (MovimientoEstadoCuenta.objects.filter(movimiento_contable__isnull=False)
                   .select_related('estado_cuenta__cuenta_bancaria__cuenta_contable').order_by('fecha', 'pk'))
    if desde:
        movimientos = movimientos.filter(fecha__gte=desde)
    for mov in movimientos:
        if es_traspaso_propio(mov) is None:
            continue
        reapuntar_a_puente(mov, puente, origen, aplicar, resultado)
    return resultado


def reapuntar_a_puente(mov, puente, origen, aplicar, resultado=None):
    """
    Reapunta a la puente la contrapartida de la póliza BANCO del movimiento si es un asiento simple de dos líneas
    cuya contrapartida está en `origen` (pks de cuentas) y el periodo no está cerrado por `SaldoApertura`. Sin
    cancelar ni tocar importes. Devuelve True si reapuntó (o lo haría, sin `aplicar`).
    """
    resultado = resultado if resultado is not None else {'reapuntados': [], 'sin_resolver': []}
    cuenta_bancaria = mov.estado_cuenta.cuenta_bancaria
    poliza = _poliza_del_movimiento(mov)
    if poliza is None or poliza.estado != 'APLICADA':
        return False
    lineas = list(poliza.movimientos.exclude(cuenta_id=cuenta_bancaria.cuenta_contable_id))
    if len(lineas) != 1 or lineas[0].cuenta_id == puente.pk:
        return False  # ya está en la puente, o no es un asiento simple de dos líneas
    linea = lineas[0]
    if linea.cuenta_id not in origen:
        resultado['sin_resolver'].append((mov, f'Contrapartida {linea.cuenta.codigo_sat}: no es retiro/aportación del dueño.'))
        return False
    if SaldoApertura.objects.filter(cuenta_bancaria=cuenta_bancaria, aplicado=True,
                                    fecha_corte__gte=poliza.fecha).exists():
        resultado['sin_resolver'].append((mov, f'La póliza {poliza.folio} es de un periodo ya cerrado.'))
        return False
    resultado['reapuntados'].append((mov, linea.cuenta))
    if aplicar:
        with transaction.atomic():
            linea.cuenta = puente
            linea.save(update_fields=['cuenta'])
    return True
