"""
Bandeja de pendientes (Issue #422, fase 1b del plan de piloto automático).

Fuente única de «qué requiere a una persona»: cada alerta interna deja un
`Pendiente` con responsable, severidad y plazo. Lo URGENTE se avisa al momento
(con botones si la ventana de 24 h está abierta, plantilla con enlace si no);
lo NORMAL espera al resumen diario; lo que el encargado no atiende a tiempo
escala a Dirección. Nada aquí lanza: un fallo de WhatsApp o de la propia
bandeja no debe tumbar la operación que originó el aviso.
"""
import logging
import re
from collections import Counter
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from .formato_wa import Aviso
from .models import MensajeWhatsApp, Pendiente
from .services import (
    enviar_whatsapp,
    enviar_whatsapp_botones,
    enviar_whatsapp_template,
    normalizar_telefono_wa,
)
from .services_responsables import SIN_DESTINO, responsable, rol_de_telefono, telefono_de, telefonos_aviso

logger = logging.getLogger(__name__)

PLAZO_HORAS = {'URGENTE': 2, 'NORMAL': 24}
ROLES_RESUMEN = ('ENCARGADO', 'DIRECCION', 'CONTADOR', 'COMPRAS')
VENTANA_WA = timedelta(hours=24)
INTERVALO_COPIA = timedelta(hours=1)
MAS_VIEJOS = 5

BOTON_TOMAR = 'tomar'
BOTON_OK = 'ok'
RE_BOTON = re.compile(r'^pend:(\d+):(tomar|ok)$')


def id_boton(pendiente_id: int, accion: str) -> str:
    return f'pend:{pendiente_id}:{accion}'


def es_boton(texto: str) -> bool:
    return bool(RE_BOTON.match((texto or '').strip()))


def _base_url() -> str:
    return (getattr(settings, 'ERP_PORTAL_URL', '') or '').rstrip('/')


def url_pendiente(pendiente) -> str:
    return _base_url() + reverse('admin:comunicacion_pendiente_change', args=[pendiente.pk])


def url_bandeja() -> str:
    return _base_url() + reverse('admin:comunicacion_pendiente_changelist') + '?estado__exact=ABIERTO'


def ventana_abierta(telefono: str) -> bool:
    """El destinatario escribió en las últimas 24 h: ya admite texto libre y botones."""
    return MensajeWhatsApp.objects.filter(
        conversacion__telefono=normalizar_telefono_wa(telefono), direccion='ENTRADA',
        created_at__gte=timezone.now() - VENTANA_WA).exists()


def _enviar_aviso(telefono: str, aviso: Aviso, *, botones=None, clave: Optional[str] = None):
    """Botones si la ventana está abierta; si no, una plantilla. `aviso` es un
    `Aviso` (formato en bloques y la plantilla del equipo de 4 parámetros). Sin
    plantilla del equipo va la de operaciones con el texto aplanado, y sin
    ninguna, texto libre. Nunca lanza."""
    try:
        mensaje = aviso.texto()
        abierta = ventana_abierta(telefono)
        if botones and abierta:
            comm = enviar_whatsapp_botones(tipo='OTRO', telefono=telefono, mensaje=mensaje, botones=botones)
            if comm is not None and comm.estado != 'FALLIDO':
                return comm
        if abierta:
            return enviar_whatsapp(tipo='OTRO', telefono=telefono, mensaje=mensaje, clave_idempotencia=clave)
        plantilla_equipo = getattr(settings, 'META_WA_PLANTILLA_AVISO_EQUIPO', '') or ''
        if plantilla_equipo:
            return enviar_whatsapp_template(
                tipo='OTRO', telefono=telefono, template_name=plantilla_equipo,
                parametros=aviso.parametros(), clave_idempotencia=clave)
        plantilla = getattr(settings, 'META_WA_PLANTILLA_OPERACIONES', '') or ''
        if plantilla:
            plano = aviso.plano()
            return enviar_whatsapp_template(
                tipo='OTRO', telefono=telefono, template_name=plantilla,
                parametros=[plano], clave_idempotencia=clave)
        return enviar_whatsapp(tipo='OTRO', telefono=telefono, mensaje=mensaje, clave_idempotencia=clave)
    except Exception:
        logger.exception("Pendientes: no se pudo avisar por WhatsApp")
        return None


