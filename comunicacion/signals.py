"""
Signals que disparan comunicaciones automáticas con el cliente.
"""
import logging
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _signals_enabled():
    return getattr(settings, 'COMUNICACION_SIGNALS_ENABLED', True)


def _portal_url(cotizacion):
    try:
        from comercial.models import PortalCliente
        portal = PortalCliente.objects.filter(cotizacion=cotizacion, activo=True).first()
        if portal:
            base = getattr(settings, 'PORTAL_URL', 'https://clientes.quintakooxtanil.com')
            return f"{base}/mi-evento/{portal.token}/"
    except Exception:
        pass
    return ''


@receiver(post_save, sender='comercial.Pago')
def notificar_pago_cliente(sender, instance, created, **kwargs):
    """Al registrar un Pago, notifica al cliente vía email."""
    if not _signals_enabled() or not created:
        return
    from .services import enviar_email
    pago = instance
    cot = pago.cotizacion
    if not cot or not cot.cliente or not cot.cliente.email:
        return

    if pago.tipo == 'REEMBOLSO':
        enviar_email(
            cotizacion=cot, pago=pago,
            tipo='REEMBOLSO',
            destinatario=cot.cliente.email,
            asunto=f"Reembolso procesado — {cot.nombre_evento}",
            template='comunicacion/email/reembolso.html',
            context={'cotizacion': cot, 'pago': pago},
        )
        return

    total_pagado = cot.total_pagado()
    saldo = cot.precio_final - total_pagado
    enviar_email(
        cotizacion=cot, pago=pago,
        tipo='CONFIRMACION_PAGO',
        destinatario=cot.cliente.email,
        asunto=f"Pago recibido — {cot.nombre_evento}",
        template='comunicacion/email/confirmacion_pago.html',
        context={
            'cotizacion': cot, 'pago': pago,
            'total_pagado': total_pagado, 'saldo': saldo,
            'portal_url': _portal_url(cot),
        },
    )


@receiver(post_save, sender='comercial.Cotizacion')
def notificar_cotizacion_enviada(sender, instance, created, update_fields=None, **kwargs):
    """
    Cuando una cotización pasa a COTIZADA, envía email automático con el resumen.
    Idempotente: solo manda si no existe ya una comunicación tipo COTIZACION para esa cotización.
    """
    if not _signals_enabled() or created:
        return
    cot = instance
    if cot.estado != 'COTIZADA':
        return
    from .models import ComunicacionCliente
    from .services import enviar_email

    if ComunicacionCliente.objects.filter(cotizacion=cot, tipo='COTIZACION').exists():
        return
    if not cot.cliente or not cot.cliente.email:
        return

    enviar_email(
        cotizacion=cot,
        tipo='COTIZACION',
        destinatario=cot.cliente.email,
        asunto=f"Tu cotización — {cot.nombre_evento}",
        template='comunicacion/email/cotizacion.html',
        context={'cotizacion': cot, 'portal_url': _portal_url(cot)},
    )
