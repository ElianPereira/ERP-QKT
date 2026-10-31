"""Retención de prospectos que no compraron: 1 año (Issue #468, fase 1).

Pasado el plazo se borran los datos personales del prospecto perdido; la fila
se conserva con lo que sirve para medir (fuente, anuncio, fechas, motivo). Es
la única excepción autorizada a la soft-deactivation en este módulo.
"""
from datetime import timedelta

from django.utils import timezone

from ..models import Lead, LeadEvento
from .pipeline import _evento

DIAS_RETENCION = 365


def anonimizar_vencidos(ahora=None) -> int:
    ahora = ahora or timezone.now()
    ganados = LeadEvento.objects.filter(etapa_nueva=Lead.GANADO).values('lead_id')
    vencidos = Lead.objects.filter(
        etapa=Lead.PERDIDO, anonimizado_en__isnull=True,
        fecha_ultima_interaccion__lt=ahora - timedelta(days=DIAS_RETENCION),
    ).exclude(pk__in=ganados)
    hechos = 0
    for lead in vencidos:
        lead.nombre = ''
        lead.telefono = ''
        lead.anuncio_clid = ''
        lead.conversacion = None
        lead.cliente = None
        lead.anonimizado_en = ahora
        lead.save(update_fields=['nombre', 'telefono', 'anuncio_clid', 'conversacion', 'cliente',
                                 'anonimizado_en', 'updated_at'])
        _evento(lead, 'ANONIMIZADO')
        hechos += 1
    return hechos
