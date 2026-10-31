"""Barrido diario de prospectos (Issue #468, fase 1): red de seguridad de las
señales, pérdidas por silencio, motivos y retención."""
import logging
from datetime import timedelta

from django.db.models import Max, Min, Q
from django.utils import timezone

from comercial.models import Cotizacion
from comunicacion.models import ConversacionWhatsApp

from ..models import Lead
from . import motivos, pipeline, retencion

logger = logging.getLogger(__name__)

DIAS_ACTIVIDAD = 2


def _contactos_recientes(desde):
    """Teléfonos con mensajes del cliente y cotizaciones desde `desde`."""
    convs = ConversacionWhatsApp.objects.filter(mensajes__direccion='ENTRADA', mensajes__created_at__gte=desde) \
        .annotate(primero=Min('mensajes__created_at', filter=Q(mensajes__direccion='ENTRADA',
                                                                mensajes__created_at__gte=desde)),
                  ultimo=Max('mensajes__created_at', filter=Q(mensajes__direccion='ENTRADA'))).distinct()
    for conv in convs:
        yield {'telefono': conv.telefono, 'nombre': conv.nombre, 'conversacion': conv,
               'primer_contacto': conv.primero, 'momento': conv.ultimo}
    for cot in Cotizacion.objects.filter(es_historica=False, created_at__gte=desde).select_related('cliente'):
        yield {'telefono': cot.cliente.telefono, 'cliente': cot.cliente, 'cotizacion': cot,
               'primer_contacto': cot.created_at, 'momento': cot.created_at}


def barrer(desde=None, ahora=None) -> dict:
    """Sin `desde`: prospectos abiertos y actividad de los últimos 2 días.
    Con `desde`: reconstruye el histórico desde esa fecha."""
    ahora = ahora or timezone.now()
    reconstruir = desde is not None
    inicio = desde or ahora - timedelta(days=DIAS_ACTIVIDAD)
    antes = Lead.objects.count()
    for contacto in _contactos_recientes(inicio):
        try:
            pipeline.registrar_contacto(ahora=ahora, reconstruir=reconstruir, **contacto)
        except Exception:
            logger.exception('Marketing: no se pudo registrar un contacto en el barrido')
    if not reconstruir:
        # Cotizaciones que cambiaron de estado sin pasar por una señal (cron con update()).
        for cot in Cotizacion.objects.filter(es_historica=False, updated_at__gte=inicio).select_related('cliente'):
            pipeline.sincronizar_existente(telefono=cot.cliente.telefono, cliente=cot.cliente, ahora=ahora)
    for lead in Lead.objects.exclude(etapa__in=Lead.CERRADAS):
        try:
            pipeline.sincronizar(lead, ahora)
        except Exception:
            logger.exception('Marketing: no se pudo sincronizar el prospecto %s', lead.pk)
    for lead in Lead.objects.filter(etapa=Lead.GANADO, etapa_cambiada_en__gte=ahora - timedelta(days=180)):
        pipeline.sincronizar(lead, ahora)
    return {
        'creados': Lead.objects.count() - antes,
        'abiertos': Lead.objects.exclude(etapa__in=Lead.CERRADAS).count(),
        'motivos': motivos.clasificar_pendientes(),
        'anonimizados': retencion.anonimizar_vencidos(ahora),
    }
