"""
Agenda de visitas a la Quinta (Fase 2a del plan de piloto automático).

Kooxi ofrece los horarios que el encargado deja en Admin → Horarios y agenda
la cita, pero **nunca la confirma**: nace «por confirmar» y solo el encargado
(o Dirección) la confirma con un botón de WhatsApp o desde el admin. Al decidir
se le avisa al cliente y la víspera se le recuerda. Nada de aquí lanza hacia
Kooxi o hacia el webhook: un fallo de WhatsApp no debe tumbar la cita.
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from core_erp.horarios import formato_hora_ampm

from . import services_pendientes
from .models import HorarioVisita, Pendiente, Visita
from .services import enviar_whatsapp, enviar_whatsapp_template, normalizar_telefono_wa, texto_plano_wa
from .services_responsables import telefono_de, telefonos_aviso

logger = logging.getLogger(__name__)

MIN_ANTICIPACION = timedelta(hours=3)   # visitas del mismo día salen del chat: las ve una persona
HORIZONTE_DIAS = 14
MAX_POR_DIA = 3                         # cuántos horarios de un mismo día se ofrecen
MAX_OFRECIDOS = 9
PLAZO_CONFIRMAR = timedelta(hours=4)
RE_BOTON = re.compile(r'^vis:(\d+):(ok|no)$')
DIAS = ('lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo')
MESES = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre',
         'octubre', 'noviembre', 'diciembre')


# ───────────────────────────── Horarios ──────────────────────────────────────

def etiqueta(inicio) -> str:
    """«martes 27 de octubre, 4:00 p.m.» en hora local."""
    local = timezone.localtime(inicio)
    return f'{DIAS[local.weekday()]} {local.day} de {MESES[local.month - 1]}, {formato_hora_ampm(local.time())}'


def _clave_aviso(visita, evento: str) -> str:
    return f'visita:{visita.pk}:{evento}'


def horarios_disponibles(ahora=None, dias: int = HORIZONTE_DIAS, limite: Optional[int] = MAX_OFRECIDOS) -> list:
    """Horarios libres (datetimes con zona), de los más próximos a los lejanos.

    Parte cada ventana activa en citas de `duracion_minutos`, descarta las que
    empiezan antes de `MIN_ANTICIPACION` y las que ya tienen una visita activa.
    Con `limite`, ofrece a lo más `MAX_POR_DIA` por día para que el cliente vea
    varios días y no solo el primero.
    """
    ahora = ahora or timezone.now()
    desde = ahora + MIN_ANTICIPACION
    hoy = timezone.localdate(ahora)
    ventanas = list(HorarioVisita.objects.filter(activo=True))
    ocupados = set(Visita.objects.filter(estado__in=Visita.ACTIVAS, inicio__gte=desde)
                   .values_list('inicio', flat=True))
    zona = timezone.get_current_timezone()
    libres = []
    for delta in range(dias + 1):
        dia = hoy + timedelta(days=delta)
        del_dia = []
        for v in ventanas:
            if v.dia_semana != dia.weekday():
                continue
            inicio = datetime.combine(dia, v.hora_inicio)
            fin = datetime.combine(dia, v.hora_fin)
            while inicio + timedelta(minutes=v.duracion_minutos) <= fin:
                cita = timezone.make_aware(inicio, zona)
                if cita >= desde and cita not in ocupados:
                    del_dia.append(cita)
                inicio += timedelta(minutes=v.duracion_minutos)
        del_dia = sorted(set(del_dia))
        libres.extend(del_dia[:MAX_POR_DIA] if limite else del_dia)
    return libres[:limite] if limite else libres


# ───────────────────────────── Agendar ───────────────────────────────────────

def _admin_url(visita) -> str:
    base = (getattr(settings, 'ERP_PORTAL_URL', '') or '').rstrip('/')
    return base + reverse('admin:comunicacion_visita_change', args=[visita.pk])


def id_boton(visita_id: int, accion: str) -> str:
    return f'vis:{visita_id}:{accion}'


def es_boton(texto: str) -> bool:
    return bool(RE_BOTON.match((texto or '').strip()))


def agendar(conv, inicio_texto, nombre, personas=None) -> dict:
    """Crea la visita POR_CONFIRMAR y avisa al encargado. Devuelve el resultado
    para el modelo (`error` si no se pudo). El horario debe ser uno de los que
    `horarios_disponibles` ofrece: lo que dicte el modelo no se acepta tal cual."""
    nombre = str(nombre or '').strip()[:200]
    if conv is None or not nombre:
        return {'error': 'Falta el nombre de quien visita: pídeselo al cliente.'}
    try:
        inicio = timezone.make_aware(datetime.strptime(str(inicio_texto or '').strip(), '%Y-%m-%dT%H:%M'),
                                     timezone.get_current_timezone())
    except ValueError:
        return {'error': 'Horario no válido: usa exactamente uno de horarios_de_visita (AAAA-MM-DDTHH:MM).'}
    if inicio not in horarios_disponibles(dias=HORIZONTE_DIAS, limite=None):
        return {'error': 'Ese horario ya no está disponible. Vuelve a consultar horarios_de_visita y ofrece otros.'}
    telefono = normalizar_telefono_wa(conv.telefono)
    if Visita.objects.filter(telefono=telefono, estado__in=Visita.ACTIVAS, inicio__gte=timezone.now()).exists():
        return {'error': 'Este cliente ya tiene una visita agendada. Si quiere cambiarla, usa pasar_a_humano.'}
    try:
        personas = int(personas) if personas else None
    except (TypeError, ValueError):
        personas = None
    try:
        with transaction.atomic():
            visita = Visita.objects.create(
                conversacion=conv, cliente=conv.cliente, nombre=nombre, telefono=telefono,
                personas=personas if personas and 0 < personas <= 500 else None, inicio=inicio)
    except IntegrityError:
        return {'error': 'Ese horario se acaba de ocupar. Vuelve a consultar horarios_de_visita.'}
    _avisar_encargado(visita)
    return {
        'ok': True, 'visita_id': visita.pk, 'estado': 'por_confirmar', 'cuando': etiqueta(inicio),
        'nota': ('La visita queda POR CONFIRMAR: el equipo debe aceptarla y el cliente recibirá su '
                 'confirmación por este chat. Díselo así; no la des por confirmada.'),
    }


def _avisar_encargado(visita) -> None:
    """Pendiente para el ENCARGADO y aviso con botones (a Dirección también si
    tiene la copia en vivo). No lleva texto del cliente, solo nombre y horario."""
    try:
        cuando = etiqueta(visita.inicio)
        personas = f' ({visita.personas} personas)' if visita.personas else ''
        services_pendientes.registrar_pendiente(
            titulo=f'Visita por confirmar: {visita.nombre}, {cuando}', rol='ENCARGADO', tipo='VISITA',
            detalle=f'{visita.nombre}{personas} pidió conocer la Quinta el {cuando}.',
            objeto=visita, vence_en=min(timezone.now() + PLAZO_CONFIRMAR, visita.inicio),
            clave_idempotencia=_clave_aviso(visita, 'por_confirmar'))
        texto = f'Visita por confirmar: {visita.nombre}{personas}, {cuando}. Abrir: {_admin_url(visita)}'
        botones = [(id_boton(visita.pk, 'ok'), 'Confirmar'), (id_boton(visita.pk, 'no'), 'No puedo')]
        for destino in telefonos_aviso('ENCARGADO'):
            services_pendientes._enviar_aviso(destino, texto, botones=botones,
                                              clave=_clave_aviso(visita, f'aviso:{destino}'))
    except Exception:
        logger.exception("Visitas: no se pudo avisar al encargado de la visita %s", visita.pk)


# ───────────────────────────── Decidir ───────────────────────────────────────

def _avisar_cliente(visita, texto: str, evento: str) -> None:
    """Texto libre si la ventana de 24 h está abierta; si no, la plantilla de
    visitas (o, sin plantilla, texto libre, que Meta puede rechazar). Queda en
    la conversación como mensaje automático para que Kooxi sepa qué pasó."""
    try:
        clave = _clave_aviso(visita, evento)
        plantilla = getattr(settings, 'META_WA_PLANTILLA_VISITA', '') or ''
        if plantilla and not services_pendientes.ventana_abierta(visita.telefono):
            comm = enviar_whatsapp_template(
                tipo='OTRO', telefono=visita.telefono, template_name=plantilla,
                parametros=[texto_plano_wa(texto)], trigger='SIGNAL', clave_idempotencia=clave)
        else:
            comm = enviar_whatsapp(tipo='OTRO', telefono=visita.telefono, mensaje=texto, clave_idempotencia=clave)
        if comm is not None and comm.estado != 'FALLIDO':
            from .services_agente import registrar_mensaje_automatico
            registrar_mensaje_automatico(telefono=visita.telefono, texto=texto, wamid=comm.proveedor_id)
    except Exception:
        logger.exception("Visitas: no se pudo avisar al cliente de la visita %s", visita.pk)


def decidir(visita_id: int, aceptar: bool, *, quien: str = '', usuario=None) -> Optional[str]:
    """Confirma o rechaza una visita POR_CONFIRMAR. Devuelve el texto con que
    contestarle a quien decidió, o None si ya estaba decidida o no existe."""
    with transaction.atomic():
        visita = Visita.objects.select_for_update().filter(pk=visita_id).first()
        if visita is None or visita.estado != 'POR_CONFIRMAR':
            return None
        usuario = usuario if getattr(usuario, 'pk', None) else None
        visita.estado = 'CONFIRMADA' if aceptar else 'RECHAZADA'
        visita.decidida_en = timezone.now()
        visita.decidida_por = (quien or (usuario.get_username() if usuario else ''))[:100]
        visita.updated_by = usuario
        visita.save(update_fields=['estado', 'decidida_en', 'decidida_por', 'updated_by', 'updated_at'])
    pendiente = Pendiente.objects.filter(clave_idempotencia=_clave_aviso(visita, 'por_confirmar')).first()
    if pendiente is not None:
        services_pendientes.resolver(pendiente, usuario)
    cuando = etiqueta(visita.inicio)
    if aceptar:
        _avisar_cliente(
            visita, f"Tu visita a Quinta Ko'ox Tanil quedó confirmada para el {cuando}. Te esperamos. "
                    'Si necesitas cambiarla, escríbenos por aquí.', 'confirmada')
        return f'Listo, quedó confirmada la visita de {visita.nombre}: {cuando}.'
    _avisar_cliente(
        visita, f'No pudimos confirmar tu visita del {cuando}. Escríbenos por aquí y te proponemos otro horario.',
        'rechazada')
    return f'Listo, avisamos a {visita.nombre} que no se puede el {cuando}. El horario quedó libre.'


def cancelar(visita_id: int, usuario=None) -> bool:
    """Cancela una visita viva (por confirmar o confirmada), libera el horario y
    avisa al cliente. False si ya estaba cancelada, rechazada o no existe."""
    with transaction.atomic():
        visita = Visita.objects.select_for_update().filter(pk=visita_id, estado__in=Visita.ACTIVAS).first()
        if visita is None:
            return False
        usuario = usuario if getattr(usuario, 'pk', None) else None
        visita.estado = 'CANCELADA'
        visita.decidida_en = timezone.now()
        visita.decidida_por = usuario.get_username()[:100] if usuario else ''
        visita.updated_by = usuario
        visita.save(update_fields=['estado', 'decidida_en', 'decidida_por', 'updated_by', 'updated_at'])
    pendiente = Pendiente.objects.filter(clave_idempotencia=_clave_aviso(visita, 'por_confirmar')).first()
    if pendiente is not None:
        services_pendientes.resolver(pendiente, usuario)
    _avisar_cliente(
        visita, f'Tu visita del {etiqueta(visita.inicio)} se canceló. Escríbenos por aquí y la reprogramamos.',
        'cancelada')
    return True


def atender_boton(telefono: str, texto: str) -> Optional[str]:
    """Botón `vis:<id>:ok|no`: solo vale desde el teléfono activo del ENCARGADO o
    de DIRECCION. Devuelve el texto con que contestarle, o None si no hizo nada."""
    coincide = RE_BOTON.match((texto or '').strip())
    if not coincide:
        return None
    telefono = normalizar_telefono_wa(telefono)
    autorizados = {telefono_de('ENCARGADO'), telefono_de('DIRECCION')} - {''}
    if not telefono or telefono not in autorizados:
        return None
    return decidir(int(coincide.group(1)), coincide.group(2) == 'ok', quien=telefono)


# ───────────────────────────── Recordatorio ──────────────────────────────────

def enviar_recordatorios(hoy=None) -> int:
    """Recuerda al cliente, el día anterior, su visita CONFIRMADA. Una sola vez."""
    hoy = hoy or timezone.localdate()
    manana = hoy + timedelta(days=1)
    enviados = 0
    for visita in Visita.objects.filter(estado='CONFIRMADA', recordatorio_en__isnull=True,
                                        inicio__date=manana).order_by('inicio'):
        if not Visita.objects.filter(pk=visita.pk, recordatorio_en__isnull=True).update(
                recordatorio_en=timezone.now()):
            continue
        _avisar_cliente(
            visita, f"Recordatorio: mañana, {etiqueta(visita.inicio)}, te esperamos en Quinta Ko'ox Tanil para "
                    'tu visita. Si no puedes venir, avísanos por aquí.', 'recordatorio')
        enviados += 1
    return enviados
