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
from .formato_wa import Aviso
from .models import HorarioVisita, Pendiente, Visita
from .services import (
    enviar_whatsapp,
    enviar_whatsapp_botones,
    enviar_whatsapp_template,
    normalizar_telefono_wa,
    texto_plano_wa,
)
from .services_responsables import telefono_de, telefonos_aviso

logger = logging.getLogger(__name__)

MIN_ANTICIPACION = timedelta(hours=3)   # visitas del mismo día salen del chat: las ve una persona
HORIZONTE_DIAS = 14
MAX_POR_DIA = 3                         # cuántos horarios de un mismo día se ofrecen
MAX_OFRECIDOS = 9
PLAZO_CONFIRMAR = timedelta(hours=4)
# Recordatorios del mismo día: al cliente 2 h antes (solo si se confirmó antes
# de eso; si no, la confirmación misma es reciente) y al equipo 45 min antes.
PREVIO_CLIENTE = timedelta(hours=2)
PREVIO_EQUIPO = timedelta(minutes=45)
COMO_AVISAR_LLEGADA = 'Cuando llegues, escríbeme «ya llegué» por aquí y le aviso al equipo para que te reciba.'
RE_BOTON = re.compile(r'^vis:(\d+):(ok|no)$')
# «Ya llegué» del recordatorio del mismo día: lo toca el cliente, no el equipo.
RE_BOTON_LLEGADA = re.compile(r'^lleg:(\d+)$')
MENSAJE_LLEGADA = '¡Bienvenido! Ya le avisé al equipo: en un momento alguien te recibe en la entrada.'
DIAS = ('lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo')
MESES = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre',
         'octubre', 'noviembre', 'diciembre')


# ───────────────────────────── Horarios ──────────────────────────────────────

def etiqueta(inicio) -> str:
    """«martes 27 de octubre, 4:00 p.m.» en hora local."""
    local = timezone.localtime(inicio)
    return f'{DIAS[local.weekday()]} {local.day} de {MESES[local.month - 1]}, {formato_hora_ampm(local.time())}'


def hora(inicio) -> str:
    return formato_hora_ampm(timezone.localtime(inicio).time())


def telefono_legible(telefono: str) -> str:
    """«999 123 4567» con los últimos 10 dígitos, para que el equipo marque directo."""
    digitos = ''.join(filter(str.isdigit, telefono or ''))[-10:]
    return f'{digitos[:3]} {digitos[3:6]} {digitos[6:]}' if len(digitos) == 10 else (telefono or '—')


def _clave_aviso(visita, evento: str) -> str:
    return f'visita:{visita.pk}:{evento}'


def _datos_equipo(visita) -> list:
    return [f'{visita.personas} personas' if visita.personas else '', f'Tel. {telefono_legible(visita.telefono)}']


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
    texto = (texto or '').strip()
    return bool(RE_BOTON.match(texto) or RE_BOTON_LLEGADA.match(texto))


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
        texto = Aviso(titulo=f'Visita por confirmar: {visita.nombre}', categoria='Visita',
                      lineas=[cuando.capitalize(), *_datos_equipo(visita)],
                      cierre='¿La recibes? Al confirmar le aviso al cliente.', enlace=_admin_url(visita))
        botones = [(id_boton(visita.pk, 'ok'), 'Confirmar'), (id_boton(visita.pk, 'no'), 'No puedo')]
        for destino in telefonos_aviso('ENCARGADO'):
            services_pendientes._enviar_aviso(destino, texto, botones=botones,
                                              clave=_clave_aviso(visita, f'aviso:{destino}'))
    except Exception:
        logger.exception("Visitas: no se pudo avisar al encargado de la visita %s", visita.pk)


# ───────────────────────────── Decidir ───────────────────────────────────────

