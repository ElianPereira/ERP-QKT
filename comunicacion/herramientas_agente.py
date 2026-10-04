"""
Herramientas del agente de WhatsApp (Issue #346, fase 1: solo lectura).

Cada herramienta es una consulta al ERP que el modelo puede pedir; nada de lo
que está aquí escribe en la base. Los precios salen de
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

from django.utils import timezone

from comercial.disponibilidad import verificar_disponibilidad_rango
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
    estimar_total,
)

logger = logging.getLogger(__name__)

URL_COTIZADOR = 'https://clientes.quintakooxtanil.com/cotizar/'
SERVICIOS = ('EVENTO', 'PASADIA', 'HOSPEDAJE')

HERRAMIENTAS = [
    {
        'name': 'consultar_disponibilidad',
        'description': (
            'Revisa en el ERP si una fecha (o una estancia de hospedaje de varias noches) '
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
            'y lo que incluye cada opción, tal como está capturado en el ERP: paquetes de '
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
            'Reglas de pago del ERP: cuánto se paga para apartar, cuándo se liquida y cómo se '
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
        for p in PreguntaFrecuente.objects.filter(activo=True).order_by('orden', 'id')
    ]}


_EJECUTORES = {
    'consultar_disponibilidad': consultar_disponibilidad,
    'ver_opciones': ver_opciones,
    'cotizar_estimado': cotizar_estimado,
    'condiciones_de_pago': condiciones_de_pago,
    'preguntas_frecuentes': preguntas_frecuentes,
}


def ejecutar(nombre: str, entrada: dict) -> tuple[str, bool]:
    """Ejecuta una herramienta de consulta. Devuelve (json, es_error).

    `pasar_a_humano` no vive aquí: cambia el estado de la conversación y lo
    resuelve `services_agente`, que sí la conoce.
    """
    funcion = _EJECUTORES.get(nombre)
    if funcion is None:
        return json.dumps({'error': f'Herramienta desconocida: {nombre}'}), True
    try:
        resultado = funcion(**(entrada or {}))
    except TypeError:
        return json.dumps({'error': 'Parámetros no válidos para la herramienta.'}), True
    except Exception:
        logger.exception("Agente WhatsApp: falló la herramienta %s", nombre)
        return json.dumps({'error': 'No se pudo consultar el ERP; ofrece pasar con una persona.'}), True
    return json.dumps(resultado, ensure_ascii=False, default=str), 'error' in resultado