def aviso_de(pendiente, *, urgente: bool = True, cierre: str = '') -> Aviso:
    """El pendiente en bloques: tipo, título, cada renglón del detalle y su enlace."""
    lineas = [linea for linea in (pendiente.detalle or '').splitlines() if linea.strip()]
    return Aviso(titulo=pendiente.titulo, categoria=pendiente.get_tipo_display(), urgente=urgente,
                 lineas=[linea[:300] for linea in lineas[:8]], enlace=url_pendiente(pendiente), cierre=cierre)


def _avisar_urgente(pendiente, botones=None, etiqueta_ok: str = 'Resuelto') -> None:
    """Aviso inmediato al rol y, si tiene la copia en vivo, a Dirección. Con
    `botones` el responsable recibe esos (p. ej. aprobar/rechazar) en vez de
    «Resuelto» (o `etiqueta_ok`); la copia a Dirección sigue con «Intervengo»."""
    destinos = telefonos_aviso(pendiente.rol)
    if not destinos:
        logger.error("%s; el pendiente %s queda solo en la bandeja", SIN_DESTINO, pendiente.pk)
        return
    principal = telefono_de(pendiente.rol)
    aviso = aviso_de(pendiente)
    for destino in destinos:
        if botones and destino == principal:
            _enviar_aviso(destino, aviso, botones=botones)
            continue
        accion = BOTON_OK if destino == principal else BOTON_TOMAR
        etiqueta = etiqueta_ok if accion == BOTON_OK else 'Intervengo'
        _enviar_aviso(destino, aviso, botones=[(id_boton(pendiente.pk, accion), etiqueta)])


def registrar_pendiente(*, titulo: str, rol: str, tipo: str = 'ALERTA', severidad: str = 'NORMAL',
                        detalle: str = '', objeto=None, vence_en=None,
                        clave_idempotencia: Optional[str] = None, usuario=None,
                        botones=None, etiqueta_ok: str = 'Resuelto') -> Optional[Pendiente]:
    """Deja un pendiente en la bandeja. Idempotente por clave (repetirla devuelve
    el existente sin avisar de nuevo) y nunca lanza: devuelve None si no pudo.
    Cada renglón de `detalle` sale como un dato aparte en el aviso."""
    try:
        if clave_idempotencia:
            existente = Pendiente.objects.filter(clave_idempotencia=clave_idempotencia).first()
            if existente is not None:
                return existente
        ahora = timezone.now()
        datos = dict(
            tipo=tipo, severidad=severidad, rol=rol, titulo=(titulo or 'Pendiente')[:200],
            detalle=detalle or '', vence_en=vence_en or ahora + timedelta(hours=PLAZO_HORAS[severidad]),
            clave_idempotencia=clave_idempotencia or None,
            created_by=usuario if getattr(usuario, 'pk', None) else None,
        )
        if objeto is not None and getattr(objeto, 'pk', None):
            datos.update(content_type=ContentType.objects.get_for_model(objeto), object_id=objeto.pk)
        try:
            with transaction.atomic():
                pendiente = Pendiente.objects.create(**datos)
        except IntegrityError:
            return Pendiente.objects.filter(clave_idempotencia=clave_idempotencia).first()
    except Exception:
        logger.exception("Pendientes: no se pudo registrar «%s»", titulo)
        return None
    if severidad == 'URGENTE':
        _avisar_urgente(pendiente, botones, etiqueta_ok)
    return pendiente


# ───────────────────────────── Cambios de estado ──────────────────────────────

def _cerrar(pendiente, estado: str, usuario=None) -> bool:
    ahora = timezone.now()
    usuario = usuario if getattr(usuario, 'pk', None) else None
    filas = Pendiente.objects.filter(pk=pendiente.pk, estado='ABIERTO').update(
        estado=estado, resuelto_en=ahora, resuelto_por=usuario, updated_by=usuario, updated_at=ahora)
    if filas:
        pendiente.refresh_from_db()
    return bool(filas)


def resolver(pendiente, usuario=None) -> bool:
    return _cerrar(pendiente, 'RESUELTO', usuario)


