"""Consentimiento de privacidad automático para cotizaciones capturadas fuera del
cotizador público (Issue #506).

El personal no hace nada: el Aviso va en el PDF y el correo de cotización, el
portal pide la aceptación al propio cliente y, para quien paga sin abrir el
portal, una tarea diaria le manda una sola solicitud por WhatsApp. La evidencia
siempre es una `legal.AceptacionLegal` (origen `PORTAL_CLIENTE` o `WHATSAPP`);
aquí no se toca `legal/`: solo se llama a `LegalService`.

Nunca se bloquea capturar una cotización ni un pago: lo único que depende de la
aceptación es lo que hace el propio cliente en el portal (firmar el contrato y
enviar solicitudes; pagar ya exige su casilla en el checkout).
"""
import logging
import re
from typing import Optional

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from legal.models import AceptacionLegal, OrigenAceptacion, TipoDocumento
from legal.services import LegalService

from .models import Cliente, Cotizacion

logger = logging.getLogger(__name__)

URL_AVISO_PRIVACIDAD = 'https://quintakooxtanil.com/aviso-de-privacidad'
ESTADOS_ABIERTOS = ('BORRADOR', 'COTIZADA', 'CONFIRMADA')
FINALIDAD_MARKETING = 'MARKETING'
FINALIDAD_USO_IMAGEN = 'USO_IMAGEN'

RE_BOTON = re.compile(r'^con:(\d+):(ok|promo|no):([\w.\-]+)$')
OPCIONES_BOTON = {'ok': 'Acepto', 'promo': 'Acepto + promociones', 'no': 'No acepto'}

MENSAJE_ACEPTADO = ('Listo, quedó registrada tu autorización. Gracias. Si tienes cualquier duda, '
                    'aquí estamos para ayudarte.')
MENSAJE_NO_ACEPTO = ('Entendido, no registramos tu autorización. Puedes revisarla y aceptarla cuando '
                     'quieras desde el portal de tu reservación o escribiéndonos aquí.')
MENSAJE_VENCIDO = ('Ese mensaje ya venció porque el Aviso de Privacidad cambió. Te enviaremos uno nuevo.')


# ───────────────────────────── Aceptación vigente ─────────────────────────────

def version_aviso_vigente() -> Optional[str]:
    documento = LegalService.documento_vigente(TipoDocumento.AVISO_PRIVACIDAD)
    return documento.version if documento else None


def _incluye_version(snapshot, version: str) -> bool:
    return any(d.get('tipo') == TipoDocumento.AVISO_PRIVACIDAD and d.get('version') == version
               for d in snapshot or [])


def tiene_aceptacion_vigente(cliente) -> bool:
    """¿El cliente tiene una aceptación cuyo snapshot incluye la versión vigente del
    Aviso de Privacidad? Una versión nueva del Aviso vuelve a pedirla, una sola vez.
    Sin versión vigente publicada no hay nada que exigir."""
    version = version_aviso_vigente()
    if version is None or getattr(cliente, 'pk', None) is None:
        return True
    return any(_incluye_version(s, version) for s in
               AceptacionLegal.objects.filter(cliente=cliente).values_list('snapshot_documentos', flat=True))


def clientes_con_aceptacion_vigente() -> set:
    """Ids de cliente con aceptación vigente, en una sola consulta (listas del admin)."""
    version = version_aviso_vigente()
    if version is None:
        return set()
    return {cid for cid, snap in AceptacionLegal.objects.filter(cliente__isnull=False)
            .values_list('cliente_id', 'snapshot_documentos') if _incluye_version(snap, version)}


def correo_de(cliente, telefono: str = '') -> str:
    """Correo que acompaña la evidencia (el campo es obligatorio); sin correo, el
    mismo marcador que ya usa Kooxi."""
    if getattr(cliente, 'email', ''):
        return cliente.email
    return f'{telefono or cliente.telefono or "sin-telefono"}@sin-correo.whatsapp.invalid'


def registrar_desde_portal(request, cliente, finalidades) -> AceptacionLegal:
    """Evidencia de la tarjeta del portal: IP y navegador del propio cliente."""
    return LegalService.registrar_aceptacion(
        request=request, correo=correo_de(cliente), origen=OrigenAceptacion.PORTAL_CLIENTE,
        cliente=cliente, finalidades_aceptadas=list(finalidades or []))


# ───────────────────────────── Quién debe aceptar ─────────────────────────────

