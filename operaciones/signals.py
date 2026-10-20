"""
Genera la tarea de preparación (turnover) en cuanto una Cotizacion se
confirma — mismo patrón que `comunicacion/signals.py`: post_save reentrante,
protegido por la idempotencia de `generar_tarea_turnover` (get_or_create), no
por el `created` del signal.
"""
import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(post_save, sender='comercial.Cotizacion')
def generar_tarea_turnover_al_confirmar(sender, instance, created, **kwargs):
    cot = instance
    if cot.estado != 'CONFIRMADA':
        return

    def _generar():
        from .services import enviar_aviso_horario, generar_tarea_turnover
        tarea = generar_tarea_turnover(cot)
        if tarea is None:
            return
        # El aviso de horario especial no espera al cron: se manda en cuanto
        # se sabe, para que el colaborador pueda organizarse con días de
        # anticipación si hace falta.
        enviar_aviso_horario(tarea)

    transaction.on_commit(_generar)
