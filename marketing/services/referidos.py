"""Referidos: quien recomienda gana un beneficio cuando su recomendado aparta fecha (Issue #468, fase 7).

Reglas del propietario: lo gana QUIEN RECOMIENDA cuando la cotización del recomendado queda CONFIRMADA (anticipo
pagado). Evento 20 % solo sobre la renta de la Quinta, Pasadía 10 % sobre el paquete Básico/Premium, Hospedaje 10 %
sobre las noches de habitación; nunca extras. No acumulable: si la cotización ya tiene otra promoción, se aplica solo
la que más ahorre al cliente y el beneficio se conserva si no es la mejor. Vigencia de 12 meses, un solo uso; se anula
si la venta del recomendado se cae antes de usarse; ya usado, no se revoca.

El beneficio es un `comercial.Descuento` MANUAL acotado por `productos`, con `activo=False`: así no aparece en la lista de
descuentos manuales del admin ni se evalúa solo; únicamente este servicio lo aplica, y lo hace con
`DescuentoService.aplicar` (mismo flujo de totales y contabilidad que cualquier descuento). La aplicación NO es
automática al cotizar: al crearse una cotización de quien tiene un beneficio vigente se abre un `Pendiente` para que
una persona decida a qué cotización aplicarlo (lo más seguro con dinero de por medio).

Renta de la Quinta (Evento): producto con rol BASE_EVENTO, más el componente `fijo_por_evento` de un paquete cuyo
producto sea la renta (rol BASE_EVENTO, o nombre con «quinta» y «renta»/«arrendamiento»). En `ItemCotizacion` cada
componente fijo es su propia línea con `producto = producto_hijo`, así que `Descuento.productos` basta para acotar la base.
"""
import calendar
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, Descuento, Producto, ProductoComponente
from comercial.roles_cotizador import normalizar
from comercial.services_descuentos import CERO, DescuentoService
from comunicacion.formato_wa import bloques
from comunicacion.services import normalizar_telefono_wa

from ..models import Lead, Referido
from . import pipeline

logger = logging.getLogger(__name__)

VIGENCIA_MESES = 12
ESTADOS_APLICABLES = ('BORRADOR', 'COTIZADA')
# tipo de servicio → (porcentaje, nombre de la regla, roles de producto que forman la base)
BENEFICIOS = {
    'EVENTO': (Decimal('20'), 'Referido Evento', ('BASE_EVENTO',)),
    'PASADIA': (Decimal('10'), 'Referido Pasadía', ('BASE_PASADIA_BASICO', 'BASE_PASADIA_PREMIUM')),
    'HOSPEDAJE': (Decimal('10'), 'Referido Hospedaje', ('HABITACION_HOSPEDAJE',)),
}
DESCRIPCION_REGLA = ('Regla reservada al módulo de Referidos (Marketing): se aplica solo desde ahí, una vez por '
                     'beneficio ganado. No la actives ni la apliques a mano.')


@dataclass
class Resultado:
    aplicado: bool
    motivo: str = ''
    monto: Decimal = CERO
    referido: Optional[Referido] = None


def _diez(telefono) -> str:
    return ''.join(filter(str.isdigit, telefono or ''))[-10:]


def _mas_meses(momento: datetime, meses: int) -> datetime:
    mes = momento.month - 1 + meses
    anio, mes = momento.year + mes // 12, mes % 12 + 1
    return momento.replace(year=anio, month=mes, day=min(momento.day, calendar.monthrange(anio, mes)[1]))


# ───────────────────────────── Captura ─────────────────────────────────────

def cliente_por_texto(texto: str) -> Optional[Cliente]:
    """Quien recomendó, emparejado SOLO por teléfono (≥10 dígitos). Un nombre no se empareja: es ambiguo y
    adivinar daría el beneficio a la persona equivocada."""
    digitos = _diez(texto)
    if len(digitos) < 10:
        return None
    encontrados = list(Cliente.objects.filter(telefono__endswith=digitos)[:2])
    return encontrados[0] if len(encontrados) == 1 else None


