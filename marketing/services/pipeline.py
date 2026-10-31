"""Embudo de prospectos: identidad, etapa derivada y reapertura (Issue #468, fase 1).

La etapa nunca se captura: `sincronizar()` la deriva de lo que ya existe
(mensajes, visitas, cotizaciones) y es idempotente. Solo avanza, salvo las
reversas explícitas (una venta que se cae, una cotización que muere, el
silencio). Marketing no copia montos: todo importe se lee de la cotización.
"""
import logging
import unicodedata
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from comercial.models import Cliente, Cotizacion
from comunicacion.models import ConversacionWhatsApp, MensajeWhatsApp, Visita
from comunicacion.services import normalizar_telefono_wa
from comunicacion.services_responsables import rol_de_telefono

from ..models import Lead, LeadEvento

logger = logging.getLogger(__name__)

DIAS_SIN_RESPUESTA = 15
DIAS_REAPERTURA = 90
DIAS_POST_SERVICIO = 30
DIAS_ANUNCIO = 2

ESTADOS_VENTA = ('CONFIRMADA', 'EJECUTADA', 'CERRADA')
ESTADOS_VIVOS = ('BORRADOR', 'COTIZADA')
ESTADOS_MUERTOS = ('EXPIRADA', 'CANCELADA')
VISITAS_VIVAS = ('POR_CONFIRMAR', 'CONFIRMADA')

_FUENTE_POR_MEDIO = {
    'facebook': 'FACEBOOK', 'instagram': 'INSTAGRAM', 'tiktok': 'TIKTOK', 'google': 'GOOGLE',
    'recomendacion': 'RECOMENDACION', 'whatsapp': 'WHATSAPP', 'visite la quinta': 'VISITA_QUINTA',
    'otro': 'OTRO',
}


def _sin_acentos(texto: str) -> str:
    base = unicodedata.normalize('NFD', texto or '')
    return ''.join(c for c in base if unicodedata.category(c) != 'Mn').strip().lower()


def _diez(telefono: str) -> str:
    return ''.join(filter(str.isdigit, telefono or ''))[-10:]


def _evento(lead, tipo, *, anterior='', nueva='', origen='SISTEMA', detalle=None, usuario=None):
    return LeadEvento.objects.create(
        lead=lead, tipo=tipo, etapa_anterior=anterior, etapa_nueva=nueva, origen=origen,
        detalle=detalle or {}, created_by=usuario if getattr(usuario, 'pk', None) else None)


# --- Identidad -----------------------------------------------------------

def telefono_del_equipo(telefono) -> bool:
    """Un número del directorio (encargado, dirección…) nunca es un prospecto."""
    tel = normalizar_telefono_wa(telefono)
    return bool(tel) and rol_de_telefono(tel) is not None


def _filtro_identidad(telefono='', cliente=None) -> Q:
    q = Q()
    if telefono:
        q |= Q(telefono__endswith=_diez(telefono))
    if cliente is not None:
        q |= Q(cliente_id=cliente.pk)
    return q


def leads_de(telefono='', cliente=None):
    q = _filtro_identidad(telefono, cliente)
    return Lead.objects.filter(q) if q else Lead.objects.none()


def _conversacion_de(telefono):
    return ConversacionWhatsApp.objects.filter(telefono=telefono).first() if telefono else None


def _cliente_de(telefono):
    if not telefono:
        return None
    return Cliente.objects.filter(telefono__endswith=_diez(telefono)).order_by('-id').first()


# --- Evidencia -----------------------------------------------------------

def cotizaciones_de(lead):
    q = Q()
    if lead.cliente_id:
        q |= Q(cliente_id=lead.cliente_id)
    if lead.telefono:
        q |= Q(cliente__telefono__endswith=_diez(lead.telefono))
    if lead.cotizacion_id:
        q |= Q(pk=lead.cotizacion_id)
    if not q:
        return Cotizacion.objects.none()
    return Cotizacion.objects.filter(q, es_historica=False).filter(
        Q(created_at__gte=lead.fecha_primer_contacto) | Q(pk=lead.cotizacion_id or 0))


def _conversacion_del_lead(lead):
    return lead.conversacion or _conversacion_de(lead.telefono)


def _mensajes(lead):
    conv = _conversacion_del_lead(lead)
    if conv is None:
        return MensajeWhatsApp.objects.none()
    return conv.mensajes.filter(created_at__gte=lead.fecha_primer_contacto)


