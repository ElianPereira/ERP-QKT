"""Permiso de fotos y testimonios (Issue #468, fase 7).

El cliente autoriza (o no) mostrar fotos de su evento, su testimonio y su nombre de pila en la página y las redes.
La evidencia es el texto exacto con su SHA-256, la fecha, el canal y el wamid del botón. Revocar no borra nada:
desactiva lo publicado que dependa de ese permiso. Nada aquí lanza hacia quien lo llama.
"""
import logging
import re
from typing import Iterable, Optional

from django.db import IntegrityError, transaction
from django.utils import timezone

from comunicacion.formato_wa import bloques
from comunicacion.services import enviar_whatsapp_botones, normalizar_telefono_wa

from ..models import ALCANCES_MEDIOS, AutorizacionMedios

logger = logging.getLogger(__name__)

RE_BOTON = re.compile(r'^med:(\d+):(si|no)$')
ETIQUETAS = dict(ALCANCES_MEDIOS)
TODOS = [codigo for codigo, _ in ALCANCES_MEDIOS]
TEXTO_SOLICITUD = bloques(
    '¿Nos autorizas a usar algunas fotos de tu evento y tu opinión, con tu nombre de pila, en nuestra página '
    'y redes sociales?',
    'Puedes retirar tu permiso cuando quieras escribiéndonos.')
GRACIAS_SI = bloques('¡Gracias! Quedó registrada tu autorización.', 'Si cambias de opinión, escríbenos y la retiramos.')
GRACIAS_NO = bloques('Gracias por avisarnos. No usaremos tus fotos ni tu opinión.')
YA_REGISTRADA = bloques('Ya habíamos registrado tu respuesta. ¡Gracias!')


def id_boton(autorizacion_id: int, respuesta: str) -> str:
    return f'med:{autorizacion_id}:{respuesta}'


def es_boton(texto: str) -> bool:
    return bool(RE_BOTON.match((texto or '').strip()))


def _diez(telefono) -> str:
    return ''.join(filter(str.isdigit, telefono or ''))[-10:]


# ─────────────────────────────── Consulta ──────────────────────────────────

def vigente(cotizacion_id) -> Optional[AutorizacionMedios]:
    """La autorización AUTORIZADA y no revocada de la cotización, si existe."""
    return (AutorizacionMedios.objects
            .filter(cotizacion_id=cotizacion_id, estado=AutorizacionMedios.AUTORIZADA).order_by('-id').first())


def faltantes(cotizacion_id, alcances: Iterable[str]) -> list:
    """Etiquetas de los alcances que no cubre una autorización vigente de la cotización."""
    autorizacion = vigente(cotizacion_id)
    cubiertos = set(autorizacion.alcances) if autorizacion else set()
    return [ETIQUETAS.get(a, a) for a in alcances if a not in cubiertos]


# ───────────────────────────── Altas y bajas ───────────────────────────────

@transaction.atomic
def registrar(*, cliente, cotizacion=None, alcances=None, origen='ADMIN', texto='', usuario=None,
              referencia=''):
    """Deja una autorización AUTORIZADA (el equipo la captura con su evidencia, o el portal). Si la cotización
    ya tenía una solicitud viva, esa fila se decide en vez de duplicarse."""
    alcances = list(alcances or TODOS)
    ahora = timezone.now()
    viva = (AutorizacionMedios.objects.select_for_update()
            .filter(cotizacion=cotizacion, estado__in=AutorizacionMedios.VIVAS).first()) if cotizacion else None
    if viva is not None:
        viva.alcances = alcances
        viva.estado = AutorizacionMedios.AUTORIZADA
        viva.origen = origen
        viva.texto_aceptado = texto or viva.texto_aceptado
        viva.referencia_externa = referencia or viva.referencia_externa
        viva.decidida_en = ahora
        viva.registrada_por = usuario if getattr(usuario, 'pk', None) else None
        viva.updated_by = viva.registrada_por
        viva.full_clean(exclude=['cliente'])
        viva.save()
        return viva
    autorizacion = AutorizacionMedios(
        cliente=cliente, cotizacion=cotizacion, alcances=alcances, estado=AutorizacionMedios.AUTORIZADA,
        origen=origen, texto_aceptado=texto, referencia_externa=referencia, decidida_en=ahora,
        registrada_por=usuario if getattr(usuario, 'pk', None) else None)
    autorizacion.created_by = autorizacion.registrada_por
    autorizacion.full_clean(exclude=['cliente'])
    autorizacion.save()
    return autorizacion