def _es_el_mismo(recomendador: Cliente, lead: Lead) -> bool:
    if lead.cliente_id and lead.cliente_id == recomendador.pk:
        return True
    return bool(_diez(lead.telefono)) and _diez(lead.telefono) == _diez(recomendador.telefono)


def registrar(lead: Lead, texto: str = '', recomendador: Optional[Cliente] = None, usuario=None) -> Optional[Referido]:
    """Anota quién recomendó a este prospecto. Guarda lo que dijo (`recomendado_por_texto`) y, si empata por
    teléfono con un cliente (y no es el mismo prospecto), crea el `Referido` REGISTRADO. Nunca revela si hubo
    empate. Idempotente por prospecto."""
    texto = (texto or '').strip()[:120]
    recomendador = recomendador or cliente_por_texto(texto)
    cambios = []
    if texto and lead.recomendado_por_texto != texto:
        lead.recomendado_por_texto = texto
        cambios.append('recomendado_por_texto')
    existente = Referido.objects.filter(recomendado_lead=lead).first()
    if recomendador is not None and not _es_el_mismo(recomendador, lead) and existente is None:
        if lead.recomendado_por_id != recomendador.pk:
            lead.recomendado_por = recomendador
            cambios.append('recomendado_por')
        existente = Referido.objects.create(
            recomendador=recomendador, recomendado_lead=lead, recomendado_cotizacion=lead.cotizacion,
            created_by=usuario if getattr(usuario, 'pk', None) else None)
    if cambios:
        if lead.fuente in Lead.FUENTES_GENERICAS or lead.fuente == 'RECOMENDACION':
            if lead.fuente != 'RECOMENDACION':
                pipeline._evento(lead, 'FUENTE', anterior=lead.fuente, nueva='RECOMENDACION')
                lead.fuente = 'RECOMENDACION'
                cambios.append('fuente')
        lead.save(update_fields=cambios + ['updated_at'])
    return existente


def registrar_desde_cotizacion(cotizacion: Cotizacion, texto: str) -> Optional[Referido]:
    """Cotizador web o Kooxi: liga lo que dijo el cliente con el prospecto de su cotización. Nunca lanza."""
    try:
        texto = (texto or '').strip()
        if not texto:
            return None
        lead = (pipeline.leads_de(cotizacion.cliente.telefono, cotizacion.cliente)
                .exclude(etapa=Lead.PERDIDO).order_by('-fecha_primer_contacto').first())
        if lead is None:
            lead = pipeline.registrar_contacto(telefono=cotizacion.cliente.telefono, cliente=cotizacion.cliente,
                                               cotizacion=cotizacion, nombre=cotizacion.cliente.nombre)
        return registrar(lead, texto) if lead is not None else None
    except Exception:
        logger.exception('Marketing: no se pudo registrar la recomendación')
        return None


# ───────────────────────────── Ganar / anular ──────────────────────────────

def _candidatos(cotizacion: Cotizacion):
    return Referido.objects.filter(estado=Referido.REGISTRADO).filter(
        Q(recomendado_cotizacion=cotizacion) | Q(recomendado_lead__cotizacion=cotizacion)
        | Q(recomendado_lead__cliente=cotizacion.cliente_id))


@transaction.atomic
def ganar_por_cotizacion(cotizacion: Cotizacion, ahora=None) -> list:
    """La cotización del recomendado quedó CONFIRMADA (o ya ejecutada): sus referidos REGISTRADO pasan a GANADO."""
    ahora = ahora or timezone.now()
    if cotizacion.es_historica or cotizacion.estado not in pipeline.ESTADOS_VENTA:
        return []
    ganados = []
    for ref in _candidatos(cotizacion).select_for_update().select_related('recomendador'):
        if cotizacion.cliente_id == ref.recomendador_id or _diez(cotizacion.cliente.telefono) == _diez(ref.recomendador.telefono):
            continue
        ref.estado = Referido.GANADO
        ref.recomendado_cotizacion = cotizacion
        ref.ganado_en = ahora
        ref.vence_en = _mas_meses(ahora, VIGENCIA_MESES)
        ref.save(update_fields=['estado', 'recomendado_cotizacion', 'ganado_en', 'vence_en', 'updated_at'])
        ganados.append(ref)
    return ganados