def _visitas(lead):
    q = Q()
    conv = _conversacion_del_lead(lead)
    if conv is not None:
        q |= Q(conversacion=conv)
    if lead.cliente_id:
        q |= Q(cliente_id=lead.cliente_id)
    if lead.telefono:
        q |= Q(telefono__endswith=_diez(lead.telefono))
    if not q:
        return Visita.objects.none()
    return Visita.objects.filter(q, created_at__gte=lead.fecha_primer_contacto)


def _mensajes_de_contacto(lead):
    """Respuestas reales del agente o de una persona; los avisos fijos
    (bienvenida, espera) y los envíos automáticos no cuentan como contacto."""
    from comunicacion.services_agente import AVISO_ESPERA, AVISO_INICIAL, MENSAJE_HUMANO
    fijos = (AVISO_INICIAL, AVISO_ESPERA, MENSAJE_HUMANO)
    return _mensajes(lead).filter(
        Q(direccion='HUMANO') | Q(direccion='AGENTE', automatico=False)).exclude(texto__in=fijos)


def _ultima_interaccion(lead, cots, visitas):
    fechas = [lead.fecha_ultima_interaccion]
    fechas += list(_mensajes(lead).filter(direccion__in=('ENTRADA', 'HUMANO'))
                   .order_by('-created_at').values_list('created_at', flat=True)[:1])
    fechas += [c.created_at for c in cots]
    fechas += [v.created_at for v in visitas]
    return max(fechas)


def etapa_derivada(lead, cots=None, visitas=None) -> str:
    cots = list(cotizaciones_de(lead)) if cots is None else cots
    if any(c.estado in ESTADOS_VENTA for c in cots):
        return Lead.GANADO
    if any(c.estado in ESTADOS_VIVOS for c in cots):
        return Lead.COTIZADO
    visitas = list(_visitas(lead)) if visitas is None else visitas
    if any(v.estado in VISITAS_VIVAS for v in visitas):
        return Lead.VISITA_AGENDADA
    if _mensajes_de_contacto(lead).exists():
        return Lead.CONTACTADO
    return Lead.NUEVO


# --- Fuente --------------------------------------------------------------

def fuente_de(telefono='', cliente=None, conv=None, cotizacion=None, primer_contacto=None, excluir=None) -> dict:
    """Devuelve {'fuente', 'anuncio_id', 'anuncio_titulo', 'anuncio_clid'}."""
    primer_contacto = primer_contacto or timezone.now()
    if conv is not None and (conv.anuncio_id or conv.anuncio_url) \
            and conv.created_at >= primer_contacto - timedelta(days=DIAS_ANUNCIO):
        return {'fuente': 'META_ANUNCIO', 'anuncio_id': conv.anuncio_id,
                'anuncio_titulo': conv.anuncio_titulo, 'anuncio_clid': conv.anuncio_clid}
    previos = leads_de(telefono, cliente).filter(etapa=Lead.GANADO)
    if excluir is not None:
        previos = previos.exclude(pk=excluir.pk)
    if previos.exists():
        return {'fuente': 'RECURRENTE'}
    if cotizacion is not None and cotizacion.como_nos_encontro:
        fuente = _FUENTE_POR_MEDIO.get(_sin_acentos(cotizacion.como_nos_encontro))
        if fuente and fuente != 'OTRO':
            return {'fuente': fuente}
    if cliente is not None:
        origen = _FUENTE_POR_MEDIO.get(_sin_acentos(cliente.origen))
        if origen and origen != 'OTRO':
            return {'fuente': origen}
    if conv is not None:
        return {'fuente': 'WHATSAPP'}
    return {'fuente': 'OTRO'}


def _mejorar_fuente(lead, cot=None):
    if lead.fuente not in Lead.FUENTES_GENERICAS or lead.anonimizado_en:
        return
    nueva = fuente_de(lead.telefono, lead.cliente, _conversacion_del_lead(lead), cot,
                      lead.fecha_primer_contacto, excluir=lead)
    if nueva['fuente'] in Lead.FUENTES_GENERICAS or nueva['fuente'] == lead.fuente:
        return
    anterior = lead.fuente
    lead.fuente = nueva['fuente']
    for campo in ('anuncio_id', 'anuncio_titulo', 'anuncio_clid'):
        if nueva.get(campo):
            setattr(lead, campo, nueva[campo])
    lead.save(update_fields=['fuente', 'anuncio_id', 'anuncio_titulo', 'anuncio_clid', 'updated_at'])
    _evento(lead, 'FUENTE', detalle={'antes': anterior, 'despues': nueva['fuente']})