def _avisar_cliente(visita, texto: str, evento: str, botones=None) -> None:
    """Texto libre (con `botones`, si los hay) si la ventana de 24 h está
    abierta; si no, la plantilla de visitas sin botones (o, sin plantilla, texto
    libre, que Meta puede rechazar). Queda en la conversación como mensaje
    automático para que Kooxi sepa qué pasó."""
    try:
        clave = _clave_aviso(visita, evento)
        plantilla = getattr(settings, 'META_WA_PLANTILLA_VISITA', '') or ''
        abierta = services_pendientes.ventana_abierta(visita.telefono)
        if botones and abierta:
            comm = enviar_whatsapp_botones(tipo='OTRO', telefono=visita.telefono, mensaje=texto,
                                           botones=botones, clave_idempotencia=clave)
        elif plantilla and not abierta:
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
        _avisar_cliente(visita, _texto_confirmada(visita), 'confirmada')
        recordatorios = _plan_recordatorios(visita)
        _avisar_equipo(visita, Aviso(
            titulo=f'Visita confirmada: {visita.nombre}', categoria='Visita',
            lineas=[cuando.capitalize(), *_datos_equipo(visita)], cierre=recordatorios,
            enlace=_admin_url(visita)), 'confirmada_equipo', excepto=quien)
        return (f'Listo, quedó confirmada la visita de {visita.nombre}: {cuando}. '
                f'Ya le avisé y le pedí que me escriba al llegar. {recordatorios}')
    _avisar_cliente(
        visita, f'No pudimos confirmar tu visita del {cuando}.\n\nEscríbenos por aquí y te proponemos otro horario.',
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


def _atender_llegada(telefono: str, visita_id: int) -> Optional[str]:
    """«Ya llegué» del recordatorio: solo vale desde el teléfono de esa visita,
    viva y de hoy. Avisa al equipo igual que si lo escribiera."""
    from .models import ConversacionWhatsApp
    from .services_llegada import avisar_llegada
    visita = Visita.objects.filter(pk=visita_id, telefono=telefono, estado__in=Visita.ACTIVAS,
                                   inicio__date=timezone.localdate()).first()
    conv = ConversacionWhatsApp.objects.filter(telefono=telefono).first()
    if visita is None or conv is None:
        return None
    avisar_llegada(conv, 'ya_llego')
    return MENSAJE_LLEGADA


def atender_boton(telefono: str, texto: str) -> Optional[str]:
    """Botón `vis:<id>:ok|no`: solo vale desde el teléfono activo del ENCARGADO o
    de DIRECCION. `lleg:<id>`: solo desde el teléfono del cliente de esa visita.
    Devuelve el texto con que contestarle, o None si no hizo nada."""
    texto = (texto or '').strip()
    telefono = normalizar_telefono_wa(telefono)
    llegada = RE_BOTON_LLEGADA.match(texto)
    if llegada:
        return _atender_llegada(telefono, int(llegada.group(1))) if telefono else None
    coincide = RE_BOTON.match(texto)
    if not coincide:
        return None
    autorizados = {telefono_de('ENCARGADO'), telefono_de('DIRECCION')} - {''}
    if not telefono or telefono not in autorizados:
        return None
    return decidir(int(coincide.group(1)), coincide.group(2) == 'ok', quien=telefono)


# ───────────────────────────── Recordatorios ─────────────────────────────────

def _texto_confirmada(visita) -> str:
    from .services_notificaciones import DOMICILIO_QKT, MAPS_URL_QKT
    nombre = visita.nombre.split()[0].title() if visita.nombre else ''
    return (f"¡Listo{', ' + nombre if nombre else ''}! Tu visita a Quinta Ko'ox Tanil quedó confirmada 🌴\n\n"
            f'*{etiqueta(visita.inicio).capitalize()}*\n'
            f'{DOMICILIO_QKT}\n{MAPS_URL_QKT}\n\n'
            f'{COMO_AVISAR_LLEGADA} Si necesitas cambiarla, escríbenos por aquí.')


def _vispera_pendiente(visita, ahora) -> bool:
    """El recordatorio de la víspera (cron de las 18:00 del día anterior) aún no pasa."""
    dia_antes = timezone.localdate(visita.inicio) - timedelta(days=1)
    corte = timezone.make_aware(datetime.combine(dia_antes, datetime.min.time()) + timedelta(hours=18),
                                timezone.get_current_timezone())
    return ahora < corte


def _plan_recordatorios(visita, ahora=None) -> str:
    """Qué recordatorios saldrán según cuánto falta (se dice al confirmar)."""
    ahora = ahora or timezone.now()
    vispera = _vispera_pendiente(visita, ahora)
    equipo = (['la víspera'] if vispera else []) + (
        ['45 min antes'] if visita.inicio - PREVIO_EQUIPO > ahora else [])
    cliente = (['la víspera'] if vispera else []) + (
        ['2 h antes'] if visita.inicio - PREVIO_CLIENTE > ahora else [])
    partes = []
    if equipo:
        partes.append(f"a ti, {' y '.join(equipo)}")
    if cliente:
        partes.append(f"al cliente, {' y '.join(cliente)}")
    return f"Recordatorios: {'; '.join(partes)}." if partes else 'Ya no da tiempo de recordatorios: es en breve.'


def _avisar_equipo(visita, aviso: Aviso, evento: str, excepto: str = '') -> int:
    """Aviso a los números del encargado (y Dirección con copia en vivo), uno
    por número y evento. Devuelve a cuántos salió."""
    enviados = 0
    excepto = normalizar_telefono_wa(excepto) if excepto else ''
    for destino in telefonos_aviso('ENCARGADO'):
        if destino == excepto:
            continue
        comm = services_pendientes._enviar_aviso(destino, aviso, clave=_clave_aviso(visita, f'{evento}:{destino}'))
        enviados += int(comm is not None)
    return enviados


def llego(visita) -> bool:
    """El cliente ya avisó por el chat que llegó o va en camino."""
    from django.contrib.contenttypes.models import ContentType
    return Pendiente.objects.filter(tipo='LLEGADA', content_type=ContentType.objects.get_for_model(Visita),
                                    object_id=visita.pk).exists()


def enviar_recordatorios(hoy=None) -> int:
    """Víspera: recuerda al cliente su visita CONFIRMADA de mañana (una sola vez)
    y le manda al equipo un resumen con las visitas de mañana."""
    hoy = hoy or timezone.localdate()
    manana = hoy + timedelta(days=1)
    enviados = 0
    for visita in Visita.objects.filter(estado='CONFIRMADA', recordatorio_en__isnull=True,
                                        inicio__date=manana).order_by('inicio'):
        if not Visita.objects.filter(pk=visita.pk, recordatorio_en__isnull=True).update(
                recordatorio_en=timezone.now()):
            continue
        _avisar_cliente(
            visita, f"Recordatorio: mañana te esperamos en Quinta Ko'ox Tanil para tu visita 🌴\n\n"
                    f'*{etiqueta(visita.inicio).capitalize()}*\n\n'
                    f'{COMO_AVISAR_LLEGADA} Si no puedes venir, avísanos por aquí.', 'recordatorio')
        enviados += 1
    _resumen_equipo(manana)
    return enviados


def _resumen_equipo(dia) -> None:
    visitas = list(Visita.objects.filter(estado__in=Visita.ACTIVAS, inicio__date=dia).order_by('inicio'))
    if not visitas:
        return
    lineas = [f"{hora(v.inicio)} · {v.nombre}{f' ({v.personas} personas)' if v.personas else ''}"
              f"{'' if v.estado == 'CONFIRMADA' else ' · POR CONFIRMAR'}" for v in visitas]
    total = len(visitas)
    aviso = Aviso(titulo=f"Mañana {'tienes 1 visita' if total == 1 else f'tienes {total} visitas'}",
                  categoria='Visita', lineas=lineas,
                  cierre='Te aviso 45 min antes de cada una y cuando el cliente me diga que llegó.',
                  enlace=(getattr(settings, 'ERP_PORTAL_URL', '') or '').rstrip('/')
                  + reverse('admin:comunicacion_visita_changelist'))
    for destino in telefonos_aviso('ENCARGADO'):
        services_pendientes._enviar_aviso(destino, aviso, clave=f'visitas:{dia}:resumen:{destino}')


def enviar_avisos_previos(ahora=None) -> int:
    """El mismo día: al cliente 2 h antes (si se confirmó antes de eso) y al
    equipo 45 min antes, salvo que el cliente ya haya avisado que llegó. Cada
    aviso sale una vez (clave de idempotencia). Devuelve cuántos salieron."""
    ahora = ahora or timezone.now()
    enviados = 0
    for visita in Visita.objects.filter(estado='CONFIRMADA', inicio__gt=ahora,
                                        inicio__lte=ahora + PREVIO_CLIENTE).order_by('inicio'):
        if llego(visita):
            continue
        confirmada_a_tiempo = (visita.decidida_en or ahora) <= visita.inicio - PREVIO_CLIENTE
        if confirmada_a_tiempo:
            _avisar_cliente(
                visita, f"¡Hoy es tu visita a Quinta Ko'ox Tanil! Te esperamos a las *{hora(visita.inicio)}* 🌴\n\n"
                        'Cuando llegues, toca «Ya llegué» o escríbemelo por aquí y le aviso al equipo para que te reciba.', 'previo',
                botones=[(f'lleg:{visita.pk}', 'Ya llegué')])
        if visita.inicio - ahora <= PREVIO_EQUIPO:
            minutos = max(1, int((visita.inicio - ahora).total_seconds() // 60))
            enviados += _avisar_equipo(visita, Aviso(
                titulo=f'En {minutos} min llega {visita.nombre} a su visita', categoria='Visita', urgente=True,
                lineas=[f'Hora: {hora(visita.inicio)}', *_datos_equipo(visita)],
                cierre='Que alguien esté listo para recibirla. Te aviso si me escribe que ya llegó.',
                enlace=_admin_url(visita)), 'previo_equipo')
    return enviados
