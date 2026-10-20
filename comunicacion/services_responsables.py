"""
Directorio de responsables internos (Issue #421).

Fuente única de «a qué número va cada aviso interno». Mientras no haya un
`Responsable` para ENCARGADO o DIRECCION, esos roles caen a
`settings.WA_NUMERO_NEGOCIO` (transición sin regresión).
"""
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import CambioResponsable, Responsable
from .services import enviar_whatsapp, enviar_whatsapp_template, normalizar_telefono_wa
from .services_bajas import _normalizar

logger = logging.getLogger(__name__)

ROLES_CON_RESPALDO = ('ENCARGADO', 'DIRECCION')
SIN_DESTINO = 'no hay responsable ni WA_NUMERO_NEGOCIO configurado'
CONFIRMACION_ACEPTO = ('Listo, este número quedó activo para recibir los avisos de Quinta Ko\'ox Tanil. '
                       'Si no lo esperabas, avisa al equipo.')


def _respaldo() -> str:
    return normalizar_telefono_wa(getattr(settings, 'WA_NUMERO_NEGOCIO', ''))


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
    texto = (f'Este número recibirá los avisos de {resp.get_rol_display()} de Quinta Ko\'ox Tanil. '
             'Responde ACEPTO para activarlo.')
    plantilla = getattr(settings, 'WA_TEMPLATE_OPERACIONES', '') or ''
    try:
        if plantilla:
            enviar_whatsapp_template(tipo='OTRO', telefono=resp.telefono_pendiente,
                                     template_name=plantilla, parametros=[texto])
        else:
            enviar_whatsapp(tipo='OTRO', telefono=resp.telefono_pendiente, mensaje=texto)
    except Exception:
        logger.exception("No se pudo pedir el ACEPTO a %s", resp)


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