# --- Transiciones --------------------------------------------------------

def _cambiar_etapa(lead, nueva, ahora, *, tipo='ETAPA', origen='SISTEMA', detalle=None):
    anterior = lead.etapa
    lead.etapa = nueva
    lead.etapa_cambiada_en = ahora
    lead.save(update_fields=['etapa', 'etapa_cambiada_en', 'updated_at'])
    _evento(lead, tipo, anterior=anterior, nueva=nueva, origen=origen, detalle=detalle)


def _perder(lead, ahora, motivo='', detalle=None):
    lead.motivo_perdida = motivo
    lead.motivo_perdida_origen = 'SISTEMA' if motivo else ''
    _cambiar_etapa(lead, Lead.PERDIDO, ahora, detalle=detalle)
    lead.save(update_fields=['motivo_perdida', 'motivo_perdida_origen', 'updated_at'])


def _cot_relevante(cots):
    for estados in (ESTADOS_VENTA, ESTADOS_VIVOS):
        elegidas = [c for c in cots if c.estado in estados]
        if elegidas:
            return max(elegidas, key=lambda c: c.pk)
    return None


def sincronizar(lead, ahora=None):
    """Lleva el prospecto a la etapa que dicen los datos. Idempotente."""
    ahora = ahora or timezone.now()
    lead.refresh_from_db()
    if lead.anonimizado_en or lead.etapa == Lead.PERDIDO:
        return lead
    cambios = []
    if lead.cliente_id is None and lead.telefono:
        cliente = _cliente_de(lead.telefono)
        if cliente is not None:
            lead.cliente = cliente
            cambios.append('cliente')
    cots = list(cotizaciones_de(lead))
    visitas = list(_visitas(lead))
    cot = _cot_relevante(cots)
    if cot is not None and (lead.cotizacion_id != cot.pk or lead.tipo_servicio != cot.tipo_servicio):
        lead.cotizacion, lead.tipo_servicio = cot, cot.tipo_servicio
        cambios += ['cotizacion', 'tipo_servicio']
    ultima = _ultima_interaccion(lead, cots, visitas)
    if ultima > lead.fecha_ultima_interaccion:
        lead.fecha_ultima_interaccion = ultima
        cambios.append('fecha_ultima_interaccion')
    if cambios:
        lead.save(update_fields=cambios + ['updated_at'])

    venta = any(c.estado in ESTADOS_VENTA for c in cots)
    if lead.etapa == Lead.GANADO:
        if not venta:
            _perder(lead, ahora, 'CANCELO', {'razon': 'la venta ya no está confirmada'})
        return lead

    derivada = etapa_derivada(lead, cots, visitas)
    viva = any(c.estado in ESTADOS_VIVOS for c in cots)
    if derivada == Lead.GANADO:
        _cambiar_etapa(lead, Lead.GANADO, ahora)
        _mejorar_fuente(lead, cot)
        return lead
    if lead.etapa == Lead.COTIZADO and not viva and any(c.estado in ESTADOS_MUERTOS for c in cots):
        _perder(lead, ahora, '', {'razon': 'cotización vencida o cancelada'})
        return lead
    if Lead.RANGO[derivada] > Lead.RANGO[lead.etapa]:
        _cambiar_etapa(lead, derivada, ahora)
    _mejorar_fuente(lead, cot)

    visita_futura = any(v.estado in VISITAS_VIVAS and v.inicio >= ahora for v in visitas)
    if not visita_futura and ahora - lead.fecha_ultima_interaccion > timedelta(days=DIAS_SIN_RESPUESTA):
        _perder(lead, ahora, '', {'razon': f'{DIAS_SIN_RESPUESTA} días sin interacción'})
    return lead


def _termino_hace_mas_de(lead, dias, ahora) -> bool:
    cot = lead.cotizacion
    if cot is not None:
        fin = cot.fecha_salida or cot.fecha_evento
        return fin < (ahora - timedelta(days=dias)).date()
    return ahora - lead.etapa_cambiada_en > timedelta(days=dias)


def _reabrir(lead, ahora):
    anterior = lead.motivo_perdida
    lead.motivo_perdida = ''
    lead.motivo_perdida_origen = ''
    lead.etapa = Lead.NUEVO
    lead.fecha_ultima_interaccion = ahora
    lead.save(update_fields=['motivo_perdida', 'motivo_perdida_origen', 'etapa',
                             'fecha_ultima_interaccion', 'updated_at'])
    destino = etapa_derivada(lead)
    lead.etapa = destino
    lead.etapa_cambiada_en = ahora
    lead.save(update_fields=['etapa', 'etapa_cambiada_en', 'updated_at'])
    _evento(lead, 'REAPERTURA', anterior=Lead.PERDIDO, nueva=destino, detalle={'motivo_anterior': anterior})
    return lead


