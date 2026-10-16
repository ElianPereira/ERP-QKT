"""
Embudo comercial y marketing (Issue #402)
=========================================
- **Solicitud** = cotización creada en el periodo (`created_at`), por tipo de
  servicio y por `como_nos_encontro` (vacío = «Sin dato», p. ej. las creadas a
  mano en el admin).
- **Conversión** = las que llegaron a `ESTADOS_VENTA_REAL`; también la tasa de
  expiración y de cancelación. Es una cohorte: se mide sobre lo creado en el periodo.
- No hay fecha de confirmación guardada: «días a confirmar» y «anticipación» se
  miden contra el **primer pago** (que es lo que confirma la venta).
- **Marketing**: gasto de pólizas APLICADAS en las cuentas de las operaciones del
  grupo VENTA (publicidad, fotografía y su subcuenta no deducible). No hay dato
  para atribuir gasto por canal: se muestra solo agregado.
  **CAC** = gasto / ventas nuevas; **costo por solicitud** = gasto / solicitudes;
  **ROAS** = venta neta nueva / gasto.
- WhatsApp: se reutiliza `metricas_periodo(desde, hasta)['conversion']`, sin recalcular.
"""
from datetime import date
from typing import Dict

from .base import D0, dividir, porcentaje, q2, venta_neta

SIN_DATO = 'Sin dato'


def gasto_marketing(desde: date, hasta: date):
    """Gasto de publicidad y fotografía del periodo según pólizas aplicadas."""
    from django.db.models import Sum

    from contabilidad import categorias_gasto as cat
    from contabilidad.models import ConfiguracionContable, MovimientoContable

    operaciones = {op for _, _, grupo, op in cat.CATALOGO if grupo == cat.VENTA}
    operaciones.add(cat.NO_DEDUCIBLE_POR_GRUPO[cat.VENTA])
    cuentas = ConfiguracionContable.objects.filter(
        activa=True, operacion__in=operaciones).values_list('cuenta_id', flat=True)
    datos = MovimientoContable.objects.filter(
        cuenta_id__in=list(cuentas), poliza__estado='APLICADA',
        poliza__fecha__gte=desde, poliza__fecha__lte=hasta).aggregate(debe=Sum('debe'), haber=Sum('haber'))
    return q2((datos['debe'] or D0) - (datos['haber'] or D0))


def _fila(nombre: str) -> dict:
    return {'nombre': nombre, 'solicitudes': 0, 'ventas': 0, 'expiradas': 0, 'canceladas': 0, 'venta_neta': D0}


def _cerrar(fila: dict) -> dict:
    s = fila['solicitudes']
    fila['tasa_conversion'] = porcentaje(fila['ventas'], s)
    fila['tasa_expiracion'] = porcentaje(fila['expiradas'], s)
    fila['tasa_cancelacion'] = porcentaje(fila['canceladas'], s)
    fila['venta_neta'] = q2(fila['venta_neta'])
    return fila


class EmbudoComercialService:

    @classmethod
    def generar(cls, desde: date, hasta: date, whatsapp: bool = True) -> Dict:
        from comercial.models import Cotizacion
        from comercial.views import ESTADOS_VENTA_REAL

        etiquetas = dict(Cotizacion.TIPO_SERVICIO_CHOICES)
        cotizaciones = Cotizacion.objects.filter(
            created_at__date__gte=desde, created_at__date__lte=hasta).prefetch_related('pagos')
        total = _fila('Total')
        por_servicio, por_canal = {}, {}
        dias_confirmar, anticipaciones = [], []
        for cot in cotizaciones:
            filas = (
                total,
                por_servicio.setdefault(cot.tipo_servicio, _fila(etiquetas.get(cot.tipo_servicio, cot.tipo_servicio))),
                por_canal.setdefault(cot.como_nos_encontro or SIN_DATO, _fila(cot.como_nos_encontro or SIN_DATO)),
            )
            for fila in filas:
                fila['solicitudes'] += 1
            if cot.estado in ESTADOS_VENTA_REAL:
                for fila in filas:
                    fila['ventas'] += 1
                    fila['venta_neta'] += venta_neta(cot)
                pagos = sorted(pg.fecha_pago for pg in cot.pagos.all() if pg.tipo != 'REEMBOLSO')
                if pagos:
                    dias_confirmar.append((pagos[0] - cot.created_at.date()).days)
                    anticipaciones.append((cot.fecha_evento - pagos[0]).days)
            elif cot.estado == 'EXPIRADA':
                for fila in filas:
                    fila['expiradas'] += 1
            elif cot.estado == 'CANCELADA':
                for fila in filas:
                    fila['canceladas'] += 1

        gasto = gasto_marketing(desde, hasta)
        ventas = total['ventas']
        resultado = {
            'desde': desde, 'hasta': hasta, 'total': _cerrar(total),
            'por_servicio': sorted((_cerrar(f) for f in por_servicio.values()), key=lambda f: -f['solicitudes']),
            'por_canal': sorted((_cerrar(f) for f in por_canal.values()), key=lambda f: -f['solicitudes']),
            'dias_a_confirmar': dividir(sum(dias_confirmar), len(dias_confirmar)) if dias_confirmar else None,
            'anticipacion': dividir(sum(anticipaciones), len(anticipaciones)) if anticipaciones else None,
            'gasto_marketing': gasto,
            'costo_por_solicitud': dividir(gasto, total['solicitudes']),
            'cac': dividir(gasto, ventas),
            'roas': dividir(total['venta_neta'], gasto),
            'whatsapp': None,
        }
        if whatsapp:
            from comunicacion.services_tablero import metricas_periodo
            resultado['whatsapp'] = metricas_periodo(desde, hasta)['conversion']
        return resultado