def descartar(pendiente, usuario=None) -> bool:
    return _cerrar(pendiente, 'DESCARTADO', usuario)


def resolver_de_objeto(objeto, usuario=None) -> int:
    """Cierra los pendientes abiertos ligados a `objeto` (p. ej. una conversación
    ya atendida). Devuelve cuántos cerró."""
    if objeto is None or not getattr(objeto, 'pk', None):
        return 0
    ahora = timezone.now()
    usuario = usuario if getattr(usuario, 'pk', None) else None
    return Pendiente.objects.filter(
        estado='ABIERTO', content_type=ContentType.objects.get_for_model(objeto), object_id=objeto.pk,
    ).update(estado='RESUELTO', resuelto_en=ahora, resuelto_por=usuario, updated_by=usuario, updated_at=ahora)


def tomar(pendiente, usuario=None) -> bool:
    """Dirección interviene: el pendiente pasa a su rol y se le avisa al responsable anterior."""
    rol_previo = pendiente.rol
    usuario = usuario if getattr(usuario, 'pk', None) else None
    filas = Pendiente.objects.filter(pk=pendiente.pk, estado='ABIERTO').exclude(rol='DIRECCION').update(
        rol='DIRECCION', tomado_por_direccion=True, updated_by=usuario, updated_at=timezone.now())
    if not filas:
        return False
    pendiente.refresh_from_db()
    destino = telefono_de(rol_previo)
    if destino:
        _enviar_aviso(destino, Aviso(titulo='Lo tomó Dirección', categoria='Pendiente', lineas=[pendiente.titulo],
                                     cierre='Ya no necesitas atenderlo.'),
                      clave=f'pend:{pendiente.pk}:tomado')
    return True


def atender_boton(telefono: str, texto: str) -> Optional[str]:
    """Respuesta a un botón `pend:<id>:<acción>`. Solo vale si el teléfono que
    responde es el responsable de ese rol (Intervengo: Dirección). Devuelve el
    texto con que contestarle, o None si no hizo nada."""
    coincide = RE_BOTON.match((texto or '').strip())
    if not coincide:
        return None
    pendiente = Pendiente.objects.filter(pk=int(coincide.group(1))).first()
    rol = rol_de_telefono(telefono)
    if pendiente is None or rol is None:
        return None
    if coincide.group(2) == BOTON_TOMAR:
        if rol == 'DIRECCION' and tomar(pendiente):
            return f'✅ Listo, tomaste el pendiente: {pendiente.titulo}.'
        return None
    if rol == pendiente.rol and resolver(pendiente):
        return f'✅ Listo, quedó resuelto: {pendiente.titulo}.'
    return None


# ───────────────────────────── Cron: escalar y copiar ─────────────────────────

def escalar_vencidos(ahora=None) -> int:
    """Un pendiente abierto del encargado con el plazo vencido pasa a Dirección,
    una sola vez, con aviso urgente."""
    ahora = ahora or timezone.now()
    escalados = 0
    for pendiente in Pendiente.objects.filter(estado='ABIERTO', rol='ENCARGADO', escalado_en__isnull=True,
                                              vence_en__lt=ahora):
        filas = Pendiente.objects.filter(
            pk=pendiente.pk, estado='ABIERTO', rol='ENCARGADO', escalado_en__isnull=True,
        ).update(rol='DIRECCION', escalado_en=ahora, updated_at=ahora)
        if not filas:
            continue
        escalados += 1
        pendiente.refresh_from_db()
        destino = telefono_de('DIRECCION')
        if destino:
            _enviar_aviso(
                destino, aviso_de(pendiente, cierre='El encargado no lo atendió a tiempo.'),
                botones=[(id_boton(pendiente.pk, BOTON_OK), 'Resuelto')],
                clave=f'pend:{pendiente.pk}:escalado')
    return escalados


