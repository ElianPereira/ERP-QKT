"""
Llegadas a la Quinta avisadas por el chat (visitas, pasadías, eventos, hospedaje).

Cuando el cliente le escribe a Kooxi «ya llegué», «estoy en la entrada», «¿cómo
entro?» o «voy en camino», Kooxi llama `avisar_llegada` en vez de darle la
ubicación o proponerle otra cita. Aquí se busca con qué viene ese número hoy
(su visita o su reservación) y se deja un pendiente URGENTE al encargado con
lo que necesita para recibirlo: quién es, a qué viene, a qué hora, cuántas
personas y, si es una reservación, lo que falta por cobrar. Sin cita ni
reservación también se avisa: alguien está en la puerta.

La identidad es el número de WhatsApp (la conversación la pone `ejecutar`,
nunca el modelo). No lleva texto del cliente, solo datos del ERP.
"""
import logging
from datetime import timedelta

from django.utils import timezone

from core_erp.horarios import formato_hora_ampm

from . import services_pendientes
from .models import Pendiente, Visita
from .services import normalizar_telefono_wa

logger = logging.getLogger(__name__)

SITUACIONES = ('ya_llego', 'en_camino')
VIGENCIA_AVISO = timedelta(hours=3)   # un mismo aviso abierto no se repite dentro de este plazo
VISITA_DESPUES = timedelta(hours=2)   # una visita de hoy sigue contando hasta 2 h después de su hora


def _visita_de_hoy(telefono: str, ahora):
    return (Visita.objects.filter(telefono=telefono, estado__in=Visita.ACTIVAS,
                                  inicio__date=timezone.localdate(ahora),
                                  inicio__gte=ahora - VISITA_DESPUES)
            .order_by('inicio').first())


def _reservacion_de_hoy(telefono: str, hoy):
    from comercial.models import Cotizacion

    from .herramientas_agente import cotizaciones_del_telefono
    for c in cotizaciones_del_telefono(telefono).exclude(estado__in=Cotizacion.ESTADOS_SIN_APARTAR):
        fin = c.fecha_salida or c.fecha_evento
        if c.fecha_evento <= hoy <= fin:
            return c
    return None


def _primer_nombre(texto: str) -> str:
    texto = (texto or '').strip()
    return texto.split()[0].title() if texto else ''


def _contexto(conv, ahora) -> dict:
    """Con qué viene hoy este número: visita, reservación o nada."""
    from .services_visitas import etiqueta, hora, telefono_legible
    telefono = normalizar_telefono_wa(conv.telefono)
    tel = f'Tel. {telefono_legible(telefono)}'
    visita = _visita_de_hoy(telefono, ahora)
    if visita is not None:
        estado = 'confirmada' if visita.estado == 'CONFIRMADA' else 'SIN CONFIRMAR'
        return {
            'objeto': visita, 'quien': visita.nombre, 'que': 'su visita',
            'lineas': [f'Visita {estado}: {hora(visita.inicio)}',
                       f'{visita.personas} personas' if visita.personas else '', tel],
            'para_el_modelo': f'Tiene visita {estado} hoy a las {hora(visita.inicio)}.',
        }
    reserva = _reservacion_de_hoy(telefono, timezone.localdate(ahora))
    if reserva is not None:
        saldo = reserva.saldo_pendiente()
        horario = f', {formato_hora_ampm(reserva.hora_inicio)}' if reserva.hora_inicio else ''
        return {
            'objeto': reserva, 'quien': _primer_nombre(getattr(reserva.cliente, 'nombre', '')) or conv.nombre,
            'que': f'su {reserva.get_tipo_servicio_display().lower()}',
            'lineas': [f'{reserva.get_tipo_servicio_display()} COT-{reserva.id:03d}{horario}',
                       f'{reserva.num_personas} personas' if reserva.num_personas else '',
                       f'Saldo por cobrar: ${saldo:,.2f}' if saldo > 0 else 'Pagado completo', tel],
            'para_el_modelo': f'Tiene {reserva.get_tipo_servicio_display()} hoy.',
        }
    anterior = (Visita.objects.filter(telefono=telefono, inicio__lt=ahora).exclude(estado='RECHAZADA')
                .order_by('-inicio').first())
    return {
        'objeto': conv, 'quien': conv.nombre or 'Alguien', 'que': None,
        'lineas': ['Sin cita ni reservación con este número',
                   f'Ya visitó la Quinta el {etiqueta(anterior.inicio)}' if anterior else '',
                   f'Nombre en WhatsApp: {conv.nombre}' if conv.nombre else '', tel],
        'para_el_modelo': 'No tiene visita ni reservación hoy con este número.',
    }


