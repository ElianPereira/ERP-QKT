"""Señales que alimentan el embudo de prospectos (Issue #468, fase 1).

Todas corren en `transaction.on_commit` y envueltas en try/except: marketing
nunca debe romper el guardado de un mensaje, una visita, una cotización o un
pago.
"""
import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from comercial.models import Cotizacion, Pago
from comunicacion.models import ConversacionWhatsApp, MensajeWhatsApp, Visita

from .services import pipeline

logger = logging.getLogger(__name__)

CAMPOS_ANUNCIO = {'anuncio_id', 'anuncio_url', 'anuncio_titulo', 'anuncio_clid'}


def _diferir(funcion, *args, **kwargs):
    def correr():
        try:
            funcion(*args, **kwargs)
        except Exception:
            logger.exception('Marketing: falló la señal %s', getattr(funcion, '__name__', funcion))
    transaction.on_commit(correr)


def _mensaje(mensaje_id):
    mensaje = MensajeWhatsApp.objects.select_related('conversacion').get(pk=mensaje_id)
    conv = mensaje.conversacion
    if mensaje.direccion == 'ENTRADA':
        pipeline.registrar_contacto(telefono=conv.telefono, nombre=conv.nombre, conversacion=conv,
                                    cliente=conv.cliente, momento=mensaje.created_at,
                                    primer_contacto=mensaje.created_at)
    else:
        pipeline.sincronizar_existente(telefono=conv.telefono, cliente=conv.cliente)


def _anuncio(conv_id):
    pipeline.aplicar_anuncio(ConversacionWhatsApp.objects.get(pk=conv_id))


def _visita(visita_id):
    visita = Visita.objects.select_related('cliente', 'conversacion').get(pk=visita_id)
    if visita.estado in pipeline.VISITAS_VIVAS:
        pipeline.registrar_contacto(telefono=visita.telefono, nombre=visita.nombre, cliente=visita.cliente,
                                    conversacion=visita.conversacion)
    else:
        pipeline.sincronizar_existente(telefono=visita.telefono, cliente=visita.cliente)


def _cotizacion(cotizacion_id, creada):
    cot = Cotizacion.objects.select_related('cliente').get(pk=cotizacion_id)
    if cot.es_historica:
        return
    viva = cot.estado in pipeline.ESTADOS_VIVOS + pipeline.ESTADOS_VENTA
    if creada or viva:
        pipeline.registrar_contacto(telefono=cot.cliente.telefono, cliente=cot.cliente, cotizacion=cot,
                                    nombre=cot.cliente.nombre)
    else:
        pipeline.sincronizar_existente(telefono=cot.cliente.telefono, cliente=cot.cliente)


def _pago(pago_id):
    cot = Pago.objects.select_related('cotizacion__cliente').get(pk=pago_id).cotizacion
    if cot is not None:
        pipeline.sincronizar_existente(telefono=cot.cliente.telefono, cliente=cot.cliente)


@receiver(post_save, sender=MensajeWhatsApp, dispatch_uid='marketing_mensaje')
def mensaje_guardado(sender, instance, created, **kwargs):
    if created and not kwargs.get('raw'):
        _diferir(_mensaje, instance.pk)


@receiver(post_save, sender=ConversacionWhatsApp, dispatch_uid='marketing_conversacion')
def conversacion_guardada(sender, instance, created, update_fields=None, **kwargs):
    if not created and update_fields and CAMPOS_ANUNCIO & set(update_fields) and not kwargs.get('raw'):
        _diferir(_anuncio, instance.pk)


@receiver(post_save, sender=Visita, dispatch_uid='marketing_visita')
def visita_guardada(sender, instance, **kwargs):
    if not kwargs.get('raw'):
        _diferir(_visita, instance.pk)


@receiver(post_save, sender=Cotizacion, dispatch_uid='marketing_cotizacion')
def cotizacion_guardada(sender, instance, created, **kwargs):
    if not kwargs.get('raw'):
        _diferir(_cotizacion, instance.pk, created)


@receiver(post_save, sender=Pago, dispatch_uid='marketing_pago')
def pago_guardado(sender, instance, **kwargs):
    if not kwargs.get('raw'):
        _diferir(_pago, instance.pk)
