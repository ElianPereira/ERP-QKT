"""
Agente de WhatsApp con IA (Issue #346).

Flujo: el webhook guarda cada mensaje entrante (`recibir_mensaje`) y lanza
`procesar_conversacion` en segundo plano; ahí se juntan los mensajes aún sin
contestar, se le pasan a Claude con las herramientas de consulta del ERP
(`herramientas_agente`) y la respuesta sale por el mismo transporte de
WhatsApp que usa el resto del sistema (`services.enviar_whatsapp`).

Reglas que no son obvias:
- `ConversacionWhatsApp.historial` solo crece. El modelo liga su razonamiento
  previo al texto exacto de la conversación; editar un turno ya enviado lo
  invalida. Por eso la fecha del día va dentro de cada mensaje del cliente y
  no en el system prompt, que nunca cambia.
- Tras `REINICIO_TRAS` sin mensajes la conversación empieza de cero (misma
  ventana de 24 h de Meta para texto libre).
- Nunca contesta si el agente está apagado, el número no está en la lista de
  prueba, alguien pidió humano o el propietario contestó desde la app.
"""
import json
import logging
import threading
from datetime import timedelta

import anthropic
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from django.utils.formats import date_format

from .herramientas_agente import HERRAMIENTAS, URL_COTIZADOR, ejecutar
from .models import ConversacionWhatsApp, MensajeWhatsApp
from .services import alertar_equipo_email, enviar_whatsapp, normalizar_telefono_wa

logger = logging.getLogger(__name__)

REINICIO_TRAS = timedelta(hours=24)
# Turnos de herramienta por mensaje del cliente: una pregunta normal usa 1-3.
MAX_ITERACIONES = 6
# Tope del historial serializado; al pasarlo se empieza de cero en vez de
# recortar (recortar editaría turnos ya enviados).
MAX_HISTORIAL_CARACTERES = 200_000
LIMITE_WHATSAPP = 4000
CANDADO_SEGUNDOS = 300

MENSAJE_HUMANO = ('Gracias por tu paciencia. Le paso tu mensaje a una persona del equipo '
                  'y te contesta por aquí en cuanto pueda.')

SYSTEM_PROMPT = f"""Eres el asistente virtual por WhatsApp de Quinta Ko'ox Tanil (QKT), una quinta \
en Umán, Yucatán, para eventos (hasta 150 personas), pasadías con alberca y hospedaje corto en dos \
habitaciones (Ka'an Room y Otoch Room). Contestas a clientes y posibles clientes en español de México, \
con trato cálido y cercano, en mensajes cortos propios de WhatsApp (sin tablas ni encabezados; \
listas cortas con guiones cuando ayuden).

Cómo trabajas:
- Todo dato de fechas, precios, paquetes, habitaciones o reglas sale de tus herramientas. Si una \
herramienta no lo da, no lo sabes: dilo y ofrece pasar con una persona. Nunca inventes precios, \
horarios, políticas ni disponibilidad.
- Antes de decir un precio, usa cotizar_estimado o ver_opciones y repite el importe tal cual, \
aclarando que es un estimado con IVA incluido. No hagas cuentas por tu cuenta ni ofrezcas descuentos.
- Antes de decir que una fecha está libre, usa consultar_disponibilidad. Aclara que la fecha no queda \
apartada hasta pagar el anticipo.
- Para reservar, manda al cotizador web ({URL_COTIZADOR}): ahí el cliente elige, acepta el aviso de \
privacidad y recibe su portal para pagar. Tú no creas reservaciones, no cobras ni firmas contratos.
- Si el cliente pide hablar con una persona, se queja, quiere negociar, pregunta por un pago o \
reservación que ya tiene, o su caso no lo cubren tus herramientas, usa pasar_a_humano y avísale que \
alguien del equipo le contestará por este mismo chat.
- En tu primer mensaje de una conversación preséntate como asistente virtual de la Quinta.
- Pide solo los datos que necesitas para contestar (servicio, fecha, personas); no pidas datos \
personales ni fiscales.
- Cada mensaje del cliente trae entre corchetes la fecha de hoy; úsala para interpretar "el próximo \
sábado" y similares, y confirma la fecha exacta con el cliente si hay duda."""


# ─────────────────────────── Entrada (webhook) ───────────────────────────

