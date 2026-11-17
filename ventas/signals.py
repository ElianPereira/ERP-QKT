"""Disparadores del motor de comisiones (Issue #505, PR 3).

Todo va en `on_commit` y dentro de `try/except`: un fallo de `ventas` nunca tumba el guardado de un
`Pago`, `Cotizacion`, `ItemCotizacion` o `Visita`. El barrido diario (`sincronizar_comisiones`)
cubre lo que cambia con `update()` sin señal, los contracargos y las confirmaciones del cron.
"""
import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _despues(funcion, *args):
    def _correr():
        try:
            funcion(*args)
        except Exception:
            logger.exception('Ventas: falló %s%s', getattr(funcion, '__name__', funcion), args)
    transaction.on_commit(_correr)


def _sincronizar_cotizacion(cotizacion_id, pago_id=None):
    from comercial.models import Cotizacion, Pago

    from .services_comisiones import sincronizar

    cot = Cotizacion.objects.filter(pk=cotizacion_id, es_historica=False, vendedor__isnull=False).first()
    if cot is None:
        return
    pago = Pago.objects.filter(pk=pago_id).first() if pago_id else None
    sincronizar(cot, pago=pago)


@receiver(post_save, sender='comercial.Pago', dispatch_uid='ventas_comision_pago_guardado')
def pago_guardado(sender, instance, **kwargs):
    _despues(_sincronizar_cotizacion, instance.cotizacion_id, instance.pk)


@receiver(post_delete, sender='comercial.Pago', dispatch_uid='ventas_comision_pago_borrado')
def pago_borrado(sender, instance, **kwargs):
    _despues(_sincronizar_cotizacion, instance.cotizacion_id)


@receiver(post_save, sender='comercial.Cotizacion', dispatch_uid='ventas_comision_cotizacion')
def cotizacion_guardada(sender, instance, **kwargs):
    if instance.vendedor_id and not instance.es_historica:
        _despues(_sincronizar_cotizacion, instance.pk)


@receiver(post_save, sender='comercial.ItemCotizacion', dispatch_uid='ventas_comision_item_guardado')
def item_guardado(sender, instance, **kwargs):
    _despues(_sincronizar_cotizacion, instance.cotizacion_id)


@receiver(post_delete, sender='comercial.ItemCotizacion', dispatch_uid='ventas_comision_item_borrado')
def item_borrado(sender, instance, **kwargs):
    _despues(_sincronizar_cotizacion, instance.cotizacion_id)


def _devengar_visita(visita_id):
    from comunicacion.models import Visita

    from .services_comisiones import devengar_visita

    visita = Visita.objects.filter(pk=visita_id).first()
    if visita is not None:
        devengar_visita(visita)


@receiver(post_save, sender='comunicacion.Visita', dispatch_uid='ventas_comision_visita')
def visita_guardada(sender, instance, **kwargs):
    # Solo importa lo cerrado (atendida / no llegó): una visita sin resultado no devenga nada.
    if instance.vendedor_id and instance.resultado:
        _despues(_devengar_visita, instance.pk)
