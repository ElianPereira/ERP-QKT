"""
Directorio de responsables internos (Issue #421).

Fuente única de «a qué número va cada aviso interno». Mientras no haya un
`Responsable` para ENCARGADO o DIRECCION, esos roles caen a
`settings.ERP_WA_NEGOCIO_NUMERO` (transición sin regresión).
"""
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .formato_wa import Aviso, bloques
from .models import CambioResponsable, Responsable
from .services import normalizar_telefono_wa
from .services_bajas import _normalizar

logger = logging.getLogger(__name__)

ROLES_CON_RESPALDO = ('ENCARGADO', 'DIRECCION')
SIN_DESTINO = 'no hay responsable ni ERP_WA_NEGOCIO_NUMERO configurado'
CONFIRMACION_ACEPTO = bloques(
    "✅ Listo, este número quedó activo para recibir los avisos de Quinta Ko'ox Tanil.",
    'Si no lo esperabas, avisa al equipo.')


def _respaldo() -> str:
    return normalizar_telefono_wa(getattr(settings, 'ERP_WA_NEGOCIO_NUMERO', ''))


def responsable(rol: str, hoy=None):
    """El responsable activo y confirmado del rol. Un suplente con rango
    vigente hoy gana sobre el permanente; fuera del rango no cuenta."""
    hoy = hoy or timezone.localdate()
    candidatos = [
        r for r in Responsable.objects.filter(rol=rol, activo=True).exclude(telefono_wa='').order_by('-id')
        if (r.vigente_desde is None or r.vigente_desde <= hoy)
        and (r.vigente_hasta is None or r.vigente_hasta >= hoy)
    ]
    suplentes = [r for r in candidatos if r.vigente_desde or r.vigente_hasta]
    return (suplentes or candidatos or [None])[0]


def telefono_de(rol: str, hoy=None) -> str:
    resp = responsable(rol, hoy)
    if resp is not None:
        return normalizar_telefono_wa(resp.telefono_wa)
    return _respaldo() if rol in ROLES_CON_RESPALDO else ''


def telefonos_aviso(rol: str, hoy=None) -> list:
    """Teléfonos que reciben un aviso del rol: el suyo, más el de Dirección
    si tiene la copia en vivo. Sin repetidos."""
    telefonos = [telefono_de(rol, hoy)]
    if rol != 'DIRECCION':
        direccion = responsable('DIRECCION', hoy)
        if direccion is not None and direccion.modo_copia == 'EN_VIVO':
            telefonos.append(normalizar_telefono_wa(direccion.telefono_wa))
    return list(dict.fromkeys(t for t in telefonos if t))


TEMAS_APOYO = {
    'EVENTOS': 'avisa_eventos',
    'OPERACION': 'avisa_operacion',
    'LLEGADAS': 'avisa_llegadas',
    'PAGOS': 'avisa_pagos',
    'COMPRAS': 'avisa_compras',
}


def equipo_apoyo(hoy=None) -> list:
    """Integrantes del equipo de apoyo vigentes hoy, con número confirmado. Sin
    rango de fechas completo no cuentan: el apoyo siempre es temporal."""
    hoy = hoy or timezone.localdate()
    return list(Responsable.objects.filter(
        rol='APOYO', activo=True, vigente_desde__lte=hoy, vigente_hasta__gte=hoy,
    ).exclude(telefono_wa='').order_by('nombre', 'id'))


def telefonos_apoyo(tema: str, hoy=None) -> list:
    """Teléfonos del equipo de apoyo vigente que recibe ese tema."""
    campo = TEMAS_APOYO[tema]
    telefonos = (normalizar_telefono_wa(r.telefono_wa) for r in equipo_apoyo(hoy) if getattr(r, campo))
    return list(dict.fromkeys(t for t in telefonos if t))


def apoyo_de_telefono(telefono: str, hoy=None):
    """El integrante vigente del equipo de apoyo con ese número, o None."""
    tel = normalizar_telefono_wa(telefono)
    return next((r for r in equipo_apoyo(hoy) if tel and normalizar_telefono_wa(r.telefono_wa) == tel), None)