def anular_por_cotizacion(cotizacion: Cotizacion, motivo: str) -> int:
    """La venta del recomendado se cayó: se anulan los referidos aún sin usar. Los ya usados no se revocan."""
    return (Referido.objects.filter(recomendado_cotizacion=cotizacion,
                                    estado__in=[Referido.REGISTRADO, Referido.GANADO])
            .update(estado=Referido.ANULADO, motivo_cierre=motivo[:255], updated_at=timezone.now()))


def revisar_reembolso(cotizacion: Cotizacion) -> int:
    """Un reembolso que deja lo pagado por debajo del anticipo que confirma la venta anula el beneficio sin usar."""
    if cotizacion.precio_final <= 0:
        return 0
    minimo = cotizacion.precio_final * cotizacion.porcentaje_anticipo_confirmacion() / Decimal('100')
    if cotizacion.total_pagado_neto() < minimo:
        return anular_por_cotizacion(cotizacion, 'Reembolso de la venta recomendada antes de usar el beneficio')
    return 0


def vencer(ahora=None) -> int:
    ahora = ahora or timezone.now()
    return (Referido.objects.filter(estado=Referido.GANADO, vence_en__lt=ahora)
            .update(estado=Referido.VENCIDO, motivo_cierre='Pasaron 12 meses sin usarse', updated_at=ahora))


def liberar_si_no_se_concreto(cotizacion: Cotizacion) -> int:
    """La cotización donde se aplicó el beneficio expiró o se canceló sin confirmarse: el beneficio no se gastó."""
    if cotizacion.estado not in ('EXPIRADA', 'CANCELADA'):
        return 0
    return (Referido.objects.filter(usado_en_cotizacion=cotizacion, estado=Referido.USADO,
                                    uso_confirmado_en__isnull=True)
            .update(estado=Referido.GANADO, usado_en=None, usado_en_cotizacion=None, monto_beneficio=None,
                    updated_at=timezone.now()))


def confirmar_uso(cotizacion: Cotizacion) -> int:
    if cotizacion.estado not in pipeline.ESTADOS_VENTA:
        return 0
    return (Referido.objects.filter(usado_en_cotizacion=cotizacion, estado=Referido.USADO,
                                    uso_confirmado_en__isnull=True)
            .update(uso_confirmado_en=timezone.now(), updated_at=timezone.now()))


# ───────────────────────────── Beneficio ───────────────────────────────────

def _es_renta_de_quinta(producto: Producto) -> bool:
    nombre = normalizar(producto.nombre)
    return producto.rol_cotizador == 'BASE_EVENTO' or (
        producto.rol_cotizador == '' and 'quinta' in nombre and ('renta' in nombre or 'arrendamiento' in nombre))


def productos_elegibles(tipo_servicio: str) -> set:
    """Ids de producto sobre los que aplica el beneficio de ese servicio. Vacío = no hay beneficio."""
    if tipo_servicio not in BENEFICIOS:
        return set()
    ids = set(Producto.objects.filter(rol_cotizador__in=BENEFICIOS[tipo_servicio][2]).values_list('pk', flat=True))
    if tipo_servicio == 'EVENTO':
        fijos = (ProductoComponente.objects.filter(fijo_por_evento=True).select_related('producto_hijo'))
        ids |= {pc.producto_hijo_id for pc in fijos if _es_renta_de_quinta(pc.producto_hijo)}
    return ids


def descuento_de(tipo_servicio: str) -> tuple:
    """La regla `Referido <servicio>` (se crea la primera vez) con `productos` al día. Devuelve (regla, ids)."""
    porcentaje, nombre, _ = BENEFICIOS[tipo_servicio]
    ids = productos_elegibles(tipo_servicio)
    descuento, _creado = Descuento.objects.get_or_create(
        nombre=nombre, modo='MANUAL',
        defaults={'tipo_valor': 'PORCENTAJE', 'valor': porcentaje, 'activo': False, 'acumulable': False,
                  'tipos_servicio': [tipo_servicio], 'descripcion': DESCRIPCION_REGLA})
    if descuento.productos.count() != len(ids) or set(descuento.productos.values_list('pk', flat=True)) != ids:
        descuento.productos.set(ids)
    return descuento, ids