def clientes_con_reservacion():
    """Clientes con una cotización CONFIRMADA o con algún pago de una cotización
    abierta. No se barre el pasado: nada de históricas, cerradas, expiradas ni
    canceladas, ni condonaciones (no entró dinero)."""
    vivas = Q(cotizacion__es_historica=False)
    return Cliente.objects.filter(
        vivas & (Q(cotizacion__estado='CONFIRMADA')
                 | (Q(cotizacion__estado__in=ESTADOS_ABIERTOS, cotizacion__pagos__tipo='INGRESO')
                    & ~Q(cotizacion__pagos__metodo='CONDONACION')))
    ).distinct()


def clientes_sin_aceptar():
    vigentes = clientes_con_aceptacion_vigente()
    return [c for c in clientes_con_reservacion().order_by('pk') if c.pk not in vigentes]


def contar_confirmados_sin_aceptar() -> int:
    """Para el resumen del lunes: clientes con reservación confirmada sin aceptar el Aviso."""
    vigentes = clientes_con_aceptacion_vigente()
    confirmados = Cliente.objects.filter(cotizacion__estado='CONFIRMADA', cotizacion__es_historica=False
                                         ).distinct().values_list('pk', flat=True)
    return sum(1 for pk in confirmados if pk not in vigentes)


# ───────────────────────────── Solicitud por WhatsApp ─────────────────────────────

def id_boton(cliente_id: int, opcion: str, version: str) -> str:
    return f'con:{cliente_id}:{opcion}:{version}'


def es_boton(texto: str) -> bool:
    return bool(RE_BOTON.match((texto or '').strip()))


def _clave(cliente_id: int, version: str) -> str:
    return f'consentimiento:{cliente_id}:{version}:whatsapp'


def _plantilla() -> str:
    return getattr(settings, 'META_WA_PLANTILLA_CONSENTIMIENTO', '') or ''


def _liberar_clave_si_fallo(comm):
    """Un envío fallido libera su clave para reintentar mañana (el registro queda)."""
    if comm is None:
        return None
    if comm.estado == 'FALLIDO':
        comm.clave_idempotencia = None
        comm.save(update_fields=['clave_idempotencia'])
    return comm


def pedir_por_whatsapp(cliente) -> str:
    """Manda la solicitud a un cliente. Devuelve el resultado: `enviada`, `ya_enviada`,
    `sin_plantilla`, `sin_telefono` o `fallida`. Una sola por cliente y versión del Aviso;
    no es promoción: no depende de MARKETING ni de `BajaWhatsApp`."""
    from comunicacion.services import (
        enviar_whatsapp_botones,
        enviar_whatsapp_template,
        normalizar_telefono_wa,
    )
    from comunicacion.services_agente import (
        MENSAJE_CONSENTIMIENTO,
        registrar_mensaje_automatico,
    )
    from comunicacion.services_pendientes import ventana_abierta

    version = version_aviso_vigente()
    telefono = normalizar_telefono_wa(cliente.telefono)
    if not telefono:
        return 'sin_telefono'
    if version is None:
        return 'fallida'
    clave = _clave(cliente.pk, version)
    from comunicacion.models import ComunicacionCliente
    if ComunicacionCliente.objects.filter(clave_idempotencia=clave).exists():
        return 'ya_enviada'

    if ventana_abierta(telefono):
        botones = [(id_boton(cliente.pk, op, version), titulo) for op, titulo in OPCIONES_BOTON.items()]
        comm = enviar_whatsapp_botones(tipo='OTRO', telefono=telefono, mensaje=MENSAJE_CONSENTIMIENTO,
                                       botones=botones, trigger='CRON', clave_idempotencia=clave)
        texto = MENSAJE_CONSENTIMIENTO
    else:
        plantilla = _plantilla()
        if not plantilla:
            return 'sin_plantilla'
        nombre = (cliente.nombre or '').strip().split()
        components = [
            {'type': 'body', 'parameters': [{'type': 'text', 'text': nombre[0].title() if nombre else 'Hola'}]},
        ] + [
            {'type': 'button', 'sub_type': 'quick_reply', 'index': str(i),
             'parameters': [{'type': 'payload', 'payload': id_boton(cliente.pk, op, version)}]}
            for i, op in enumerate(OPCIONES_BOTON)
        ]
        comm = enviar_whatsapp_template(tipo='OTRO', telefono=telefono, template_name=plantilla,
                                        components=components, trigger='CRON', clave_idempotencia=clave)
        texto = 'Solicitud de autorización del Aviso de Privacidad (plantilla).'
    comm = _liberar_clave_si_fallo(comm)
    if comm is None:
        return 'ya_enviada'
    if comm.estado == 'FALLIDO':
        return 'fallida'
    registrar_mensaje_automatico(telefono=telefono, wamid=comm.proveedor_id, texto=texto)
    return 'enviada'


