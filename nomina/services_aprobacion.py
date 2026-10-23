"""
Aprobación semanal de la nómina por Dirección (Issue #431, fase 4).

Cada viernes se registra el total a pagar de la semana (recibos no cancelados
cuyo periodo empieza entre el lunes y el domingo). Aprobar solo deja constancia
de quién y cuándo: no genera póliza, no mueve dinero y no cambia los recibos.
"""
import logging
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from .models import AprobacionNomina

logger = logging.getLogger(__name__)

CENTAVOS = Decimal('0.01')


def semana_de(hoy):
    """(lunes, domingo) de la semana de `hoy`."""
    lunes = hoy - timedelta(days=hoy.weekday())
    return lunes, lunes + timedelta(days=6)


def registrar_semana(hoy=None):
    """Crea (o devuelve) la aprobación de la semana con el total actual de sus recibos.
    Una semana ya avisada o aprobada conserva su total. Devuelve None si no hay recibos."""
    from reportes.services.operacion import NominaPeriodoService

    hoy = hoy or timezone.localdate()
    lunes, domingo = semana_de(hoy)
    existente = AprobacionNomina.objects.filter(semana_inicio=lunes).first()
    if existente is not None and (existente.notificada_en or existente.aprobada_en):
        return existente
    datos = NominaPeriodoService.generar(lunes, domingo)
    if not datos['recibos']:
        return existente
    total = Decimal(datos['total']).quantize(CENTAVOS, rounding=ROUND_HALF_UP)
    aprobacion, _ = AprobacionNomina.objects.update_or_create(
        semana_inicio=lunes, defaults={'semana_fin': domingo, 'total': total, 'recibos': len(datos['recibos'])})
    return aprobacion


def aprobar(aprobacion, *, nombre='', telefono='', usuario=None) -> bool:
    """Deja constancia de la aprobación. Un solo uso: devuelve False si ya estaba aprobada."""
    usuario = usuario if getattr(usuario, 'pk', None) else None
    with transaction.atomic():
        filas = AprobacionNomina.objects.filter(pk=aprobacion.pk, aprobada_en__isnull=True).update(
            aprobada_en=timezone.now(), aprobada_por_nombre=(nombre or '')[:120],
            aprobada_por_telefono=(telefono or '')[:20], aprobada_por=usuario, updated_at=timezone.now())
    if filas:
        aprobacion.refresh_from_db()
    return bool(filas)
