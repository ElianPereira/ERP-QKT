"""
Herramientas del agente de WhatsApp (Issue #346; Issue #366 agrega las del cliente).

Casi todas son consultas de solo lectura. Las que dependen de quién escribe
(`mi_reservacion`, `crear_cotizacion`) reciben la conversación desde
`ejecutar`, nunca un teléfono del modelo; `crear_cotizacion` es la única que
escribe, con la misma función que el cotizador web. Los precios salen de
`comercial.views_cotizador.estimar_total`, la misma función que exhibe el
total del cotizador web: el agente nunca calcula ni redondea importes.

El resultado de cada herramienta es un dict serializable que se le devuelve
al modelo como JSON. Los errores de captura (fecha mal escrita, servicio que
no existe) se devuelven como `{"error": ...}` para que el modelo le pida el
dato al cliente en vez de adivinarlo.
"""
import json
import logging
from datetime import date, datetime, timedelta

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from comercial.disponibilidad import verificar_disponibilidad_rango
from comercial.forms_cotizador import TIPO_EVENTO_CHOICES
from comercial.models import Cotizacion, PreguntaFrecuente, Producto
from comercial.reglas_eventos import (
    MAX_PERSONAS_EVENTO,
    MAX_PERSONAS_EXTRA_POR_HABITACION,
    MAX_PERSONAS_PASADIA,
    MIN_PERSONAS_PERSONALIZADO_EVENTO,
)
from comercial.views_cotizador import (
    HORAS_BASE_EVENTO,
    HORAS_MAX_EVENTO,
    NOCHES_HOSPEDAJE_MAX,
    SolicitudInvalida,
    crear_cotizacion_solicitud,
    estimar_total,
)

logger = logging.getLogger(__name__)

URL_COTIZADOR = 'https://quintakooxtanil.com/cotizar/'
# Entrada al portal con código + 4 dígitos: la salida de un enlace vencido.
URL_PORTAL_ACCESO = 'https://quintakooxtanil.com/mi-evento/'
SERVICIOS = ('EVENTO', 'PASADIA', 'HOSPEDAJE')

