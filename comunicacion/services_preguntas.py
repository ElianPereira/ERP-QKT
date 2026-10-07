"""
Resumen semanal de lo que el agente de WhatsApp no supo contestar (Issue #366).

Junta las `PreguntaSinRespuesta` aún no resumidas, le pide a un modelo que las
agrupe y que proponga una respuesta **solo con la información ya capturada**
(preguntas frecuentes y descripciones de productos), y deja cada propuesta
como `PreguntaFrecuente` **inactiva**: el agente no la ve hasta que alguien la
revisa y la activa en el admin. Al final avisa al propietario por WhatsApp.

Si el modelo no tiene con qué contestar, la respuesta queda con el marcador
`[COMPLETAR]` para que se capture a mano; nunca se inventa un dato.
"""
import json
import logging

import anthropic
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from comercial.models import PreguntaFrecuente, Producto

from .models import PreguntaSinRespuesta

logger = logging.getLogger(__name__)

MARCADOR_COMPLETAR = PreguntaFrecuente.MARCADOR_COMPLETAR
MAX_GRUPOS = 15

INSTRUCCIONES = f"""Ayudas al dueño de una quinta de eventos en Yucatán a completar las preguntas \
frecuentes de su asistente de WhatsApp. Recibes las dudas que el asistente no supo contestar y la \
información que el negocio ya tiene capturada.

1. Agrupa las dudas que preguntan lo mismo y redacta cada grupo como una sola pregunta clara y \
general (sin nombres ni datos personales), de máximo 200 caracteres.
2. Propón una respuesta corta, en el tono de un anfitrión amable, usando SOLO la información \
capturada. Si esa información no alcanza para contestar con certeza, la respuesta debe ser \
exactamente «{MARCADOR_COMPLETAR}». Nunca inventes precios, horarios, políticas ni servicios.
3. Ordena de la duda más repetida a la menos repetida; máximo {MAX_GRUPOS} grupos."""

_ESQUEMA = {
    'type': 'object',
    'properties': {
        'grupos': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'pregunta': {'type': 'string'},
                    'respuesta_sugerida': {'type': 'string'},
                    'veces': {'type': 'integer'},
                },
                'required': ['pregunta', 'respuesta_sugerida', 'veces'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['grupos'],
    'additionalProperties': False,
}


def _contexto_capturado() -> str:
    faqs = PreguntaFrecuente.objects.filter(activo=True).order_by('orden', 'id')
    productos = Producto.objects.filter(visible_cotizador=True).exclude(descripcion='').order_by('nombre')
    partes = ['Preguntas frecuentes vigentes:']
    partes += [f'- {f.pregunta}: {f.respuesta}' for f in faqs] or ['- (ninguna)']
    partes.append('\nServicios y productos:')
    partes += [f'- {p.nombre}: {p.descripcion}' for p in productos] or ['- (ninguno)']
    return '\n'.join(partes)


def _cliente():
    return anthropic.Anthropic(timeout=60.0, max_retries=2)


def _agrupar(dudas: list[str]) -> list[dict]:
    contenido = (f'{_contexto_capturado()}\n\nDudas sin respuesta de esta semana:\n'
                 + '\n'.join(f'- {d}' for d in dudas))
    r = _cliente().messages.create(
        model=settings.WA_AGENTE_MODELO_JUEZ,
        max_tokens=4000,
        system=INSTRUCCIONES,
        messages=[{'role': 'user', 'content': contenido}],
        output_config={'format': {'type': 'json_schema', 'schema': _ESQUEMA}},
    )
    datos = json.loads(next(b.text for b in r.content if b.type == 'text'))
    return datos['grupos'][:MAX_GRUPOS]


def resumir_preguntas(aplicar: bool = False) -> dict:
    """Agrupa las dudas pendientes, crea borradores inactivos y avisa al propietario.

    Sin `aplicar` solo dice cuántas dudas hay. Si el modelo falla, no se marca
    nada como resumido: la siguiente corrida lo reintenta.
    """
    pendientes = list(PreguntaSinRespuesta.objects.filter(resumida_en__isnull=True)
                      .values_list('pk', 'pregunta'))
    dudas = [pregunta for _, pregunta in pendientes]
    resultado = {'dudas': len(dudas), 'borradores': 0, 'por_completar': 0}
    if not dudas or not aplicar:
        return resultado

    grupos = _agrupar(dudas)
    with transaction.atomic():
        for g in grupos:
            respuesta = (g.get('respuesta_sugerida') or '').strip() or MARCADOR_COMPLETAR
            PreguntaFrecuente.objects.create(
                pregunta=(g.get('pregunta') or '').strip()[:200] or 'Pregunta sin título',
                respuesta=respuesta, activo=False, orden=999,
            )
            resultado['borradores'] += 1
            resultado['por_completar'] += MARCADOR_COMPLETAR in respuesta
        PreguntaSinRespuesta.objects.filter(pk__in=[pk for pk, _ in pendientes]).update(
            resumida_en=timezone.now())

    if resultado['borradores']:
        from .services_agente import _avisar_propietario
        _avisar_propietario(
            f"Kooxi no supo contestar {resultado['dudas']} dudas. Dejé {resultado['borradores']} "
            f"preguntas frecuentes en borrador ({resultado['por_completar']} sin respuesta) para que las "
            'revises y actives en Admin, Preguntas')
    return resultado
