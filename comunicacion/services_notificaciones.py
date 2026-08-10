"""
Capa de orquestación de las notificaciones transaccionales del ERP.

Un evento de negocio (cotización, pago, recordatorio) entra por aquí y sale como
Email + WhatsApp. Los triggers —la vista del cotizador, los signals y el comando
de recordatorios— no construyen mensajes ni hablan con Meta/Brevo: solo llaman a
estas funciones.

Reglas que sostienen todo el módulo:

- Cada canal falla por su cuenta. Un error de Brevo no impide el WhatsApp y
  ninguno de los dos revierte la operación de negocio.
- Cada envío lleva clave de idempotencia, así que repetir el trigger no duplica.
- El CTA siempre es la URL del PortalCliente, nunca un enlace de otra parte.
- Los mensajes iniciados por el negocio (pago, recordatorio) van por plantilla
  aprobada; fuera de la ventana de 24 h Meta rechaza el texto libre con 131047.
"""
import logging
from decimal import Decimal

from django.conf import settings

from .services import (
    alertar_equipo_email,
    enviar_email,
    enviar_whatsapp,
    enviar_whatsapp_template,
    normalizar_telefono_wa,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────── Utilidades ────────────────────────────────

def url_portal(cotizacion) -> str:
    """
    URL canónica del portal del cliente, o '' si no hay portal utilizable.

    Único punto que resuelve la URL: `PortalCliente.get_full_url()`. Antes se
    construía a mano en dos módulos distintos y se desincronizaban.
    """
    from comercial.models import PortalCliente
    try:
        portal = getattr(cotizacion, 'portal', None)
        if portal is None:
            portal, _ = PortalCliente.objects.get_or_create(
                cotizacion=cotizacion, defaults={'activo': True}
            )
        if not portal.activo:
            return ''
        return portal.get_full_url()
    except Exception:
        logger.exception("No se pudo resolver la URL del portal de COT-%s", cotizacion.pk)
        return ''


def _nombre_pila(cliente) -> str:
    nombre = (getattr(cliente, 'nombre', '') or '').strip()
    return nombre.split()[0].title() if nombre else 'Hola'


def _dinero(valor) -> str:
    return f"{Decimal(valor or 0):,.2f}"


def _fecha(valor) -> str:
    return valor.strftime('%d/%m/%Y') if valor else '—'


def _tipo_evento(cotizacion) -> str:
    """Nombre del tipo de evento desde el catálogo, no del texto libre del formulario."""
    tipo = getattr(cotizacion, 'tipo_evento', None)
    if tipo is not None:
        return str(tipo)
    return getattr(cotizacion, 'nombre_evento', '') or 'Evento'


def _plantilla(nombre_setting: str) -> str:
    return getattr(settings, nombre_setting, '') or ''


def _seguro(descripcion, funcion, *args, **kwargs):
    """
    Ejecuta un envío aislando su fallo.

    Las comunicaciones son un efecto secundario: que Meta o Brevo estén caídos
    no puede tumbar la cotización ni el pago que las originó.
    """
    try:
        return funcion(*args, **kwargs)
    except Exception:
        logger.exception("Fallo al %s", descripcion)
        return None


def _telefono(cliente) -> str:
    return normalizar_telefono_wa(getattr(cliente, 'telefono', ''))


# ──────────────────────────────── Cotización ────────────────────────────────

def notificar_cotizacion(cotizacion, *, origen: str):
    """
    Avisa al cliente de que su cotización está lista.

    `origen` distingue dos eventos que no son el mismo: 'WEB' es el acuse
    inmediato del cotizador público y 'ERP' es la cotización oficial cuando pasa
    a COTIZADA. Una cotización nacida en la web puede recibir después la versión
    del ERP, y ambas deben salir.

    En 'WEB' el cliente acaba de escribir, así que la ventana de 24 h está
    abierta y el texto libre es válido si no hay plantilla configurada. En 'ERP'
    puede haber pasado cualquier tiempo, así que la plantilla es obligatoria.
    """
    origen = (origen or '').upper()
    evento = 'web' if origen == 'WEB' else 'erp_cotizada'
    cliente = getattr(cotizacion, 'cliente', None)
    if cliente is None:
        return

    portal = url_portal(cotizacion)
    contexto = {'cotizacion': cotizacion, 'portal_url': portal}

    if cliente.email:
        _seguro(
            'enviar el email de cotización',
            enviar_email,
            cotizacion=cotizacion,
            tipo='COTIZACION',
            destinatario=cliente.email,
            asunto=f"Tu cotización — {cotizacion.nombre_evento}",
            template='comunicacion/email/cotizacion.html',
            context=contexto,
            clave_idempotencia=f"cotizacion:{cotizacion.pk}:{evento}:email",
        )

    telefono = _telefono(cliente)
    if not telefono:
        return

    clave_wa = f"cotizacion:{cotizacion.pk}:{evento}:whatsapp"
    plantilla = _plantilla('WA_TEMPLATE_COTIZACION')
    if plantilla:
        _seguro(
            'enviar el WhatsApp de cotización',
            enviar_whatsapp_template,
            cotizacion=cotizacion,
            tipo='COTIZACION',
            telefono=telefono,
            template_name=plantilla,
            parametros=[
                _nombre_pila(cliente),
                _tipo_evento(cotizacion),
                _fecha(cotizacion.fecha_evento),
                _dinero(cotizacion.precio_final),
                portal,
            ],
            clave_idempotencia=clave_wa,
        )
        return

    if origen != 'WEB':
        # Fuera del cotizador la ventana puede estar cerrada: mandar texto libre
        # fallaría con 131047 y ensuciaría la auditoría con un falso intento.
        from .services import reservar_comunicacion
        comm = _seguro(
            'registrar el WhatsApp de cotización sin plantilla',
            reservar_comunicacion,
            cotizacion=cotizacion, canal='WHATSAPP', tipo='COTIZACION',
            trigger='SIGNAL', destinatario=telefono, asunto='',
            estado='FALLIDO', clave_idempotencia=clave_wa,
            error='WA_TEMPLATE_COTIZACION no configurada',
        )
        if comm is not None:
            logger.warning(
                "COT-%s: sin WA_TEMPLATE_COTIZACION no se puede notificar por WhatsApp",
                cotizacion.pk,
            )
        return

    _seguro(
        'enviar el WhatsApp de cotización',
        enviar_whatsapp,
        cotizacion=cotizacion,
        tipo='COTIZACION',
        telefono=telefono,
        mensaje=(
            f"Hola {_nombre_pila(cliente)} 👋\n\n"
            f"Tipo de evento: {_tipo_evento(cotizacion)}\n"
            f"Fecha del evento: {_fecha(cotizacion.fecha_evento)}\n"
            f"Total a pagar: ${_dinero(cotizacion.precio_final)}\n"
            f"URL: {portal}"
        ),
        trigger='SIGNAL',
        clave_idempotencia=clave_wa,
    )


# ─────────────────────────────────── Pago ───────────────────────────────────

def notificar_pago(pago):
    """Confirma al cliente un pago de ingreso, con el saldo ya recalculado."""
    cotizacion = getattr(pago, 'cotizacion', None)
    cliente = getattr(cotizacion, 'cliente', None) if cotizacion else None
    if cliente is None:
        return

    portal = url_portal(cotizacion)
    total_pagado = cotizacion.total_pagado()
    saldo = cotizacion.saldo_pendiente()

    if cliente.email:
        _seguro(
            'enviar el email de confirmación de pago',
            enviar_email,
            cotizacion=cotizacion, pago=pago,
            tipo='CONFIRMACION_PAGO',
            destinatario=cliente.email,
            asunto=f"Pago recibido — {cotizacion.nombre_evento}",
            template='comunicacion/email/confirmacion_pago.html',
            context={
                'cotizacion': cotizacion, 'pago': pago,
                'total_pagado': total_pagado, 'saldo': saldo,
                'portal_url': portal,
            },
            clave_idempotencia=f"pago:{pago.pk}:email",
        )

    telefono = _telefono(cliente)
    if not telefono:
        return

    # Los parámetros van sin '$': el símbolo vive en el cuerpo aprobado de la
    # plantilla ("Saldo pendiente: ${{4}}"), no en la variable.
    saldo_texto = _dinero(saldo) if saldo > 0 else "0.00 — evento totalmente pagado"
    _seguro(
        'enviar el WhatsApp de confirmación de pago',
        enviar_whatsapp_template,
        cotizacion=cotizacion, pago=pago,
        tipo='CONFIRMACION_PAGO',
        telefono=telefono,
        template_name=_plantilla('WA_TEMPLATE_PAGO'),
        parametros=[
            _nombre_pila(cliente),
            _dinero(pago.monto),
            _fecha(pago.fecha_pago),
            saldo_texto,
            portal,
        ],
        clave_idempotencia=f"pago:{pago.pk}:whatsapp",
    )


# ──────────────────────────────── Recordatorio ──────────────────────────────

def notificar_recordatorio(parcialidad, *, fecha, dias_restantes=None):
    """
    Recordatorio de una parcialidad pendiente.

    La clave de idempotencia incluye la fecha de ejecución, así que correr el
    comando dos veces el mismo día no duplica, pero el recordatorio de +3 días y
    el de −1 sí son envíos distintos.
    """
    cotizacion = parcialidad.plan.cotizacion
    cliente = getattr(cotizacion, 'cliente', None)
    if cliente is None:
        return

    portal = url_portal(cotizacion)
    saldo = cotizacion.saldo_pendiente()
    if dias_restantes is None:
        dias_restantes = (parcialidad.fecha_limite - fecha).days
    marca = fecha.isoformat()

    if cliente.email:
        _seguro(
            'enviar el email de recordatorio',
            enviar_email,
            cotizacion=cotizacion,
            tipo='RECORDATORIO_PAGO',
            destinatario=cliente.email,
            asunto=f"Recordatorio de pago — {cotizacion.nombre_evento}",
            template='comunicacion/email/recordatorio.html',
            context={
                'cotizacion': cotizacion,
                'parcialidad': parcialidad,
                'dias_restantes': dias_restantes,
                'saldo': saldo,
                'portal_url': portal,
            },
            trigger='CRON',
            clave_idempotencia=f"recordatorio:{parcialidad.pk}:{marca}:email",
        )

    telefono = _telefono(cliente)
    if not telefono:
        return

    _seguro(
        'enviar el WhatsApp de recordatorio',
        enviar_whatsapp_template,
        cotizacion=cotizacion,
        tipo='RECORDATORIO_PAGO',
        telefono=telefono,
        template_name=_plantilla('WA_TEMPLATE_RECORDATORIO'),
        parametros=[
            _nombre_pila(cliente),
            _dinero(parcialidad.monto),
            _fecha(parcialidad.fecha_limite),
            _dinero(saldo),
            portal,
        ],
        trigger='CRON',
        clave_idempotencia=f"recordatorio:{parcialidad.pk}:{marca}:whatsapp",
    )


# ─────────────────────────── Alerta interna al equipo ───────────────────────

def alertar_equipo_nueva_cotizacion(cotizacion):
    """
    Avisa al negocio de que entró una cotización por el cotizador público.

    Va al `WA_NUMERO_NEGOCIO` configurado —sin fallback: un número de negocio
    hardcodeado es exactamente lo que hacía que estas alertas se perdieran— más
    una copia por correo, para que quede registro aunque WhatsApp falle.

    El destino es un número externo cualquiera, así que la ventana de 24 h le
    aplica igual: si se configura `WA_TEMPLATE_ALERTA_INTERNA` la alerta va por
    plantilla y llega siempre; si no, va como texto libre y solo llega mientras
    la ventana esté abierta.
    """
    portal = url_portal(cotizacion)
    tipo_evento = _tipo_evento(cotizacion)
    fecha = _fecha(cotizacion.fecha_evento)
    total = _dinero(cotizacion.precio_final)
    folio = f"COT-{cotizacion.pk:03d}"

    cuerpo = (
        f"🔔 Nueva cotización web ({folio})\n\n"
        f"Tipo de evento: {tipo_evento}\n"
        f"Fecha: {fecha}\n"
        f"Total a pagar: ${total}\n"
        f"URL: {portal}"
    )

    _seguro(
        'enviar la copia por email de la alerta interna',
        alertar_equipo_email,
        cotizacion,
        asunto=f"Nueva cotización web — {folio}",
        cuerpo=cuerpo,
        clave_idempotencia=f"cotizacion:{cotizacion.pk}:alerta_equipo:email",
    )

    destino = normalizar_telefono_wa(getattr(settings, 'WA_NUMERO_NEGOCIO', ''))
    if not destino:
        logger.error(
            "%s: WA_NUMERO_NEGOCIO no está configurado; la alerta interna de "
            "WhatsApp no se envía (la cotización se creó correctamente)", folio,
        )
        return

    clave = f"cotizacion:{cotizacion.pk}:alerta_equipo:whatsapp"
    plantilla = _plantilla('WA_TEMPLATE_ALERTA_INTERNA')
    if plantilla:
        _seguro(
            'enviar la alerta interna por WhatsApp',
            enviar_whatsapp_template,
            cotizacion=cotizacion,
            tipo='OTRO',
            telefono=destino,
            template_name=plantilla,
            parametros=[tipo_evento, fecha, total, portal],
            clave_idempotencia=clave,
        )
        return

    _seguro(
        'enviar la alerta interna por WhatsApp',
        enviar_whatsapp,
        cotizacion=cotizacion,
        tipo='OTRO',
        telefono=destino,
        mensaje=cuerpo,
        clave_idempotencia=clave,
    )