def destinos_ventas(hoy=None) -> list:
    """A dónde van los avisos de ventas (cotización nueva, visitas, prospectos): los del rol VENTAS si
    existe un responsable activo; si no, los del ENCARGADO, igual que antes (Issue #505)."""
    if responsable('VENTAS', hoy) is not None:
        return telefonos_aviso('VENTAS', hoy)
    return telefonos_aviso('ENCARGADO', hoy)


def rol_ventas(hoy=None) -> str:
    """Rol dueño de los pendientes de ventas: VENTAS si hay responsable, si no ENCARGADO."""
    return 'VENTAS' if responsable('VENTAS', hoy) is not None else 'ENCARGADO'


def rol_de_telefono(telefono: str):
    """Rol del número en el directorio (activo o pendiente de confirmar), o None."""
    tel = normalizar_telefono_wa(telefono)
    if not tel:
        return None
    for r in Responsable.objects.filter(activo=True).exclude(telefono_wa='', telefono_pendiente=''):
        if tel in (normalizar_telefono_wa(r.telefono_wa), normalizar_telefono_wa(r.telefono_pendiente)):
            return r.rol
    return 'ENCARGADO' if tel == _respaldo() else None


def registrar_cambio(resp, campo, antes, despues, usuario=None):
    return CambioResponsable.objects.create(
        responsable=resp, campo=campo, antes=str(antes or '')[:300], despues=str(despues or '')[:300],
        usuario=usuario if getattr(usuario, 'pk', None) else None)


def proponer_telefono(resp, telefono: str, usuario=None) -> bool:
    """Registra un número nuevo como pendiente y pide el «ACEPTO». El activo no
    cambia. Devuelve True si hubo algo que confirmar."""
    nuevo = normalizar_telefono_wa(telefono)
    if not nuevo or nuevo == normalizar_telefono_wa(resp.telefono_wa):
        if resp.telefono_pendiente:
            registrar_cambio(resp, 'telefono_pendiente', resp.telefono_pendiente, '', usuario)
            resp.telefono_pendiente = ''
            resp.save(update_fields=['telefono_pendiente', 'updated_at'])
        return False
    if nuevo == normalizar_telefono_wa(resp.telefono_pendiente):
        return False
    registrar_cambio(resp, 'telefono_pendiente', resp.telefono_pendiente, nuevo, usuario)
    resp.telefono_pendiente = nuevo
    resp.save(update_fields=['telefono_pendiente', 'updated_at'])
    _pedir_acepto(resp)
    return True


def _pedir_acepto(resp) -> None:
    from .services_pendientes import _enviar_aviso  # evita el ciclo: pendientes importa este módulo

    _enviar_aviso(resp.telefono_pendiente, Aviso(
        titulo='Activa este número para recibir avisos', categoria='Quinta Ko\'ox Tanil',
        lineas=[f'Recibirá los avisos de {resp.get_rol_display()}', *_vigencia_apoyo(resp)],
        cierre='Responde ACEPTO para activarlo.'))


def _vigencia_apoyo(resp) -> list:
    if resp.rol != 'APOYO' or not (resp.vigente_desde and resp.vigente_hasta):
        return []
    return [f'Del {resp.vigente_desde:%d/%m/%Y} al {resp.vigente_hasta:%d/%m/%Y}']


def reenviar_acepto(resp, usuario=None) -> None:
    """Vuelve a mandar la solicitud de «ACEPTO» al número pendiente."""
    registrar_cambio(resp, 'reenvio_acepto', '', resp.telefono_pendiente, usuario)
    _pedir_acepto(resp)


def es_acepto(texto: str) -> bool:
    return _normalizar(texto) == 'acepto'


def confirmar_numero(telefono: str):
    """«ACEPTO» desde un número pendiente: lo activa. Devuelve el responsable o None."""
    tel = normalizar_telefono_wa(telefono)
    if not tel:
        return None
    with transaction.atomic():
        for r in Responsable.objects.select_for_update().filter(activo=True).exclude(telefono_pendiente=''):
            if normalizar_telefono_wa(r.telefono_pendiente) == tel:
                registrar_cambio(r, 'telefono_wa', r.telefono_wa, tel)
                r.telefono_wa, r.telefono_pendiente = tel, ''
                r.save(update_fields=['telefono_wa', 'telefono_pendiente', 'updated_at'])
                return r
    return None