def atender_boton(telefono: str, texto: str, wamid: str = '') -> Optional[str]:
    """Botón `con:<cliente>:<ok|promo|no>:<versión>`. Solo vale desde el teléfono del
    cliente; uno ajeno se descarta sin contestar. Devuelve el texto de respuesta."""
    from comunicacion.services import normalizar_telefono_wa

    boton = RE_BOTON.match((texto or '').strip())
    if not boton:
        return None
    cliente = Cliente.objects.filter(pk=int(boton.group(1))).first()
    telefono = normalizar_telefono_wa(telefono)
    if cliente is None or not telefono or normalizar_telefono_wa(cliente.telefono)[-10:] != telefono[-10:]:
        return None
    opcion, version = boton.group(2), boton.group(3)
    if opcion == 'no':
        return MENSAJE_NO_ACEPTO
    if version != version_aviso_vigente():
        return MENSAJE_VENCIDO
    if tiene_aceptacion_vigente(cliente):
        return MENSAJE_ACEPTADO
    try:
        LegalService.registrar_aceptacion(
            request=None, correo=correo_de(cliente, telefono), origen=OrigenAceptacion.WHATSAPP,
            cliente=cliente, finalidades_aceptadas=[FINALIDAD_MARKETING] if opcion == 'promo' else [],
            referencia_externa=wamid or '', aceptado_en=timezone.now())
    except Exception:
        logger.exception("Consentimiento por WhatsApp: no se pudo registrar la aceptación del cliente %s", cliente.pk)
        return None
    return MENSAJE_ACEPTADO


# ───────────────────────────── Tarea diaria ─────────────────────────────

def avisar_sin_contacto(cliente) -> bool:
    """Única excepción humana: reservación confirmada sin teléfono ni correo. Un pendiente
    NORMAL, idempotente por cliente, para quien capturó la cotización."""
    from comunicacion.services_pendientes import registrar_pendiente

    cotizacion = (Cotizacion.objects.filter(cliente=cliente, estado='CONFIRMADA', es_historica=False)
                  .select_related('usuario').order_by('-created_at').first())
    if cotizacion is None:
        return False
    capturo = ''
    if cotizacion.usuario:
        capturo = cotizacion.usuario.get_full_name() or cotizacion.usuario.username
    detalle = [f'Cliente: {cliente.nombre}', f'Reservación: COT-{cotizacion.pk:03d}']
    if capturo:
        detalle.append(f'La capturó: {capturo}')
    detalle.append('Sin teléfono ni correo no se le puede pedir su autorización del Aviso de Privacidad.')
    registrar_pendiente(
        titulo='Captura un teléfono o correo del cliente', rol='ENCARGADO', objeto=cliente,
        detalle='\n'.join(detalle), clave_idempotencia=f'consentimiento_sin_contacto:{cliente.pk}')
    return True


def pedir_consentimientos_pendientes(*, dry_run: bool = False) -> dict:
    """Respaldo diario para quien ya reservó o pagó y no ha aceptado. Idempotente."""
    from comunicacion.services import normalizar_telefono_wa

    resumen = {'candidatos': 0, 'enviadas': 0, 'ya_enviadas': 0, 'sin_plantilla': 0,
               'fallidas': 0, 'sin_contacto': 0}
    for cliente in clientes_sin_aceptar():
        resumen['candidatos'] += 1
        if not normalizar_telefono_wa(cliente.telefono):
            if not cliente.email:
                resumen['sin_contacto'] += 1
                if not dry_run:
                    avisar_sin_contacto(cliente)
            continue
        if dry_run:
            continue
        resultado = pedir_por_whatsapp(cliente)
        clave = {'enviada': 'enviadas', 'ya_enviada': 'ya_enviadas', 'sin_plantilla': 'sin_plantilla',
                 'fallida': 'fallidas'}.get(resultado)
        if clave:
            resumen[clave] += 1
    return resumen