HERRAMIENTAS = [
    {
        'name': 'consultar_disponibilidad',
        'description': (
            'Revisa si una fecha (o una estancia de hospedaje de varias noches) '
            'está libre. Úsala siempre antes de decir que una fecha está disponible. '
            'Una fecha libre NO queda apartada: se aparta al pagar el anticipo.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'fecha': {'type': 'string', 'description': 'Fecha de inicio, formato AAAA-MM-DD.'},
                'noches': {
                    'type': 'integer',
                    'description': 'Solo hospedaje: número de noches. Omitir para evento o pasadía.',
                },
            },
            'required': ['fecha'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'ver_opciones',
        'description': (
            'Lista lo que se puede contratar de un servicio, con precio total IVA incluido '
            'y lo que incluye cada opción, tal como lo ofrece la Quinta: paquetes de '
            'evento y la renta del lugar sola, niveles de pasadía (Básico/Premium) o '
            'habitaciones de hospedaje.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'servicio': {'type': 'string', 'enum': list(SERVICIOS)},
                'personas': {
                    'type': 'integer',
                    'description': 'Número de personas, si el cliente ya lo dijo (cambia el precio de paquetes).',
                },
            },
            'required': ['servicio'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'cotizar_estimado',
        'description': (
            'Calcula el total estimado (IVA incluido, con promociones automáticas vigentes) '
            'de una selección concreta, con el mismo cálculo del cotizador web. Úsala para '
            'cualquier precio que vayas a decir. Para Evento, con paquete_id cotiza ese paquete '
            '(sale de ver_opciones); sin paquete_id cotiza solo la renta del lugar, que se '
            f'contrata a partir de {MIN_PERSONAS_PERSONALIZADO_EVENTO} personas.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'servicio': {'type': 'string', 'enum': list(SERVICIOS)},
                'personas': {'type': 'integer'},
                'fecha': {'type': 'string', 'description': 'AAAA-MM-DD, si el cliente la dio.'},
                'paquete_id': {
                    'type': 'integer',
                    'description': 'Evento: id del paquete. Omitir para cotizar solo la renta del lugar.',
                },
                'horas': {
                    'type': 'integer',
                    'description': f'Evento: duración total en horas ({HORAS_BASE_EVENTO} incluidas, '
                                   f'máximo {HORAS_MAX_EVENTO}).',
                },
                'nivel_pasadia': {'type': 'string', 'enum': ['BASICO', 'PREMIUM']},
                'habitaciones_ids': {
                    'type': 'array', 'items': {'type': 'integer'},
                    'description': 'Hospedaje: ids de las habitaciones (salen de ver_opciones).',
                },
                'noches': {'type': 'integer', 'description': 'Hospedaje: número de noches.'},
            },
            'required': ['servicio', 'personas'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'condiciones_de_pago',
        'description': (
            'Reglas de pago de la Quinta: cuánto se paga para apartar, cuándo se liquida y cómo se '
            'paga. Úsala para cualquier pregunta de anticipo, liquidación o formas de pago. '
            'Con la fecha del cliente da también su fecha límite para liquidar.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'servicio': {'type': 'string', 'enum': list(SERVICIOS)},
                'fecha': {'type': 'string', 'description': 'AAAA-MM-DD, si el cliente la dio.'},
            },
            'required': ['servicio'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'preguntas_frecuentes',
        'description': (
            'Devuelve las preguntas frecuentes vigentes del negocio con su respuesta oficial '
            '(reglas, qué se permite, políticas). Consúltala antes de responder cualquier duda '
            'que no sea de precio o fecha.'
        ),
        'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    },
    {
        'name': 'mi_reservacion',
        'description': (
            'Las cotizaciones y reservaciones vigentes del cliente que escribe, identificado '
            'por su número de WhatsApp: estado, fecha, total, lo pagado, el saldo, el mínimo '
            'a pagar y la fecha límite para liquidar. Úsala cuando pregunte por su reservación, '
            'su saldo o cuánto le falta. Solo ve las de este número: no puede consultar otras.'
        ),
        'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    },
    {
        'name': 'crear_cotizacion',
        'description': (
            'Crea la cotización formal, igual que el cotizador web, y le manda al cliente '
            'por WhatsApp y correo el enlace a su portal para pagar. Úsala solo cuando el cliente '
            'pida que le prepares la cotización y ya tengas servicio, fecha libre, personas, la '
            'opción elegida (paquete, nivel u habitaciones), su nombre y su correo. La primera vez '
            'el sistema le manda un mensaje con botones para aceptar el aviso de privacidad: si la '
            'herramienta responde que falta la autorización, díselo y espera su respuesta.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'servicio': {'type': 'string', 'enum': list(SERVICIOS)},
                'fecha': {'type': 'string', 'description': 'AAAA-MM-DD.'},
                'personas': {'type': 'integer'},
                'nombre': {'type': 'string', 'description': 'Nombre completo del cliente.'},
                'correo': {'type': 'string', 'description': 'Correo del cliente.'},
                'paquete_id': {'type': 'integer', 'description': 'Evento: id del paquete; omitir para solo la renta.'},
                'hora_inicio': {'type': 'string', 'description': 'Evento: HH:MM en 24 h.'},
                'hora_fin': {'type': 'string', 'description': 'Evento: HH:MM en 24 h.'},
                'tipo_evento': {'type': 'string', 'enum': [v for v, _ in TIPO_EVENTO_CHOICES]},
                'nivel_pasadia': {'type': 'string', 'enum': ['BASICO', 'PREMIUM']},
                'habitaciones_ids': {'type': 'array', 'items': {'type': 'integer'}},
                'noches': {'type': 'integer'},
                'notas': {'type': 'string', 'description': 'Lo que el cliente pidió anotar (máx. 300).'},
            },
            'required': ['servicio', 'fecha', 'personas', 'nombre', 'correo'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'registrar_pregunta_sin_respuesta',
        'description': (
            'Anota para el equipo una duda del cliente que tus herramientas no contestan (por '
            'ejemplo, algo que no viene en preguntas_frecuentes ni en ver_opciones). Úsala antes '
            'de decirle al cliente que no tienes ese dato. No la uses para preguntas que no '
            'corresponden (información interna, datos de otras personas).'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'pregunta': {
                    'type': 'string',
                    'description': 'La duda redactada en general, sin nombres, teléfonos ni datos '
                                   'personales. Ej.: «¿La renta del lugar incluye la alberca?»',
                },
            },
            'required': ['pregunta'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'pasar_a_humano',
        'description': (
            'Pasa la conversación a una persona del equipo y deja de contestar. Úsala si el '
            'cliente lo pide, si hay una queja, una negociación de precio, un caso que tus '
            'herramientas no cubren, o si ya quiere reservar con algo fuera del cotizador web.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'motivo': {'type': 'string', 'description': 'Una línea para el equipo: qué necesita el cliente.'},
            },
            'required': ['motivo'],
            'additionalProperties': False,
        },
    },
]


def _fecha(valor):
    """Fecha AAAA-MM-DD a `date`, o un mensaje de error para el modelo."""
    try:
        f = datetime.strptime(str(valor or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None, 'Fecha no válida: pídesela al cliente y usa el formato AAAA-MM-DD.'
    if f < timezone.localdate():
        return None, 'La fecha ya pasó: confirma con el cliente la fecha correcta.'
    return f, None


def _entero(valor, defecto=None):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return defecto


def _formato(monto):
    return f"${monto:,.2f}"


def _incluye(producto) -> dict:
    """Lo que trae un paquete o nivel según el ERP: su descripción y los
    productos capturados en «Productos incluidos». Van sin cantidad a
    propósito: en los paquetes por persona la cantidad es por invitado y
    leída suelta («1 x Silla») engañaría al cliente."""
    componentes = list(
        producto.productos_incluidos.select_related('producto_hijo')
        .order_by('producto_hijo__nombre').values_list('producto_hijo__nombre', flat=True)
    )
    return {
        'descripcion': producto.descripcion or producto.descripcion_corta or '',
        'productos_incluidos': componentes,
    }


def _disponibilidad(inicio: date, noches: int = 0) -> dict:
    fin = inicio + timedelta(days=max(noches, 1))
    libre, _ = verificar_disponibilidad_rango(inicio, fin)
    # El mensaje de disponibilidad del ERP menciona el folio y el servicio de
    # la otra reservación: al cliente solo le importa si está libre o no.
    return {
        'fecha': inicio.isoformat(),
        'noches': noches or None,
        'disponible': libre,
        'nota': ('Libre por ahora; se aparta al pagar el anticipo.' if libre
                 else 'Ocupada: sugiere otra fecha.'),
    }


def consultar_disponibilidad(fecha=None, noches=None):
    inicio, error = _fecha(fecha)
    if error:
        return {'error': error}
    noches = _entero(noches, 0) or 0
    if noches > NOCHES_HOSPEDAJE_MAX:
        return {'error': f'Máximo {NOCHES_HOSPEDAJE_MAX} noches por reservación.'}
    return _disponibilidad(inicio, noches)


def _solo_renta(personas):
    """La renta del lugar sin paquete: la línea base que el cotizador web cobra
    en «Arma tu propio evento». Se ofrece con la misma regla de aforo mínimo."""
    base = Producto.objects.filter(rol_cotizador='BASE_EVENTO').first()
    if not base:
        return None
    return {
        'nombre': base.nombre,
        'precio_total': _formato(estimar_total(
            servicio='EVENTO', num_personas=max(personas, MIN_PERSONAS_PERSONALIZADO_EVENTO),
            horas_evento=HORAS_BASE_EVENTO)['total']),
        'incluye': _incluye(base),
        'minimo_personas': MIN_PERSONAS_PERSONALIZADO_EVENTO,
    }


def ver_opciones(servicio=None, personas=None):
    servicio = str(servicio or '').upper()
    personas = _entero(personas)

    if servicio == 'EVENTO':
        n = min(max(personas or 50, 1), MAX_PERSONAS_EVENTO)
        paquetes = Producto.objects.filter(
            es_paquete=True, visible_cotizador=True, cotizador_evento=True,
        ).order_by('orden_cotizador', 'nombre')
        return {
            'servicio': 'EVENTO',
            'personas_cotizadas': n,
            'paquetes': [{
                'paquete_id': p.id,
                'nombre': p.nombre,
                'precio_total': _formato(estimar_total(
                    servicio='EVENTO', paquete_id=p.id, num_personas=n,
                    horas_evento=HORAS_BASE_EVENTO)['total']),
                'incluye': _incluye(p),
            } for p in paquetes],
            'solo_renta': _solo_renta(n),
            'reglas': (
                f'{HORAS_BASE_EVENTO} horas incluidas y hasta {HORAS_MAX_EVENTO - HORAS_BASE_EVENTO} '
                f'horas extra con costo. Aforo máximo {MAX_PERSONAS_EVENTO} personas. '
                f'La renta sola (y armar el evento a la medida) es desde '
                f'{MIN_PERSONAS_PERSONALIZADO_EVENTO} personas; con menos, solo paquetes.'
            ),
            'cotizador_web': f'{URL_COTIZADOR}?servicio=EVENTO',
        }

    if servicio == 'PASADIA':
        n = min(max(personas or 20, 1), MAX_PERSONAS_PASADIA)
        niveles = []
        for nivel, rol in (('BASICO', 'BASE_PASADIA_BASICO'), ('PREMIUM', 'BASE_PASADIA_PREMIUM')):
            prod = Producto.objects.filter(rol_cotizador=rol).first()
            if not prod:
                continue
            niveles.append({
                'nivel': nivel,
                'nombre': prod.nombre,
                'precio_total': _formato(estimar_total(
                    servicio='PASADIA', nivel_pasadia=nivel, num_personas=n)['total']),
                'incluye': _incluye(prod),
            })
        return {
            'servicio': 'PASADIA',
            'personas_cotizadas': n,
            'niveles': niveles,
            'reglas': (
                'Horario 11:00 a.m. a 7:00 p.m. 20 personas incluidas en ambos niveles; de 21 a '
                f'{MAX_PERSONAS_PASADIA} con cargo por persona. No incluye pernocta. Se puede '
                'agregar una habitación de uso de día como extra (en Básico, una; en Premium, '
                'una segunda); su precio se ve en el cotizador web.'
            ),
            'cotizador_web': f'{URL_COTIZADOR}?servicio=PASADIA',
        }

    if servicio == 'HOSPEDAJE':
        habitaciones = Producto.objects.filter(
            rol_cotizador='HABITACION_HOSPEDAJE', visible_cotizador=True,
        ).order_by('orden_cotizador', 'nombre')
        return {
            'servicio': 'HOSPEDAJE',
            'habitaciones': [{
                'habitacion_id': h.id,
                'nombre': h.nombre,
                'precio_por_noche': _formato(estimar_total(
                    servicio='HOSPEDAJE', habitaciones_ids=[h.id], noches=1,
                    num_personas=h.capacidad_base_hospedaje or 1)['total']),
                'capacidad_incluida': h.capacidad_base_hospedaje,
                'descripcion': h.descripcion or h.descripcion_corta,
            } for h in habitaciones],
            'reglas': (
                'Check-in 2:00 p.m., check-out 10:00 a.m. Por habitación se admiten hasta '
                f'{MAX_PERSONAS_EXTRA_POR_HABITACION} personas extra sobre su capacidad, con cargo por noche.'
            ),
            'cotizador_web': f'{URL_COTIZADOR}?servicio=HOSPEDAJE',
        }

    return {'error': 'Servicio no válido: EVENTO, PASADIA u HOSPEDAJE.'}


def cotizar_estimado(servicio=None, personas=None, fecha=None, paquete_id=None, horas=None,
                     nivel_pasadia=None, habitaciones_ids=None, noches=None):
    """Mismas validaciones de aforo que `cotizador_enviar`, y el total de `estimar_total`."""
    servicio = str(servicio or '').upper()
    if servicio not in SERVICIOS:
        return {'error': 'Servicio no válido: EVENTO, PASADIA u HOSPEDAJE.'}
    personas = _entero(personas)
    if not personas or personas < 1:
        return {'error': 'Falta el número de personas: pídeselo al cliente.'}

    inicio = None
    if fecha:
        inicio, error = _fecha(fecha)
        if error:
            return {'error': error}

    horas_ev = _entero(horas, HORAS_BASE_EVENTO) or HORAS_BASE_EVENTO
    noches_n = _entero(noches, 1) or 1
    hab_ids = [i for i in (_entero(x) for x in (habitaciones_ids or [])) if i]

    if servicio == 'EVENTO':
        if personas > MAX_PERSONAS_EVENTO:
            return {'error': f'No hay eventos de más de {MAX_PERSONAS_EVENTO} personas.'}
        if horas_ev > HORAS_MAX_EVENTO:
            return {'error': f'Un evento dura como máximo {HORAS_MAX_EVENTO} horas.'}
        if paquete_id in (None, ''):
            # Solo la renta del lugar: misma regla de aforo mínimo que el cotizador web.
            if personas < MIN_PERSONAS_PERSONALIZADO_EVENTO:
                return {'error': f'La renta sola del lugar es a partir de '
                                 f'{MIN_PERSONAS_PERSONALIZADO_EVENTO} personas; con menos, '
                                 'ofrece los paquetes (ver_opciones).'}
        elif not Producto.objects.filter(
            id=_entero(paquete_id) or 0, es_paquete=True, visible_cotizador=True,
        ).exists():
            return {'error': 'Ese paquete no existe: usa ver_opciones.'}
    elif servicio == 'PASADIA':
        if personas > MAX_PERSONAS_PASADIA:
            return {'error': f'La pasadía admite como máximo {MAX_PERSONAS_PASADIA} personas; '
                             'para más, ofrece un evento.'}
    elif servicio == 'HOSPEDAJE':
        habitaciones = list(Producto.objects.filter(
            id__in=hab_ids, rol_cotizador='HABITACION_HOSPEDAJE', visible_cotizador=True,
        ).values_list('capacidad_base_hospedaje', flat=True))
        if not habitaciones:
            return {'error': 'Falta elegir habitación (usa ver_opciones).'}
        tope = sum(c + MAX_PERSONAS_EXTRA_POR_HABITACION for c in habitaciones)
        if personas > tope:
            return {'error': f'Esas habitaciones admiten como máximo {tope} huéspedes.'}
        if noches_n > NOCHES_HOSPEDAJE_MAX:
            return {'error': f'Máximo {NOCHES_HOSPEDAJE_MAX} noches por reservación.'}

    r = estimar_total(
        servicio=servicio,
        num_personas=personas,
        horas_evento=horas_ev,
        paquete_id=paquete_id,
        noches=noches_n,
        habitaciones_ids=hab_ids,
        nivel_pasadia=nivel_pasadia or 'BASICO',
        fecha=inicio,
    )
    resultado = {
        'servicio': servicio,
        'personas': r['personas'],
        'conceptos': r['conceptos'],
        'total': _formato(r['total']),
        'leyenda': r['leyenda'],
        'es_estimado': True,
        'cotizador_web': f'{URL_COTIZADOR}?servicio={servicio}',
    }
    if r['descuentos']:
        resultado['promociones'] = r['descuentos']
        resultado['precio_regular'] = _formato(r['total_sin_descuento'])
    if inicio:
        resultado['disponibilidad'] = _disponibilidad(
            inicio, r['noches'] if servicio == 'HOSPEDAJE' else 0)
    return resultado


def condiciones_de_pago(servicio=None, fecha=None):
    """Reglas de anticipo y liquidación tal como las aplica el portal de pago."""
    servicio = str(servicio or '').upper()
    if servicio not in SERVICIOS:
        return {'error': 'Servicio no válido: EVENTO, PASADIA u HOSPEDAJE.'}
    # Instancia sin guardar: solo para leer las mismas reglas que usa el
    # portal (el % configurado en ConstanteSistema y los días por servicio).
    referencia = Cotizacion(tipo_servicio=servicio)
    dias = Cotizacion.DIAS_PAGO_TOTAL[servicio]
    primer_pago = Cotizacion.PORCENTAJE_PRIMER_PAGO
    # Si el % que aparta la fecha no pasa del mínimo del primer pago, todo
    # primer pago la aparta: decir dos porcentajes distintos solo confunde.
    aparta = referencia.porcentaje_anticipo_confirmacion()
    resultado = {
        'servicio': servicio,
        'primer_pago_minimo': f'{primer_pago:.0f}% del total',
        'aparta_la_fecha': ('Con el primer pago.' if aparta <= primer_pago
                            else f'Al llevar pagado el {aparta:.0f}% del total.'),
        'liquidar': f'El saldo completo a más tardar {dias} días antes de la fecha.',
        'si_faltan_menos_dias': f'Con menos de {dias} días de anticipación se paga el total en un solo pago.',
        'como_se_paga': ('En línea desde el portal del cliente, que recibe al cotizar en el cotizador web: '
                         'tarjeta de crédito o débito, transferencia SPEI o efectivo en tiendas.'),
    }
    if fecha:
        inicio, error = _fecha(fecha)
        if error:
            return {'error': error}
        limite = inicio - timedelta(days=dias)
        resultado['fecha_limite_para_liquidar'] = limite.isoformat()
        resultado['paga_total_desde_el_inicio'] = limite <= timezone.localdate()
    return resultado


def preguntas_frecuentes():
    return {'preguntas': [
        {'pregunta': p.pregunta, 'respuesta': p.respuesta}
        for p in PreguntaFrecuente.objects.filter(activo=True)
        .exclude(respuesta__contains=PreguntaFrecuente.MARCADOR_COMPLETAR).order_by('orden', 'id')
    ]}


def cotizaciones_del_telefono(telefono: str):
    """Cotizaciones vigentes de los clientes con este número (últimos 10 dígitos,
    mismo criterio que liga la conversación con su cliente). Sin número válido,
    ninguna: nunca se buscan por un dato que dicte el modelo o el cliente."""
    digitos = ''.join(filter(str.isdigit, telefono or ''))
    if len(digitos) < 10:
        return Cotizacion.objects.none()
    return (Cotizacion.objects
            .filter(cliente__telefono__endswith=digitos[-10:])
            .exclude(estado__in=Cotizacion.ESTADOS_SIN_COBRO)
            .filter(fecha_evento__gte=timezone.localdate() - timedelta(days=1))
            .order_by('fecha_evento', 'id'))


def mi_reservacion(conv=None):
    """Saldo y estado de las reservaciones del número que escribe.

    La conversación la pone `ejecutar`, nunca el modelo: el esquema de la
    herramienta no tiene parámetros, así que el cliente no puede pedir las de
    otro número. No se manda el enlace con token del portal: se entra por el
    acceso con código y 4 dígitos.
    """
    reservaciones = []
    for c in cotizaciones_del_telefono(getattr(conv, 'telefono', '')):
        saldo = c.saldo_pendiente()
        minimo, motivo = c.monto_minimo_pago_detalle()
        dias = Cotizacion.DIAS_PAGO_TOTAL.get(c.tipo_servicio)
        datos = {
            'folio': f'COT-{c.id:03d}',
            'servicio': c.get_tipo_servicio_display(),
            'fecha': c.fecha_evento.isoformat(),
            'estado': c.get_estado_display(),
            'fecha_apartada': c.estado not in Cotizacion.ESTADOS_SIN_APARTAR,
            'total': _formato(c.precio_final),
            'pagado': _formato(c.total_pagado()),
            'saldo': _formato(saldo),
        }
        if c.fecha_salida:
            datos['fecha_salida'] = c.fecha_salida.isoformat()
        if saldo > 0:
            datos['minimo_a_pagar_hoy'] = _formato(minimo)
            if motivo:
                datos['motivo_minimo'] = motivo
            if dias is not None:
                datos['fecha_limite_para_liquidar'] = (c.fecha_evento - timedelta(days=dias)).isoformat()
        reservaciones.append(datos)
    if not reservaciones:
        return {'reservaciones': [],
                'nota': 'No hay reservaciones vigentes con este número de WhatsApp. Si cotizó con '
                        'otro número, ofrece pasar con una persona.'}
    return {
        'reservaciones': reservaciones,
        'para_pagar_o_ver_contrato': f'{URL_PORTAL_ACCESO} con su folio y los últimos 4 dígitos de su teléfono.',
    }


# Cotizaciones que un mismo número puede crear por día en el chat: más que eso
# es un abuso o un caso para una persona.
MAX_COTIZACIONES_CHAT_DIA = 3


def crear_cotizacion(conv=None, servicio=None, fecha=None, personas=None, nombre=None, correo=None,
                     paquete_id=None, hora_inicio=None, hora_fin=None, tipo_evento=None,
                     nivel_pasadia=None, habitaciones_ids=None, noches=None, notas=None):
    """Crea la cotización con la misma función que el cotizador web.

    El teléfono sale de la conversación (nunca del modelo) y la evidencia del
    consentimiento es el botón que tocó el cliente en este chat.
    """
    from comunicacion import services_agente  # importa este módulo: va aquí

    if conv is None:
        return {'error': 'Sin conversación.'}
    if not services_agente.consentimiento_vigente(conv):
        enviado = services_agente.pedir_consentimiento(conv)
        return {'requiere_autorizacion': True, 'nota': (
            'Se le acaba de mandar al cliente un mensaje con botones para aceptar el Aviso de '
            'Privacidad y los Términos. Pídele que toque «Acepto» y, cuando lo haga, vuelve a '
            'llamar crear_cotizacion.' if enviado else
            'Ya tiene en el chat el mensaje con botones para aceptar el Aviso de Privacidad; pídele '
            'que toque «Acepto». Sin eso no se puede crear la cotización.')}

    correo = str(correo or '').strip().lower()
    try:
        validate_email(correo)
    except ValidationError:
        return {'error': 'El correo no parece válido: confírmalo con el cliente.'}
    inicio, error = _fecha(fecha)
    if error:
        return {'error': error}
    servicio = str(servicio or '').upper()
    noches_n = _entero(noches, 1) or 1
    # Mismos topes de aforo, paquete y habitaciones que el estimado: el
    # cotizador web los aplica en su formulario, no en la función compartida.
    horas = None
    if hora_inicio and hora_fin:
        try:
            h_i = datetime.strptime(hora_inicio, '%H:%M')
            h_f = datetime.strptime(hora_fin, '%H:%M')
        except ValueError:
            return {'error': 'Horario no válido: usa HH:MM en 24 h.'}
        horas = int(((h_f - h_i).total_seconds() % 86400) / 3600) or 24
    estimado = cotizar_estimado(servicio=servicio, personas=personas, fecha=inicio.isoformat(),
                                paquete_id=paquete_id, horas=horas, nivel_pasadia=nivel_pasadia,
                                habitaciones_ids=habitaciones_ids, noches=noches_n)
    if 'error' in estimado:
        return estimado
    if not estimado['disponibilidad']['disponible']:
        return {'error': 'Esa fecha ya está ocupada: sugiere otra con consultar_disponibilidad.'}

    hoy = timezone.localdate()
    creadas_hoy = Cotizacion.objects.filter(
        cliente__telefono__endswith=conv.telefono[-10:], created_at__date=hoy).count()
    if creadas_hoy >= MAX_COTIZACIONES_CHAT_DIA:
        return {'error': 'Este número ya creó varias cotizaciones hoy: ofrece pasar con una persona.'}

    datos = {
        'nombre': str(nombre or '').strip(), 'telefono': conv.telefono, 'email': correo,
        'servicio': servicio, 'fecha': inicio.isoformat(), 'personas': str(_entero(personas, '') or ''),
        'noches': str(noches_n), 'hora_inicio': hora_inicio or '', 'hora_fin': hora_fin or '',
        'tipo_evento': tipo_evento or '', 'notas': str(notas or '')[:300], 'acepta_legales': True,
        'paquete_id': paquete_id or '', 'habitaciones_ids': habitaciones_ids or [],
        'nivel_pasadia': nivel_pasadia or 'BASICO',
        'finalidades': ['MARKETING'] if conv.consentimiento_marketing else [],
    }

    def _consentimiento_whatsapp(cliente, email, finalidades):
        from legal.models import OrigenAceptacion
        from legal.services import LegalService
        LegalService.registrar_aceptacion(
            request=None, correo=email, origen=OrigenAceptacion.WHATSAPP, cliente=cliente,
            finalidades_aceptadas=finalidades, referencia_externa=conv.consentimiento_wamid,
            aceptado_en=conv.consentimiento_en,
        )

    try:
        r = crear_cotizacion_solicitud(datos, origen_cliente='WhatsApp',
                                       registrar_consentimiento=_consentimiento_whatsapp)
    except SolicitudInvalida as e:
        return {'error': ' '.join(e.errores)}
    cot = r['cotizacion']
    if conv.cliente_id != cot.cliente_id:
        conv.cliente = cot.cliente
        conv.save(update_fields=['cliente', 'updated_at'])
    return {
        'folio': f'COT-{cot.id:03d}',
        'total': _formato(cot.precio_final),
        'conceptos': [i.descripcion for i in cot.items.all()],
        # El enlace con token no pasa por el modelo: le llega al cliente en
        # el aviso de cotización (WhatsApp y correo).
        'nota': ('La cotización quedó creada. El cliente recibe en mensaje aparte y por correo el enlace '
                 'a su portal, donde paga y ve su contrato. Si no le llega, puede entrar en '
                 f'{URL_PORTAL_ACCESO} con su folio y los últimos 4 dígitos de su teléfono. La fecha se '
                 'aparta con el primer pago.'),
    }


def registrar_pregunta_sin_respuesta(conv=None, pregunta=None):
    from comunicacion.models import PreguntaSinRespuesta
    pregunta = str(pregunta or '').strip()[:300]
    if conv is None or not pregunta:
        return {'error': 'Falta la pregunta.'}
    PreguntaSinRespuesta.objects.create(conversacion=conv, pregunta=pregunta)
    return {'ok': True, 'nota': 'Quedó anotada para el equipo. Dile al cliente que no tienes ese dato y '
                                'ofrécele pasar con una persona.'}


_EJECUTORES = {
    'consultar_disponibilidad': consultar_disponibilidad,
    'ver_opciones': ver_opciones,
    'cotizar_estimado': cotizar_estimado,
    'condiciones_de_pago': condiciones_de_pago,
    'preguntas_frecuentes': preguntas_frecuentes,
}
# Herramientas que dependen de quién escribe: reciben la conversación, nunca
# un teléfono que pueda dictar el modelo.
_EJECUTORES_DEL_CLIENTE = {
    'mi_reservacion': mi_reservacion,
    'crear_cotizacion': crear_cotizacion,
    'registrar_pregunta_sin_respuesta': registrar_pregunta_sin_respuesta,
}


def ejecutar(nombre: str, entrada: dict, conv=None) -> tuple[str, bool]:
    """Ejecuta una herramienta de consulta. Devuelve (json, es_error).

    `pasar_a_humano` no vive aquí: cambia el estado de la conversación y lo
    resuelve `services_agente`, que sí la conoce.
    """
    entrada = dict(entrada or {})
    funcion = _EJECUTORES.get(nombre)
    if nombre in _EJECUTORES_DEL_CLIENTE:
        funcion = _EJECUTORES_DEL_CLIENTE[nombre]
        entrada['conv'] = conv
    if funcion is None:
        return json.dumps({'error': f'Herramienta desconocida: {nombre}'}), True
    try:
        resultado = funcion(**entrada)
    except TypeError:
        return json.dumps({'error': 'Parámetros no válidos para la herramienta.'}), True
    except Exception:
        logger.exception("Agente WhatsApp: falló la herramienta %s", nombre)
        return json.dumps({'error': 'No se pudo consultar la información; ofrece pasar con una persona.'}), True
    return json.dumps(resultado, ensure_ascii=False, default=str), 'error' in resultado