@transaction.atomic
def revocar(autorizacion: AutorizacionMedios, usuario=None, motivo='') -> int:
    """Revoca el permiso (soft) y desactiva lo publicado que dependa de él. Devuelve cuántos contenidos
    públicos se desactivaron."""
    autorizacion = AutorizacionMedios.objects.select_for_update().get(pk=autorizacion.pk)
    if autorizacion.estado not in AutorizacionMedios.VIVAS:
        return 0
    autorizacion.estado = AutorizacionMedios.REVOCADA
    autorizacion.revocada_en = timezone.now()
    autorizacion.revocada_por = usuario if getattr(usuario, 'pk', None) else None
    autorizacion.updated_by = autorizacion.revocada_por
    autorizacion.motivo_revocacion = (motivo or '')[:255]
    autorizacion.save()
    return _desactivar_contenido(autorizacion.cotizacion_id)


def _desactivar_contenido(cotizacion_id) -> int:
    from comercial.models import ImagenLanding, TestimonioLanding

    if not cotizacion_id:
        return 0
    n = ImagenLanding.objects.filter(cotizacion_id=cotizacion_id, activo=True).update(activo=False)
    n += TestimonioLanding.objects.filter(cotizacion_id=cotizacion_id, activo=True).update(activo=False)
    return n


# ───────────────────────────── Solicitud por WhatsApp ──────────────────────

def solicitar_por_whatsapp(cotizacion) -> Optional[AutorizacionMedios]:
    """Pide el permiso con botones Sí/No justo después de una encuesta 4–5. Una sola vez por cotización, solo a
    quien aceptó MARKETING, no pidió la baja y tiene la ventana de 24 h abierta (acaba de contestar). Nunca lanza."""
    try:
        from comunicacion.services_encuesta import _motivo_marketing
        from comunicacion.services_pendientes import ventana_abierta

        cliente = cotizacion.cliente
        if _motivo_marketing(cliente) or not ventana_abierta(cliente.telefono):
            return None
        try:
            with transaction.atomic():
                if AutorizacionMedios.objects.filter(cotizacion=cotizacion).exists():
                    return None
                autorizacion = AutorizacionMedios.objects.create(
                    cliente=cliente, cotizacion=cotizacion, alcances=list(TODOS), origen='WHATSAPP',
                    texto_aceptado=TEXTO_SOLICITUD)
        except IntegrityError:
            return None
        enviar_whatsapp_botones(
            tipo='OTRO', telefono=cliente.telefono, mensaje=TEXTO_SOLICITUD,
            botones=[(id_boton(autorizacion.pk, 'si'), 'Sí, autorizo'), (id_boton(autorizacion.pk, 'no'), 'No, gracias')],
            trigger='SIGNAL', clave_idempotencia=f'medios:{cotizacion.pk}:solicitud')
        return autorizacion
    except Exception:
        logger.exception('Marketing: no se pudo pedir la autorización de medios')
        return None


def atender_boton(telefono: str, texto: str, wamid: str = '') -> Optional[str]:
    """Procesa `med:<id>:si|no`. Solo vale desde el teléfono del cliente; un botón ajeno se descarta (None)."""
    boton = RE_BOTON.match((texto or '').strip())
    if not boton:
        return None
    with transaction.atomic():
        autorizacion = (AutorizacionMedios.objects.select_for_update().select_related('cliente')
                        .filter(pk=int(boton.group(1))).first())
        if autorizacion is None or _diez(autorizacion.cliente.telefono) != _diez(normalizar_telefono_wa(telefono)):
            return None
        if autorizacion.estado != AutorizacionMedios.SOLICITADA:
            return YA_REGISTRADA
        acepto = boton.group(2) == 'si'
        autorizacion.estado = AutorizacionMedios.AUTORIZADA if acepto else AutorizacionMedios.RECHAZADA
        autorizacion.decidida_en = timezone.now()
        autorizacion.referencia_externa = (wamid or '')[:200]
        autorizacion.save()
    return GRACIAS_SI if acepto else GRACIAS_NO
