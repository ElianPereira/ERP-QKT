import logging

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

logger = logging.getLogger(__name__)


@receiver(post_save, sender='comercial.Cotizacion')
def emitir_contrato_al_confirmar(sender, instance, **kwargs):
    """
    En cuanto una cotización queda CONFIRMADA se emite su contrato y se le
    avisa al cliente que ya puede firmarlo en el portal. Antes alguien tenía
    que entrar al admin a darle «Generar contrato»: el cliente pagaba y se
    quedaba sin nada que firmar.

    `CONTRATO_AUTOMATICO=False` en Railway lo apaga sin desplegar.
    """
    from .services import ContratoService

    if not getattr(settings, 'CONTRATO_AUTOMATICO', True):
        return
    cot = instance
    if cot.estado != 'CONFIRMADA' or cot.tipo_servicio not in ContratoService.TIPOS:
        return
    # Una reservación vieja que se vuelve a guardar no debe recibir de pronto
    # un contrato que ya resolvió por fuera.
    if not cot.fecha_evento or cot.fecha_evento < timezone.localdate():
        return
    if cot.contratos.exists():
        return
    transaction.on_commit(lambda: _emitir_contrato_automatico(cot.pk))


def _emitir_contrato_automatico(cotizacion_pk):
    from comunicacion.services_notificaciones import (
        alertar_equipo_contrato_fallido,
        notificar_contrato_listo,
    )

    from .models import Cotizacion
    from .services import emitir_contrato

    try:
        with transaction.atomic():
            # El candado evita dos contratos si dos guardados de la misma
            # cotización disparan el signal a la vez.
            cot = Cotizacion.objects.select_for_update().get(pk=cotizacion_pk)
            if cot.estado != 'CONFIRMADA' or cot.contratos.exists():
                return
            contrato, _ = emitir_contrato(cot)
    except Exception:
        logger.exception("No se pudo emitir el contrato automático de COT-%s.", cotizacion_pk)
        cot = Cotizacion.objects.filter(pk=cotizacion_pk).first()
        if cot:
            alertar_equipo_contrato_fallido(cot)
        return
    notificar_contrato_listo(contrato)