def copiar_cada_hora(ahora=None) -> int:
    """Si Dirección tiene la copia «cada hora», una vez por hora le llega en un
    solo mensaje lo nuevo del encargado. Devuelve cuántos pendientes incluyó."""
    ahora = ahora or timezone.now()
    direccion = responsable('DIRECCION')
    if direccion is None or direccion.modo_copia != 'CADA_HORA':
        return 0
    if Pendiente.objects.filter(copiado_en__gte=ahora - INTERVALO_COPIA).exists():
        return 0
    nuevos = list(Pendiente.objects.filter(estado='ABIERTO', rol='ENCARGADO', copiado_en__isnull=True)
                  .order_by('created_at', 'id'))
    if not nuevos:
        return 0
    lineas = [p.titulo[:70] for p in nuevos[:MAS_VIEJOS]]
    if len(nuevos) > MAS_VIEJOS:
        lineas.append(f'y {len(nuevos) - MAS_VIEJOS} más')
    aviso = Aviso(titulo=f'{len(nuevos)} pendientes nuevos del encargado', categoria='Copia por hora',
                  lineas=lineas, enlace=url_bandeja())
    botones = [(id_boton(nuevos[0].pk, BOTON_TOMAR), 'Intervengo')] if len(nuevos) == 1 else None
    comm = _enviar_aviso(telefono_de('DIRECCION'), aviso, botones=botones,
                         clave=f'pend:copia:{ahora:%Y%m%d%H}')
    if comm is None or comm.estado == 'FALLIDO':
        return 0
    Pendiente.objects.filter(pk__in=[p.pk for p in nuevos]).update(copiado_en=ahora)
    return len(nuevos)


# ───────────────────────────── Resumen diario ─────────────────────────────────

def _resumen_de(abiertos, resueltos_ayer, operaciones, hoy, programador='') -> Aviso:
    lineas = []
    if abiertos:
        conteo = ', '.join(f'{n} {tipo}' for tipo, n in Counter(p.get_tipo_display() for p in abiertos).items())
        lineas.append(f'Pendientes abiertos: {len(abiertos)} ({conteo})')
        lineas.extend(f'{p.titulo[:60]} ({timezone.localtime(p.created_at):%d/%m})' for p in abiertos[:MAS_VIEJOS])
    if operaciones:
        lineas.append(f'Operación: {operaciones}')
    if resueltos_ayer:
        lineas.append(f'Resueltos ayer: {resueltos_ayer}')
    if programador:
        lineas.append(programador)
    return Aviso(titulo=f'Resumen del {hoy:%d/%m}', categoria='Resumen diario', lineas=lineas, enlace=url_bandeja())


def enviar_resumen_diario(hoy=None) -> int:
    """Un mensaje por responsable con sus pendientes abiertos (conteo por tipo y
    los más viejos); a Dirección además los resueltos de ayer, y al encargado el
    resumen de operaciones. Idempotente por día y teléfono. Devuelve cuántos salieron."""
    from operaciones.services import marcar_resumen_enviado, resumen_operaciones
    from operaciones.services_programador import texto_ultima_vuelta

    hoy = hoy or timezone.localdate()
    por_telefono = {}
    for rol in ROLES_RESUMEN:
        telefono = telefono_de(rol, hoy)
        if telefono:
            por_telefono.setdefault(telefono, []).append(rol)

    enviados = 0
    for telefono, roles in por_telefono.items():
        abiertos = list(Pendiente.objects.filter(estado='ABIERTO', rol__in=roles).order_by('created_at', 'id'))
        resueltos_ayer = (Pendiente.objects.filter(estado='RESUELTO', resuelto_en__date=hoy - timedelta(days=1))
                          .count() if 'DIRECCION' in roles else 0)
        tareas, operaciones = resumen_operaciones(hoy) if 'ENCARGADO' in roles else ([], '')
        if not (abiertos or resueltos_ayer or operaciones):
            continue
        programador = texto_ultima_vuelta() if 'DIRECCION' in roles else ''
        comm = _enviar_aviso(telefono, _resumen_de(abiertos, resueltos_ayer, operaciones, hoy, programador),
                             clave=f'pend:resumen:{hoy}:{telefono}')
        if comm is None:
            continue
        if comm.estado != 'FALLIDO':
            enviados += 1
        if tareas:
            marcar_resumen_enviado(tareas, 'FALLIDO' if comm.estado == 'FALLIDO' else 'ENVIADO')
    return enviados