def vigentes_de(cliente: Cliente):
    return Referido.objects.filter(recomendador=cliente, estado=Referido.GANADO,
                                   vence_en__gt=timezone.now()).order_by('vence_en', 'id')


def evaluar(cotizacion: Cotizacion, referido: Optional[Referido] = None) -> Resultado:
    """Qué pasaría al aplicar el beneficio, sin escribir nada."""
    return _decidir(cotizacion, referido, escribir=False)


@transaction.atomic
def aplicar(cotizacion: Cotizacion, referido: Optional[Referido] = None, usuario=None) -> Resultado:
    """Aplica el beneficio si es lo que más ahorra al cliente; si no, lo conserva. Un solo uso."""
    return _decidir(cotizacion, referido, escribir=True, usuario=usuario)


def _decidir(cotizacion, referido, *, escribir, usuario=None) -> Resultado:
    cotizacion = Cotizacion.objects.select_for_update().get(pk=cotizacion.pk) if escribir else cotizacion
    if cotizacion.es_historica or cotizacion.estado not in ESTADOS_APLICABLES:
        return Resultado(False, 'La cotización ya no admite cambios de precio (solo borrador o cotizada).')
    if cotizacion.tipo_servicio not in BENEFICIOS:
        return Resultado(False, 'Este servicio no tiene beneficio de referido.')
    candidatos = vigentes_de(cotizacion.cliente)
    if escribir:
        candidatos = candidatos.select_for_update()
    ref = candidatos.filter(pk=referido.pk).first() if referido is not None else candidatos.first()
    if ref is None:
        return Resultado(False, 'El cliente no tiene un beneficio de referido vigente.')
    descuento, ids = descuento_de(cotizacion.tipo_servicio)
    if not ids:
        return Resultado(False, 'No hay productos de la renta/paquete/habitación configurados para este beneficio.', referido=ref)
    monto = DescuentoService.monto_potencial(cotizacion, descuento)
    if monto <= 0:
        return Resultado(False, 'La cotización no incluye el concepto sobre el que aplica el beneficio.', referido=ref)

    cot = Cotizacion.objects.get(pk=cotizacion.pk)
    activos = list(cot.descuentos_aplicados.filter(activo=True).select_related('descuento'))
    existente = cot.descuento or CERO
    if any(a.descuento.es_cortesia for a in activos):
        return Resultado(False, 'La cotización tiene una cortesía; no se mezcla con un referido.', monto, ref)
    if existente >= monto:
        return Resultado(False, f'Otra promoción ahorra más (${existente:,.2f} contra ${monto:,.2f}); el beneficio se conserva.',
                         monto, ref)
    capturado_a_mano = existente - sum((a.monto_aplicado for a in activos), CERO)
    if capturado_a_mano > 0:
        return Resultado(False, 'La cotización tiene un descuento capturado a mano; revísalo antes de aplicar el referido.',
                         monto, ref)
    if not escribir:
        return Resultado(True, 'Se puede aplicar.', monto, ref)

    for a in activos:
        DescuentoService.revertir(a)
    cot.refresh_from_db()
    aplicado = DescuentoService.aplicar(cot, descuento, usuario=usuario, modo='MANUAL')
    ahora = timezone.now()
    ref.estado = Referido.USADO
    ref.usado_en = ahora
    ref.usado_en_cotizacion = cot
    ref.monto_beneficio = aplicado.monto_aplicado
    ref.updated_by = usuario if getattr(usuario, 'pk', None) else None
    ref.save(update_fields=['estado', 'usado_en', 'usado_en_cotizacion', 'monto_beneficio', 'updated_by', 'updated_at'])
    return Resultado(True, 'Beneficio aplicado.', aplicado.monto_aplicado, ref)


