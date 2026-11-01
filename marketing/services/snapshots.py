"""Instantánea diaria de indicadores (Issue #468, fase 3)."""
from datetime import date, timedelta
from typing import Optional

from django.db import transaction
from django.utils import timezone

from ..models import ParametroMarketing, SnapshotKPI
from . import kpis

CAMPOS_SNAPSHOT = ('gasto', 'prospectos', 'cotizaciones', 'ganados', 'contratado', 'cobrado', 'cpl',
                   'costo_cotizacion', 'tasa_cierre', 'cac', 'roas_cobrado', 'roas_contratado')


def generar(fecha: Optional[date] = None, parametros: Optional[ParametroMarketing] = None) -> dict:
    """Guarda las cifras de la ventana que termina en `fecha`: total, un renglón por servicio y uno por
    campaña. Idempotente por (fecha, servicio, campaña): correrlo otra vez actualiza, no duplica."""
    fecha = fecha or timezone.localdate()
    p = parametros or ParametroMarketing.vigente()
    datos = kpis.calcular(fecha - timedelta(days=p.ventana_dias - 1), fecha, parametros=p)
    topes = kpis.topes_cac(fecha, p)

    def guardar(grupo, tipo_servicio, campana_id, tope=None):
        valores = {c: grupo[c] for c in CAMPOS_SNAPSHOT}
        SnapshotKPI.objects.update_or_create(
            fecha=fecha, tipo_servicio=tipo_servicio, campana_id=campana_id,
            defaults={**valores, 'ventana_dias': p.ventana_dias, 'cac_tope': tope})

    with transaction.atomic():
        guardar(datos['total'], '', None)
        for tipo, grupo in datos['por_servicio'].items():
            guardar(grupo, tipo, None, topes.get(tipo, {}).get('tope'))
        for grupo in datos['por_campana']:
            guardar(grupo, grupo['tipo_servicio'], grupo['campana_id'],
                    topes.get(grupo['tipo_servicio'], {}).get('tope'))
    return {'fecha': fecha, 'filas': 1 + len(datos['por_servicio']) + len(datos['por_campana'])}
