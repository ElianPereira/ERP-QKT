"""
Reporte de costo de cumplimiento (Issue #403, fase 4): lo pagado en permisos,
licencias, constancias y contratos de servicio, por trámite y por tipo.
Solo lectura; todo monto en `Decimal`.
"""
from datetime import date
from decimal import Decimal
from typing import Dict

CERO = Decimal('0.00')


class CostoCumplimientoService:
    """Vigencias no canceladas cuyo inicio de vigencia cae en el periodo
    (misma regla que el costo anual del tablero)."""

    @classmethod
    def generar(cls, fecha_inicio: date, fecha_fin: date) -> Dict:
        from cumplimiento.models import Tramite, Vigencia

        vigencias = list(Vigencia.objects.exclude(estado='CANCELADA').filter(
            fecha_inicio__gte=fecha_inicio, fecha_inicio__lte=fecha_fin,
        ).select_related('tramite').order_by('tramite__nombre', 'fecha_inicio', 'id'))

        etiquetas = dict(Tramite.TIPO_CHOICES)
        por_tramite, por_tipo = {}, {}
        total = CERO
        for v in vigencias:
            total += v.costo
            fila = por_tramite.setdefault(v.tramite_id, {
                'nombre': v.tramite.nombre, 'tipo': etiquetas.get(v.tramite.tipo, v.tramite.tipo),
                'vigencias': 0, 'costo': CERO})
            fila['vigencias'] += 1
            fila['costo'] += v.costo
            tipo = por_tipo.setdefault(v.tramite.tipo, {
                'nombre': etiquetas.get(v.tramite.tipo, v.tramite.tipo), 'vigencias': 0, 'costo': CERO})
            tipo['vigencias'] += 1
            tipo['costo'] += v.costo

        return {
            'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin,
            'vigencias': vigencias,
            'tramites': sorted(por_tramite.values(), key=lambda f: (-f['costo'], f['nombre'])),
            'tipos': sorted(por_tipo.values(), key=lambda f: (-f['costo'], f['nombre'])),
            'total': total,
        }
