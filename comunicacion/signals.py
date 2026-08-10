"""
Signals que disparan comunicaciones automáticas con el cliente.

Aquí solo se decide *cuándo* notificar. El contenido, los canales y la
idempotencia viven en `services_notificaciones.py`.

Los envíos van dentro de `transaction.on_commit()`: el `save()` del admin corre
en una transacción y sin esto se mandaría el correo de una cotización o un pago
que todavía puede revertirse.
"""
import logging

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _signals_enabled():
    return getattr(settings, 'COMUNICACION_SIGNALS_ENABLED', True)


@receiver(post_save, sender='comercial.Pago')
def notificar_pago_cliente(sender, instance, created, **kwargs):
    """Al registrar un Pago, notifica al cliente por email y WhatsApp."""
    if not _signals_enabled() or not created:
        return
    pago = instance
    cot = pago.cotizacion
    if not cot or not cot.cliente:
        return

    if pago.tipo == 'REEMBOLSO':
        # Los reembolsos conservan el comportamiento previo: solo email.
        def _enviar_reembolso():
            from .services import enviar_email
            enviar_email(
                cotizacion=cot, pago=pago,
                tipo='REEMBOLSO',
                destinatario=cot.cliente.email,
                asunto=f"Reembolso procesado — {cot.nombre_evento}",
                template='comunicacion/email/reembolso.html',
                context={'cotizacion': cot, 'pago': pago},
                clave_idempotencia=f"pago:{pago.pk}:reembolso:email",
            )

        if cot.cliente.email:
            transaction.on_commit(_enviar_reembolso)
        return

    def _notificar():
        from .services_notificaciones import notificar_pago
        notificar_pago(pago)

    transaction.on_commit(_notificar)


@receiver(post_save, sender='comercial.Cotizacion')
def notificar_cotizacion_enviada(sender, instance, created, update_fields=None, **kwargs):
    """
    Cuando una cotización pasa a COTIZADA, notifica al cliente por email y WhatsApp.

    `post_save` no permite ver la transición BORRADOR→COTIZADA (el modelo no
    guarda el estado anterior), así que el filtro es el estado final más la clave
    de idempotencia del servicio: volver a guardar una COTIZADA no reenvía nada.
    Los BORRADOR no disparan, y el `created` excluye las cotizaciones que nacen
    ya COTIZADA —hoy ninguna: el cotizador público crea en BORRADOR—.
    """
    if not _signals_enabled() or created:
        return
    cot = instance
    if cot.estado != 'COTIZADA':
        return
    if not cot.cliente:
        return

    def _notificar():
        from .services_notificaciones import notificar_cotizacion
        notificar_cotizacion(cot, origen='ERP')

    transaction.on_commit(_notificar)
