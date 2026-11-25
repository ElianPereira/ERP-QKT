"""
Parte del día para el equipo de apoyo temporal (rol APOYO del directorio).

Mientras haya un equipo de apoyo vigente, cada mañana sale un mensaje con los
servicios confirmados de hoy y mañana (así el equipo se entera con 24 h de
anticipación) y las visitas de hoy. Cada quien recibe solo lo que le toca:

- Todos: folio, servicio, horario y personas.
- Encargado, Dirección y apoyo con operación, llegadas o pagos: además el
  nombre y teléfono del cliente.
- Encargado, Dirección y apoyo con pagos: además el saldo por cobrar.

Sin equipo de apoyo vigente no manda nada: fuera del viaje todo sigue igual.
Idempotente por día y teléfono.
"""
import logging
from datetime import timedelta

from django.utils import timezone

from core_erp.horarios import formato_hora_ampm

from .formato_wa import Aviso
from .models import Visita
from .services_pendientes import _enviar_aviso
from .services_responsables import apoyo_de_telefono, telefonos_apoyo, telefonos_aviso
from .services_visitas import hora, telefono_legible

logger = logging.getLogger(__name__)

CON_CLIENTE = ('avisa_operacion', 'avisa_llegadas', 'avisa_pagos')


def servicios_del_parte(hoy):
    """Cotizaciones confirmadas cuyo servicio ocurre hoy o mañana (un hospedaje
    en curso también cuenta)."""
    from comercial.models import Cotizacion

    manana = hoy + timedelta(days=1)
    candidatas = (Cotizacion.objects.filter(estado='CONFIRMADA', fecha_evento__lte=manana)
                  .select_related('cliente').order_by('fecha_evento', 'hora_inicio', 'id'))
    return [c for c in candidatas if (c.fecha_salida or c.fecha_evento) >= hoy]


def visitas_de_hoy(hoy):
    return list(Visita.objects.filter(estado='CONFIRMADA', inicio__date=hoy).order_by('inicio'))


def _horario(c) -> str:
    if c.tipo_servicio == 'HOSPEDAJE' and c.fecha_salida:
        return f'del {c.fecha_evento:%d/%m} al {c.fecha_salida:%d/%m}'
    if c.hora_inicio and c.hora_fin:
        return f'{formato_hora_ampm(c.hora_inicio)} a {formato_hora_ampm(c.hora_fin)}'
    return 'horario por confirmar'


def _linea_servicio(c, hoy, *, cliente: bool, saldo: bool) -> str:
    cuando = 'Hoy' if c.fecha_evento <= hoy else 'Mañana'
    partes = [f'{cuando}: COT-{c.pk:03d} {c.get_tipo_servicio_display()}, {_horario(c)}, {c.num_personas} personas']
    if cliente and c.cliente_id:
        tel = telefono_legible(c.cliente.telefono) if c.cliente.telefono else 'sin teléfono'
        partes.append(f'cliente {c.cliente.nombre} ({tel})')
    if saldo:
        pendiente = c.saldo_pendiente()
        partes.append(f'saldo por cobrar ${pendiente:,.2f}' if pendiente > 0 else 'liquidado')
    return ', '.join(partes)


def _alcance(telefono, internos, hoy) -> tuple:
    """(ve datos del cliente, ve saldo, ve visitas) para ese número."""
    if telefono in internos:
        return True, True, True
    integrante = apoyo_de_telefono(telefono, hoy)
    if integrante is None:
        return False, False, False
    cliente = any(getattr(integrante, campo) for campo in CON_CLIENTE)
    return cliente, integrante.avisa_pagos, integrante.avisa_llegadas or integrante.avisa_operacion


def aviso_del_parte(telefono, servicios, visitas, hoy, internos) -> Aviso:
    cliente, saldo, ve_visitas = _alcance(telefono, internos, hoy)
    lineas = [_linea_servicio(c, hoy, cliente=cliente, saldo=saldo) for c in servicios]
    if ve_visitas:
        lineas += [f'Visita hoy {hora(v.inicio)}: {v.nombre} ({telefono_legible(v.telefono)})' for v in visitas]
    return Aviso(titulo=f'Parte del día {hoy:%d/%m}', categoria='Equipo de apoyo', lineas=lineas[:12],
                 cierre='Si un cliente llega o algo falla, avisa al encargado.')


def enviar_parte_del_dia(hoy=None) -> int:
    """Manda el parte al equipo de apoyo vigente y al encargado (con la copia de
    Dirección). Devuelve cuántos salieron."""
    hoy = hoy or timezone.localdate()
    apoyo = telefonos_apoyo('EVENTOS', hoy)
    if not apoyo:
        return 0
    servicios, visitas = servicios_del_parte(hoy), visitas_de_hoy(hoy)
    if not (servicios or visitas):
        return 0
    internos = telefonos_aviso('ENCARGADO', hoy)
    enviados = 0
    for telefono in dict.fromkeys(internos + apoyo):
        aviso = aviso_del_parte(telefono, servicios, visitas, hoy, internos)
        if not aviso.lineas:
            continue
        comm = _enviar_aviso(telefono, aviso, clave=f'apoyo:parte:{hoy}:{telefono}')
        if comm is not None and comm.estado != 'FALLIDO':
            enviados += 1
    return enviados