def _crear(*, tel, cliente, nombre, conv, cot, ahora, primer_contacto):
    datos = fuente_de(tel, cliente, conv, cot, primer_contacto)
    lead = Lead(
        telefono=tel, nombre=(nombre or (cliente.nombre if cliente else '') or (conv.nombre if conv else ''))[:200],
        conversacion=conv, cliente=cliente, cotizacion=cot,
        tipo_servicio=cot.tipo_servicio if cot else '',
        fecha_primer_contacto=primer_contacto, fecha_ultima_interaccion=max(primer_contacto, ahora),
        etapa_cambiada_en=primer_contacto, **datos)
    try:
        with transaction.atomic():
            lead.save()
    except IntegrityError:
        # Otro proceso ganó la carrera por el mismo teléfono.
        return leads_de(tel).exclude(etapa__in=Lead.CERRADAS).first()
    _evento(lead, 'CREADO', nueva=Lead.NUEVO, detalle={'fuente': lead.fuente})
    return lead


def registrar_contacto(*, telefono='', cliente=None, nombre='', conversacion=None, cotizacion=None,
                       ahora=None, primer_contacto=None, reconstruir=False, momento=None):
    """Un contacto nuevo (mensaje del cliente o cotización): crea, reabre o
    reutiliza el prospecto que corresponda. Devuelve el prospecto o None.
    `momento` es cuándo ocurrió el contacto (por defecto, ahora): un contacto
    anterior al cierre del último prospecto no lo reabre ni crea otro."""
    ahora = ahora or timezone.now()
    tel = normalizar_telefono_wa(telefono) or (normalizar_telefono_wa(cliente.telefono) if cliente else '')
    if telefono_del_equipo(tel):
        return None
    if cotizacion is not None and cotizacion.es_historica:
        return None
    if cliente is None and cotizacion is not None:
        cliente = cotizacion.cliente
    if cliente is None:
        cliente = _cliente_de(tel)
    if conversacion is None:
        conversacion = _conversacion_de(tel)
    if not tel and cliente is None:
        return None

    existentes = leads_de(tel, cliente)
    abierto = existentes.exclude(etapa__in=Lead.CERRADAS).order_by('-fecha_primer_contacto').first()
    if abierto is not None:
        return sincronizar(abierto, ahora)
    ultimo = existentes.order_by('-etapa_cambiada_en', '-id').first()
    if ultimo is not None:
        if reconstruir or (momento is not None and momento <= ultimo.etapa_cambiada_en):
            return sincronizar(ultimo, ahora)
        if ultimo.etapa == Lead.GANADO:
            ultimo = sincronizar(ultimo, ahora)
        if ultimo.etapa == Lead.GANADO and not _termino_hace_mas_de(ultimo, DIAS_POST_SERVICIO, ahora):
            return ultimo  # post-venta: no es un prospecto nuevo
        if ultimo.etapa == Lead.PERDIDO and not ultimo.anonimizado_en \
                and ahora - ultimo.etapa_cambiada_en < timedelta(days=DIAS_REAPERTURA):
            return sincronizar(_reabrir(ultimo, ahora), ahora)

    if primer_contacto is None:
        primer_contacto = cotizacion.created_at if cotizacion is not None and cotizacion.created_at else ahora
    lead = _crear(tel=tel, cliente=cliente, nombre=nombre, conv=conversacion, cot=cotizacion,
                  ahora=ahora, primer_contacto=primer_contacto)
    return sincronizar(lead, ahora) if lead is not None else None


def sincronizar_existente(*, telefono='', cliente=None, ahora=None):
    """Actualiza los prospectos de esta persona sin crear ninguno."""
    tel = normalizar_telefono_wa(telefono)
    if telefono_del_equipo(tel):
        return []
    return [sincronizar(lead, ahora) for lead in leads_de(tel, cliente).exclude(etapa=Lead.PERDIDO)]


def aplicar_anuncio(conv, ahora=None):
    """La conversación recibió el `referral` de un anuncio: mejora la fuente."""
    tel = conv.telefono
    if telefono_del_equipo(tel):
        return
    for lead in leads_de(tel).exclude(etapa__in=Lead.CERRADAS):
        _mejorar_fuente(lead)
