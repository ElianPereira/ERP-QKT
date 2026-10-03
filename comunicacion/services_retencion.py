"""
Conservación de las conversaciones de WhatsApp del agente (Aviso de
Privacidad v2.5, §9). Plazos dados por el abogado del propietario, contados
desde el último mensaje de la conversación:

- Contratación (`CONTRATACION`): el número es de un cliente con una
  cotización confirmada, ejecutada, cerrada o con algún pago. La
  conversación puede haber servido para pactar el servicio y se conserva
  como mensaje de datos mercantil y fiscal (art. 30 CFF, Código de
  Comercio): 5 años.
- Atención (`ATENCION`): intervino una persona del equipo (respondió o se
  pidió atención humana). Sirve para aclaraciones y reclamos ante PROFECO:
  2 años.
- Consulta (`CONSULTA`): dudas, cotizaciones y prospección que no se
  concretaron: 1 año.

Vencido el plazo se borran la conversación, sus mensajes y las copias del
texto que quedaron en la bitácora de comunicaciones (respuestas del agente
y respuestas del equipo desde el admin). Es borrado físico a propósito: el
aviso promete supresión, y una conversación no es un registro contable.
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import ComunicacionCliente, ConversacionWhatsApp

PLAZOS = {
    'CONTRATACION': timedelta(days=365 * 5),
    'ATENCION': timedelta(days=365 * 2),
    'CONSULTA': timedelta(days=365),
}
ESTADOS_CONTRATADOS = ('CONFIRMADA', 'EJECUTADA', 'CERRADA')


def categoria(conv) -> str:
    from comercial.models import Cotizacion
    contratada = Cotizacion.objects.filter(
        Q(estado__in=ESTADOS_CONTRATADOS) | Q(pagos__isnull=False),
        cliente__telefono__endswith=conv.telefono[-10:],
    ).exists()
    if contratada:
        return 'CONTRATACION'
    if conv.motivo_humano or conv.mensajes.filter(direccion='HUMANO').exists():
        return 'ATENCION'
    return 'CONSULTA'


def _copias_en_bitacora(conv):
    """Copias del texto en la bitácora: lo que mandó el agente y lo que
    contestó el equipo desde Conversaciones (`responder_como_persona`)."""
    return ComunicacionCliente.objects.filter(
        canal='WHATSAPP', cotizacion__isnull=True, destinatario=conv.telefono,
    ).filter(Q(tipo='AGENTE_IA') | Q(tipo='OTRO', trigger='MANUAL'))


def purgar_conversaciones(*, aplicar=False, ahora=None) -> dict:
    """Borra (o solo cuenta, si `aplicar` es False) las conversaciones vencidas.

    Devuelve cuántas vencen por categoría y cuántas copias de bitácora caen.
    """
    ahora = ahora or timezone.now()
    resumen = {cat: 0 for cat in PLAZOS}
    resumen['copias_bitacora'] = 0
    # Ninguna conversación vence antes del plazo más corto: no hace falta
    # clasificar las recientes.
    candidatas = ConversacionWhatsApp.objects.filter(
        ultimo_mensaje__lt=ahora - min(PLAZOS.values()))
    for conv in candidatas.iterator():
        cat = categoria(conv)
        if conv.ultimo_mensaje >= ahora - PLAZOS[cat]:
            continue
        resumen[cat] += 1
        copias = _copias_en_bitacora(conv)
        resumen['copias_bitacora'] += copias.count()
        if aplicar:
            with transaction.atomic():
                copias.delete()
                conv.delete()  # sus MensajeWhatsApp caen en cascada
    return resumen
