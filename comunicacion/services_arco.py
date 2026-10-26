"""
Solicitudes ARCO recibidas por Kooxi (Fase 2c, Issue #432).

Kooxi solo **registra**: crea la `legal.SolicitudARCO` (el plazo de 20 días
hábiles lo calcula el propio modelo) y le deja a Dirección un pendiente con el
borrador del acuse. Nada sale al titular sin que Dirección toque el botón
`arco:<id>-<firma>:ok` desde su teléfono activo. El acuse es texto fijo (no lo
redacta un modelo); la resolución de fondo (procedente o no) se captura en el
admin de `legal`. No se modifica nada de `legal/`.

El botón no necesita tabla de tokens: lleva el id y una firma HMAC del id, y el
envío es de una sola vez por la clave de idempotencia del correo.
"""
import hmac
import logging
import re
from typing import Optional

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from legal.models import EstadoARCO, SolicitudARCO, TipoARCO

from .services import enviar_email, normalizar_telefono_wa
from .services_pendientes import registrar_pendiente
from .services_responsables import rol_de_telefono, telefono_de

logger = logging.getLogger(__name__)

MAX_POR_NUMERO_DIA = 2
RE_BOTON = re.compile(r'^arco:(\d+)-([0-9a-f]{32}):ok$')
ETIQUETA_BOTON = 'Enviar respuesta'
ASUNTO = 'Recibimos tu solicitud de derechos ARCO'

BORRADOR = (
    'Recibimos tu solicitud de derechos ARCO ({tipo}) con el folio {folio}. '
    'Daremos respuesta en un plazo máximo de 20 días hábiles, a más tardar el {limite}. '
    'Para atenderla necesitamos verificar tu identidad: responde a este correo con una copia legible de tu '
    'identificación oficial vigente, citando el folio. Tus datos y esa copia se usan solo para atender tu solicitud.'
)


class ArcoError(ValueError):
    """Error con mensaje apto para el cliente."""


def _firma(pk: int) -> str:
    return salted_hmac('comunicacion.arco', str(pk), algorithm='sha256').hexdigest()[:32]


def id_boton(pk: int) -> str:
    return f'arco:{pk}-{_firma(pk)}:ok'


def es_boton(texto: str) -> bool:
    return bool(RE_BOTON.match((texto or '').strip()))


def borrador(solicitud: SolicitudARCO) -> str:
    return BORRADOR.format(tipo=solicitud.get_tipo_display().lower(), folio=solicitud.folio,
                           limite=solicitud.fecha_limite.strftime('%d/%m/%Y'))


def registrar(*, telefono: str, tipo: str, nombre: str, correo: str, descripcion: str) -> SolicitudARCO:
    """Registra la solicitud y avisa a Dirección. Lanza `ArcoError`."""
    tel = normalizar_telefono_wa(telefono)
    nombre, correo = (nombre or '').strip()[:200], (correo or '').strip()
    if tipo not in TipoARCO.values:
        raise ArcoError('Indica qué derecho quieres ejercer: acceso, rectificación, cancelación, oposición '
                        'o revocación del consentimiento.')
    if len(nombre.split()) < 2:
        raise ArcoError('Necesito tu nombre completo para registrar la solicitud.')
    try:
        validate_email(correo)
    except ValidationError as e:
        raise ArcoError('Necesito un correo válido para responderte.') from e
    hoy = timezone.localdate()
    if SolicitudARCO.objects.filter(telefono=tel, recibida_en__date=hoy).count() >= MAX_POR_NUMERO_DIA:
        raise ArcoError('Ya registramos solicitudes desde este número hoy. El equipo te contactará por este chat.')
    with transaction.atomic():
        solicitud = SolicitudARCO.objects.create(
            tipo=tipo, titular_nombre=nombre, correo=correo, telefono=tel,
            descripcion=((descripcion or '').strip() or 'Sin detalle')[:2000])
    texto = borrador(solicitud)
    # Sin nombre ni texto del cliente: la bandeja no la alcanza la purga de chats.
    registrar_pendiente(
        titulo=f'Solicitud ARCO {solicitud.folio}', rol='DIRECCION', tipo='OTRO', severidad='URGENTE',
        detalle=(f'{solicitud.get_tipo_display()}, vence {solicitud.fecha_limite:%d/%m/%Y}. '
                 f'Borrador del acuse: {texto}'),
        objeto=solicitud, clave_idempotencia=f'arco:{solicitud.pk}',
        botones=[(id_boton(solicitud.pk), ETIQUETA_BOTON)])
    return solicitud


def atender_boton(telefono: str, texto: str) -> Optional[str]:
    """Aprueba el envío del acuse. None si no es un botón ARCO o el teléfono no
    es el activo de DIRECCION (se descarta sin contestar)."""
    coincide = RE_BOTON.match((texto or '').strip())
    tel = normalizar_telefono_wa(telefono)
    if not coincide or rol_de_telefono(tel) != 'DIRECCION' or tel != telefono_de('DIRECCION'):
        return None
    pk = int(coincide.group(1))
    if not hmac.compare_digest(coincide.group(2), _firma(pk)):
        return 'Ese botón no es válido.'
    with transaction.atomic():
        solicitud = SolicitudARCO.objects.select_for_update().filter(pk=pk).first()
        if solicitud is None:
            return 'Ese botón no es válido.'
        if solicitud.estado != EstadoARCO.RECIBIDA:
            return f'La solicitud {solicitud.folio} ya estaba en trámite. No se envió nada más.'
        comm = enviar_email(
            tipo='OTRO', destinatario=solicitud.correo, asunto=ASUNTO, template='comunicacion/email/aviso_solicitud.html',
            context={'nombre': solicitud.titular_nombre, 'titulo': ASUNTO, 'texto': borrador(solicitud), 'asunto': ASUNTO},
            clave_idempotencia=f'arco:{solicitud.pk}:acuse')
        if comm is None or comm.estado == 'FALLIDO':
            logger.error("ARCO %s: no se pudo enviar el acuse", solicitud.folio)
            return f'No se pudo enviar el correo de {solicitud.folio}. Revísalo en el ERP.'
        solicitud.estado = EstadoARCO.EN_TRAMITE
        solicitud.save(update_fields=['estado'])
    return f'✅ Listo: se envió el acuse de {solicitud.folio} y quedó en trámite (vence {solicitud.fecha_limite:%d/%m/%Y}).'
