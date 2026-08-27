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
        from .services import enviar_aviso_horario, enviar_resumen_propietario, generar_tarea_turnover
        tarea = generar_tarea_turnover(cot)
        if tarea is None:
            return
        # El aviso de horario especial no espera al cron: se manda en cuanto
        # se sabe, para que el colaborador pueda organizarse con días de
        # anticipación si hace falta.
        enviar_aviso_horario(tarea)
        # Si el corte de "la noche anterior" ya pasó cuando se generó la
        # tarea (confirmación de último momento), el resumen al propietario
        # también sale de inmediato en vez de esperar al cron de la noche.
        from django.utils import timezone
        ahora = timezone.localtime().replace(tzinfo=None)
        if ahora >= tarea.hora_envio_resumen():
            enviar_resumen_propietario(tarea.fecha)

    transaction.on_commit(_generar)