def _conversacion(telefono: str, nombre: str = '') -> ConversacionWhatsApp:
    conv, creada = ConversacionWhatsApp.objects.get_or_create(telefono=telefono)
    if nombre and conv.nombre != nombre[:200]:
        conv.nombre = nombre[:200]
        conv.save(update_fields=['nombre', 'updated_at'])
    if creada:
        from comercial.models import Cliente
        cliente = Cliente.objects.filter(telefono__endswith=telefono[-10:]).order_by('-id').first()
        if cliente:
            conv.cliente = cliente
            conv.save(update_fields=['cliente', 'updated_at'])
    return conv


def _guardar_mensaje(conv, direccion, texto, wamid, procesado):
    """INSERT idempotente por wamid: Meta reintenta entregas."""
    try:
        with transaction.atomic():
            return MensajeWhatsApp.objects.create(
                conversacion=conv, direccion=direccion, texto=texto[:10000],
                wamid=wamid or None, procesado=procesado,
            )
    except IntegrityError:
        logger.info("Mensaje de WhatsApp repetido omitido (wamid=%s)", wamid)
        return None


def recibir_mensaje(*, telefono, nombre, texto, wamid):
    """Registra un mensaje del cliente. Devuelve la conversación si es nuevo, o None."""
    telefono = normalizar_telefono_wa(telefono)
    if not telefono:
        return None
    conv = _conversacion(telefono, nombre)
    if _guardar_mensaje(conv, 'ENTRADA', texto, wamid, procesado=False) is None:
        return None
    conv.ultimo_mensaje = timezone.now()
    conv.save(update_fields=['ultimo_mensaje', 'updated_at'])
    return conv


def registrar_respuesta_humana(*, telefono, texto, wamid):
    """El propietario contestó desde la app (coexistencia): el agente se calla."""
    telefono = normalizar_telefono_wa(telefono)
    if not telefono:
        return
    conv = _conversacion(telefono)
    if _guardar_mensaje(conv, 'HUMANO', texto, wamid, procesado=True) is None:
        return
    horas = getattr(settings, 'WA_AGENTE_PAUSA_HUMANO_HORAS', 12)
    conv.pausado_hasta = timezone.now() + timedelta(hours=horas)
    conv.ultimo_mensaje = timezone.now()
    conv.save(update_fields=['pausado_hasta', 'ultimo_mensaje', 'updated_at'])


class VentanaCerrada(Exception):
    """El cliente no escribe desde hace más de 24 h: Meta rechaza el texto libre."""


def responder_como_persona(conv, texto: str, usuario) -> MensajeWhatsApp:
    """Respuesta escrita por alguien del equipo desde el admin.

    Sin coexistencia el número del agente no está en la app de WhatsApp, así
    que esta es la única vía para que una persona conteste. Pausa al agente en
    esa conversación igual que un eco de la app, para que no se encimen.
    """
    texto = (texto or '').strip()[:LIMITE_WHATSAPP]
    if not texto:
        raise ValueError('La respuesta está vacía.')
    ultima_entrada = conv.mensajes.filter(direccion='ENTRADA').order_by('-created_at').first()
    if not ultima_entrada or timezone.now() - ultima_entrada.created_at > REINICIO_TRAS:
        raise VentanaCerrada(
            'El cliente no ha escrito en las últimas 24 h y WhatsApp no permite mandarle '
            'texto libre. Contáctalo por otro medio o espera a que vuelva a escribir.')
    comm = enviar_whatsapp(tipo='OTRO', telefono=conv.telefono, mensaje=texto, trigger='MANUAL')
    if comm is None or comm.estado == 'FALLIDO':
        raise ValueError(f"WhatsApp no aceptó el mensaje: {getattr(comm, 'error', '') or 'sin detalle'}")
    mensaje = MensajeWhatsApp.objects.create(
        conversacion=conv, direccion='HUMANO', texto=texto, procesado=True,
        wamid=comm.proveedor_id or None, enviado_por=usuario,
    )
    horas = getattr(settings, 'WA_AGENTE_PAUSA_HUMANO_HORAS', 12)
    conv.pausado_hasta = timezone.now() + timedelta(hours=horas)
    conv.ultimo_mensaje = timezone.now()
    conv.save(update_fields=['pausado_hasta', 'ultimo_mensaje', 'updated_at'])
    return mensaje


# ─────────────────────────── Procesamiento ───────────────────────────────