def avisar_beneficio_disponible(cotizacion: Cotizacion) -> None:
    """Al crearse una cotización de quien tiene un beneficio vigente, deja un pendiente para que una persona decida."""
    from comunicacion.services_pendientes import registrar_pendiente

    if cotizacion.es_historica or cotizacion.tipo_servicio not in BENEFICIOS or not vigentes_de(cotizacion.cliente).exists():
        return
    registrar_pendiente(
        titulo=f'Beneficio de referido disponible para COT-{cotizacion.pk:03d}',
        rol='DIRECCION', tipo='OTRO', severidad='NORMAL', objeto=cotizacion,
        detalle='El cliente ganó un beneficio por recomendar. Aplícalo desde la lista de Cotizaciones (acción «Aplicar '
                'beneficio de referido»); solo se aplica si es mejor que otra promoción.',
        clave_idempotencia=f'referido:disponible:{cotizacion.pk}')


# ───────────────────────────── Avisos ──────────────────────────────────────

def avisar_ganado(referido: Referido) -> bool:
    """Texto fijo al recomendador (WhatsApp y correo), solo si aceptó MARKETING y no pidió la baja. Si no se puede,
    abre un pendiente para que alguien le avise. Nunca lanza. Devuelve True si salió algún canal."""
    from django.conf import settings

    from comunicacion.services import enviar_email, enviar_whatsapp, enviar_whatsapp_template
    from comunicacion.services_encuesta import _motivo_marketing
    from comunicacion.services_pendientes import registrar_pendiente, ventana_abierta

    enviado = False
    try:
        cliente = referido.recomendador
        vence = timezone.localtime(referido.vence_en).strftime('%d/%m/%Y')
        pila = cliente.nombre.split()[0] if cliente.nombre else ''
        texto = bloques(
            f'¡Hola{" " + pila if pila else ""}! Quien nos recomendaste ya apartó su fecha.',
            f'Ganaste un beneficio para tu próxima reservación, válido hasta el {vence}.',
            'Cuando vayas a cotizar, avísanos y lo aplicamos si es la mejor promoción disponible.')
        if not _motivo_marketing(cliente):
            telefono = normalizar_telefono_wa(cliente.telefono)
            if ventana_abierta(telefono):
                comm = enviar_whatsapp(tipo='REFERIDO', telefono=telefono, mensaje=texto,
                                       clave_idempotencia=f'referido:{referido.pk}:whatsapp')
            elif getattr(settings, 'META_WA_PLANTILLA_REFERIDO', ''):
                comm = enviar_whatsapp_template(
                    tipo='REFERIDO', telefono=telefono, template_name=settings.META_WA_PLANTILLA_REFERIDO,
                    parametros=[pila or 'Hola', vence],
                    clave_idempotencia=f'referido:{referido.pk}:whatsapp')
            else:
                comm = None
            enviado = comm is not None and comm.estado != 'FALLIDO'
            if cliente.email:
                comm_mail = enviar_email(
                    tipo='REFERIDO', destinatario=cliente.email, asunto='Ganaste un beneficio por recomendarnos',
                    template='comunicacion/email/referido.html', context={'cliente': cliente, 'vence': vence},
                    clave_idempotencia=f'referido:{referido.pk}:email')
                enviado = enviado or (comm_mail is not None and comm_mail.estado != 'FALLIDO')
        if enviado:
            Referido.objects.filter(pk=referido.pk).update(notificado_en=timezone.now())
        else:
            registrar_pendiente(
                titulo=f'Avisar al cliente que ganó un beneficio ({referido.codigo})', rol='DIRECCION', tipo='OTRO',
                severidad='NORMAL', detalle='No se pudo avisar solo (sin consentimiento, con baja, sin ventana de '
                                            'WhatsApp ni plantilla). Avísale por otro medio.',
                clave_idempotencia=f'referido:{referido.pk}:avisar')
    except Exception:
        logger.exception('Marketing: no se pudo avisar el referido ganado')
    return enviado