def contexto_de_hoy(conv) -> str:
    """Nota corta para el modelo cuando este número viene hoy a la Quinta, así
    un «¿cómo entro?» se entiende como llegada y no como pregunta de ubicación.
    Vacía si hoy no tiene nada."""
    if conv is None or not getattr(conv, 'telefono', ''):
        return ''
    try:
        datos = _contexto(conv, timezone.now())
    except Exception:
        logger.exception("Llegadas: no se pudo armar el contexto de hoy de %s", conv.pk)
        return ''
    if datos['que'] is None:
        return ''
    return (f"[Contexto del sistema: {datos['para_el_modelo']} Si dice que ya llegó, que está afuera, "
            'que le abran o cómo entrar, usa avisar_llegada.]\n')


def avisar_llegada(conv=None, situacion=None, minutos=None) -> dict:
    """Pendiente URGENTE al encargado: alguien llegó (o va en camino). Un mismo
    aviso abierto no se repite en `VIGENCIA_AVISO`; al pasar de «en camino» a
    «ya llegó» sí sale otro."""
    if conv is None:
        return {'error': 'No se pudo identificar al cliente.'}
    situacion = situacion if situacion in SITUACIONES else 'ya_llego'
    ahora = timezone.now()
    datos = _contexto(conv, ahora)
    objeto = datos['objeto']
    clave = f'llegada:{conv.pk}:{situacion}:'
    abierto = Pendiente.objects.filter(clave_idempotencia__startswith=clave, estado='ABIERTO',
                                       created_at__gte=ahora - VIGENCIA_AVISO).exists()
    if not abierto:
        try:
            minutos = int(minutos) if minutos else None
        except (TypeError, ValueError):
            minutos = None
        quien = datos['quien']
        if situacion == 'en_camino':
            llega = f' (llega en ~{minutos} min)' if minutos and 0 < minutos <= 240 else ''
            titulo = f'{quien} va en camino a la Quinta{llega}'
        else:
            titulo = f'{quien} llegó a la Quinta: está en la entrada'
        if datos['que'] is None:
            cierre = 'No viene con cita. Recíbelo o contéstale por el chat.'
        else:
            cierre = f"Viene a {datos['que']}. Que alguien lo reciba."
        services_pendientes.registrar_pendiente(
            titulo=titulo[:200], rol='ENCARGADO', tipo='LLEGADA', severidad='URGENTE',
            detalle='\n'.join([*[ln for ln in datos['lineas'] if ln], cierre]), objeto=objeto,
            vence_en=ahora + timedelta(minutes=30), clave_idempotencia=f'{clave}{ahora:%Y%m%d%H%M%S}',
            etiqueta_ok='Ya lo recibí' if situacion == 'ya_llego' else 'Enterado')
    if datos['que'] is None:
        nota = ('No tiene cita ni reservación con este número, pero ya avisé al equipo. Dile que en un momento '
                'alguien lo atiende. No le des la ubicación ni le agendes nada ahora; si después quiere visitar '
                'o reservar, ayúdale normalmente.')
    elif situacion == 'en_camino':
        nota = f"{datos['para_el_modelo']} Ya avisé al equipo que va en camino. Dile que lo esperan y que te escriba al llegar."
    else:
        nota = f"{datos['para_el_modelo']} Ya avisé al equipo. Dile que en un momento alguien lo recibe en la entrada."
    return {'ok': True, 'ya_estaba_avisado': abierto, 'nota': nota}