def agente_contesta_a(conv) -> bool:
    if not getattr(settings, 'WA_AGENTE_ACTIVO', False):
        return False
    prueba = {normalizar_telefono_wa(n) for n in getattr(settings, 'WA_AGENTE_NUMEROS_PRUEBA', []) if n}
    prueba.discard('')
    if prueba and conv.telefono not in prueba:
        return False
    # El número del propietario (destino de las alertas internas) le escribe
    # al de la API para abrir la ventana de 24 h; el agente no le contesta.
    if conv.telefono == normalizar_telefono_wa(getattr(settings, 'WA_NUMERO_NEGOCIO', '')):
        return False
    return not conv.agente_en_pausa()


def lanzar_procesamiento(conversacion_id: int) -> None:
    """Contesta en segundo plano: Meta espera un 200 rápido y el modelo tarda segundos."""
    def _trabajo():
        try:
            procesar_conversacion(conversacion_id)
        except Exception:
            logger.exception("Agente WhatsApp: error procesando la conversación %s", conversacion_id)
        finally:
            connection.close()
    threading.Thread(target=_trabajo, daemon=True, name=f'agente-wa-{conversacion_id}').start()


def procesar_conversacion(conversacion_id: int) -> None:
    """Contesta los mensajes pendientes de una conversación, uno a la vez.

    El candado evita dos respuestas en paralelo si el cliente manda varios
    mensajes seguidos: el hilo que lo tiene vuelve a revisar al terminar y
    se lleva también los que llegaron mientras contestaba.
    """
    clave = f'agente_wa:{conversacion_id}'
    while True:
        if not cache.add(clave, 1, timeout=CANDADO_SEGUNDOS):
            return
        try:
            _atender_pendientes(conversacion_id)
        finally:
            cache.delete(clave)
        if not MensajeWhatsApp.objects.filter(
            conversacion_id=conversacion_id, direccion='ENTRADA', procesado=False,
        ).exists():
            return


def _atender_pendientes(conversacion_id: int) -> None:
    conv = ConversacionWhatsApp.objects.get(pk=conversacion_id)
    pendientes = list(conv.mensajes.filter(direccion='ENTRADA', procesado=False).order_by('created_at', 'id'))
    if not pendientes:
        return
    MensajeWhatsApp.objects.filter(pk__in=[m.pk for m in pendientes]).update(procesado=True)
    if not agente_contesta_a(conv):
        return

    texto = _texto_desde_ultima_respuesta(conv)
    if not texto:
        return
    respuesta = responder(conv, texto)
    if respuesta:
        _enviar(conv, respuesta)


def _texto_desde_ultima_respuesta(conv) -> str:
    """Lo que el agente no ha visto desde su última respuesta.

    Normalmente son solo los mensajes nuevos del cliente. Si mientras tanto
    contestó una persona desde la app (el agente estaba en pausa), se le pasa
    el intercambio completo con quién dijo qué, para que no repita ni
    contradiga lo que ya se le respondió al cliente.
    """
    ultima = conv.mensajes.filter(direccion='AGENTE').order_by('-created_at', '-id').first()
    desde = timezone.now() - REINICIO_TRAS
    if ultima and ultima.created_at > desde:
        desde = ultima.created_at
    recientes = conv.mensajes.exclude(direccion='AGENTE').filter(created_at__gte=desde)
    recientes = [m for m in recientes.order_by('created_at', 'id') if m.texto.strip()]
    if not any(m.direccion == 'ENTRADA' for m in recientes):
        return ''
    if not any(m.direccion == 'HUMANO' for m in recientes):
        return '\n'.join(m.texto for m in recientes)
    return ('Conversación desde tu última respuesta (una persona del equipo también contestó):\n'
            + '\n'.join(f"{'Equipo QKT' if m.direccion == 'HUMANO' else 'Cliente'}: {m.texto}"
                        for m in recientes))


def _enviar(conv, texto: str) -> None:
    texto = texto.strip()[:LIMITE_WHATSAPP]
    comm = enviar_whatsapp(tipo='AGENTE_IA', telefono=conv.telefono, mensaje=texto, trigger='SIGNAL')
    MensajeWhatsApp.objects.create(
        conversacion=conv, direccion='AGENTE', texto=texto, procesado=True,
        wamid=(comm.proveedor_id or None) if comm else None,
    )


def _pasar_a_humano(conv, motivo: str) -> None:
    conv.requiere_humano = True
    conv.motivo_humano = (motivo or '')[:300]
    conv.save(update_fields=['requiere_humano', 'motivo_humano', 'updated_at'])
    ultimos = conv.mensajes.order_by('-created_at', '-id')[:6]
    resumen = '\n'.join(f"{m.get_direccion_display()}: {m.texto[:300]}" for m in reversed(ultimos))
    alertar_equipo_email(
        None,
        asunto=f"WhatsApp: {conv.nombre or conv.telefono} necesita atención",
        cuerpo=(f"El agente de WhatsApp pasó la conversación a una persona.\n\n"
                f"Cliente: {conv.nombre or '—'} ({conv.telefono})\nMotivo: {conv.motivo_humano}\n\n"
                f"Últimos mensajes:\n{resumen}\n\n"
                "Contesta desde Admin → Comunicación → Conversaciones (campo «Responder»). "
                "Para que el agente vuelva a contestar, desmarca «Requiere humano»."),
    )


def _cliente_ia():
    return anthropic.Anthropic(timeout=60.0, max_retries=2)


def _llamar_modelo(client, messages):
    return client.beta.messages.create(
        model=settings.WA_AGENTE_MODELO,
        max_tokens=4000,
        system=[{'type': 'text', 'text': SYSTEM_PROMPT}],
        tools=HERRAMIENTAS,
        messages=messages,
        output_config={'effort': 'low'},
        cache_control={'type': 'ephemeral'},
        betas=['server-side-fallback-2026-07-01'],
        fallbacks='default',
    )


def responder(conv, texto_cliente: str):
    """Corre el agente sobre el historial y devuelve el texto a enviar (o None)."""
    ahora = timezone.localtime()
    historial = list(conv.historial or [])
    ultima = conv.mensajes.filter(direccion__in=('AGENTE', 'HUMANO')).order_by('-created_at').first()
    if (not ultima or ahora - ultima.created_at > REINICIO_TRAS
            or len(json.dumps(historial)) > MAX_HISTORIAL_CARACTERES):
        historial = []

    fecha_hoy = date_format(ahora, 'l j \\d\\e F \\d\\e Y, H:i')
    messages = historial + [{
        'role': 'user',
        'content': [{'type': 'text', 'text': f'[Hoy es {fecha_hoy}, hora de Yucatán]\n{texto_cliente}'}],
    }]

    try:
        client = _cliente_ia()
        respuesta = None
        for _ in range(MAX_ITERACIONES):
            respuesta = _llamar_modelo(client, messages)
            if respuesta.stop_reason == 'refusal':
                logger.warning("Agente WhatsApp: el modelo declinó en %s", conv.telefono)
                _pasar_a_humano(conv, 'El asistente no pudo responder este mensaje.')
                return MENSAJE_HUMANO
            messages.append({'role': 'assistant', 'content': [b.to_dict(exclude_none=True) for b in respuesta.content]})
            if respuesta.stop_reason != 'tool_use':
                break
            messages.append({'role': 'user', 'content': _resultados_herramientas(conv, respuesta.content)})
        else:
            _pasar_a_humano(conv, 'El asistente no llegó a una respuesta.')
            conv.historial = messages
            conv.save(update_fields=['historial', 'updated_at'])
            return MENSAJE_HUMANO
    except anthropic.APIError:
        logger.exception("Agente WhatsApp: falló la API de Claude para %s", conv.telefono)
        _pasar_a_humano(conv, 'El asistente no estuvo disponible (error de la API de IA).')
        return MENSAJE_HUMANO

    conv.historial = messages
    conv.save(update_fields=['historial', 'updated_at'])
    texto = '\n'.join(b.text for b in respuesta.content if b.type == 'text').strip()
    if not texto and conv.requiere_humano:
        return MENSAJE_HUMANO
    return texto or None


def _resultados_herramientas(conv, contenido) -> list:
    resultados = []
    for bloque in contenido:
        if bloque.type != 'tool_use':
            continue
        if bloque.name == 'pasar_a_humano':
            _pasar_a_humano(conv, (bloque.input or {}).get('motivo', ''))
            salida, error = json.dumps({'ok': True, 'nota': 'El equipo fue avisado.'}), False
        else:
            salida, error = ejecutar(bloque.name, bloque.input or {})
        resultado = {'type': 'tool_result', 'tool_use_id': bloque.id, 'content': salida}
        if error:
            resultado['is_error'] = True
        resultados.append(resultado)
    return resultados
