"""
Renovación preparada y cierre del trámite por WhatsApp (Fase 3, Issue #434).

A 45 días del vencimiento el ERP abre la renovación prellenada (la misma que
arma `services.datos_renovacion`), copia al expediente los documentos vigentes
por requisito y le avisa a Dirección lo que falta. Hacer el trámite sigue
siendo humano. Para cerrarlo, Dirección manda la foto o el PDF del documento
nuevo a Kooxi: queda como vigencia POR CONFIRMAR con las fechas propuestas y un
botón; solo al tocarlo pasa a OBTENIDA.
"""
import logging
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from comunicacion.formato_wa import Aviso
from comunicacion.models import Pendiente
from comunicacion.services import descargar_media_wa, normalizar_telefono_wa
from comunicacion.services_bajas import _normalizar
from comunicacion.services_pendientes import _base_url, _enviar_aviso, registrar_pendiente, resolver
from comunicacion.services_responsables import rol_de_telefono, telefono_de

from . import services
from .models import DocumentoExpediente, Vigencia

logger = logging.getLogger(__name__)

DIAS_ANTICIPACION = 45
RE_BOTON = re.compile(r'^vig:(\d+):ok$')


def id_boton(vigencia_id: int) -> str:
    return f'vig:{vigencia_id}:ok'


def es_boton(texto: str) -> bool:
    return bool(RE_BOTON.match((texto or '').strip()))


def _url(vigencia) -> str:
    return _base_url() + reverse('admin:cumplimiento_vigencia_change', args=[vigencia.pk])


# ─────────────────────────── Renovación a 45 días ───────────────────────────

def tramites_por_renovar(hoy=None):
    """`(trámite, vigencia actual, días al vencimiento)` de lo que ya entra en
    la ventana de 45 días (o ya venció) y no tiene renovación abierta."""
    hoy = hoy or timezone.localdate()
    encontrados = []
    for tramite in services._tramites_activos():
        actual = services.vigencia_actual(tramite)
        if actual is None or actual.fecha_vencimiento is None or not tramite.periodicidad_meses:
            continue
        if tramite.tipo == 'CONTRATO' and tramite.renovacion_automatica:
            continue  # se renueva solo; lo cubre el preaviso de cancelación
        dias = (actual.fecha_vencimiento - hoy).days
        if dias > DIAS_ANTICIPACION or services.renovacion_en_curso(tramite):
            continue
        encontrados.append((tramite, actual, dias))
    return encontrados


def _copiar_documentos(actual, nueva) -> int:
    """Copia a la renovación el documento más reciente de cada requisito activo
    (referencia al mismo archivo, sin duplicarlo en el storage)."""
    copiados = 0
    vistos = set()
    for doc in actual.documentos.filter(activo=True, requisito__activo=True).order_by('-id'):
        if doc.requisito_id in vistos:
            continue
        vistos.add(doc.requisito_id)
        DocumentoExpediente.objects.create(
            vigencia=nueva, requisito=doc.requisito, nombre=doc.nombre, archivo=doc.archivo.name,
            fecha_documento=doc.fecha_documento)
        copiados += 1
    return copiados


def abrir_renovacion(tramite, actual, dias=None):
    """Abre la renovación prellenada y avisa a Dirección. Una sola vez por
    vigencia vencible (el pendiente guarda la clave aunque la cancelen)."""
    clave = f'renovacion:{actual.pk}'
    if Pendiente.objects.filter(clave_idempotencia=clave).exists():
        return None
    datos = services.datos_renovacion(tramite)
    with transaction.atomic():
        nueva = Vigencia.objects.create(
            tramite=tramite, estado='EN_TRAMITE',
            fecha_inicio=date.fromisoformat(datos['fecha_inicio']) if datos.get('fecha_inicio') else None,
            fecha_vencimiento=date.fromisoformat(datos['fecha_vencimiento']) if datos.get('fecha_vencimiento') else None,
            costo=Decimal(datos.get('costo') or '0'),
            notas='Renovación abierta automáticamente 45 días antes del vencimiento.')
        copiados = _copiar_documentos(actual, nueva)
    _, _, faltantes = services.avance_requisitos(nueva)
    vence = actual.fecha_vencimiento
    cuando = services.dias_texto(dias if dias is not None else (vence - timezone.localdate()).days).lower()
    detalle = (f'Vence el {vence:%d/%m/%Y} ({cuando}). Ya abrí la renovación con {copiados} documento(s) del '
               'expediente anterior.')
    nombres_faltantes = ', '.join(r.nombre for r in faltantes)
    if faltantes:
        detalle += f' Faltan: {nombres_faltantes}.'
    pendiente = registrar_pendiente(
        titulo=f'Renovar {tramite.nombre}', rol='DIRECCION', tipo='CUMPLIMIENTO', objeto=nueva,
        detalle=detalle, clave_idempotencia=clave)
    destino = telefono_de('DIRECCION')
    if destino and pendiente is not None:
        aviso = Aviso(
            titulo=f'Renovar {tramite.nombre}', categoria='Cumplimiento',
            lineas=[f'Vence el {vence:%d/%m/%Y} ({cuando})',
                    f'Ya abrí la renovación con {copiados} documento(s) del expediente anterior',
                    *([f'Faltan: {nombres_faltantes}'] if faltantes else [])],
            cierre='Revisa y haz el trámite.', enlace=_url(nueva))
        _enviar_aviso(destino, aviso, clave=f'{clave}:whatsapp')
    return nueva


# ───────────────────── Cierre: el documento nuevo por WhatsApp ───────────────────

def _renovaciones_abiertas():
    return list(Vigencia.objects.filter(estado='EN_TRAMITE', tramite__activo=True).select_related('tramite'))


def _elegir_renovacion(abiertas, pie: str):
    """La renovación a la que se refiere el pie de foto, o la única abierta.
    Devuelve `(vigencia | None, aclaración | None)`."""
    pie = _normalizar(pie or '')
    por_nombre = [v for v in abiertas if _normalizar(v.tramite.nombre) in pie]
    if len(por_nombre) == 1:
        return por_nombre[0], None
    if len(abiertas) == 1 and not por_nombre:
        return abiertas[0], None
    nombres = ', '.join(v.tramite.nombre for v in (por_nombre or abiertas))
    return None, (f'Tengo varias renovaciones abiertas ({nombres}). Reenvía el documento escribiendo '
                  'el nombre del trámite en el pie de la foto.')


def recibir_documento(telefono: str, media_id: str, pie: str = ''):
    """Dirección mandó un archivo: si hay renovaciones abiertas, lo adjunta a la
    que corresponde como vigencia por confirmar. Devuelve `(atendido, respuesta)`:
    `atendido` es False si el archivo no es de este flujo (p. ej. una foto de
    daños, que lleva «cot» en el pie)."""
    if rol_de_telefono(telefono) != 'DIRECCION':
        return False, None
    abiertas = _renovaciones_abiertas()
    if not abiertas or re.search(r'\bcot', _normalizar(pie or '')):
        return False, None
    vigencia, aclaracion = _elegir_renovacion(abiertas, pie)
    if vigencia is None:
        return True, aclaracion
    try:
        contenido, _, extension = descargar_media_wa(media_id)
    except ValueError as e:
        return True, str(e)
    propuesta = services.datos_renovacion(vigencia.tramite)
    with transaction.atomic():
        vigencia = Vigencia.objects.select_for_update().get(pk=vigencia.pk)
        if vigencia.estado != 'EN_TRAMITE':
            return True, 'Esa renovación ya no está en trámite.'
        nombre = f'{vigencia.tramite.nombre[:60]}-{timezone.localdate():%Y%m%d}{extension}'
        vigencia.documento.save(nombre, ContentFile(contenido), save=False)
        if not vigencia.fecha_inicio and propuesta.get('fecha_inicio'):
            vigencia.fecha_inicio = date.fromisoformat(propuesta['fecha_inicio'])
        if not vigencia.fecha_vencimiento and propuesta.get('fecha_vencimiento'):
            vigencia.fecha_vencimiento = date.fromisoformat(propuesta['fecha_vencimiento'])
        vigencia.por_confirmar = True
        vigencia.save()
    propuesta_fechas = (f'Vigencia propuesta: {vigencia.fecha_inicio:%d/%m/%Y} al {vigencia.fecha_vencimiento:%d/%m/%Y}'
                        if vigencia.fecha_inicio and vigencia.fecha_vencimiento else 'No pude proponer fechas')
    aviso = Aviso(titulo=f'Recibí el documento de {vigencia.tramite.nombre}', categoria='Cumplimiento',
                  lineas=[propuesta_fechas], cierre='Si son correctas, toca Confirmar; si no, corrígelas aquí.',
                  enlace=_url(vigencia))
    _enviar_aviso(normalizar_telefono_wa(telefono), aviso, botones=[(id_boton(vigencia.pk), 'Confirmar')])
    return True, None


def atender_boton(telefono: str, texto: str) -> Optional[str]:
    """Botón `vig:<id>:ok`: solo vale desde el teléfono de Dirección. Devuelve el
    texto con que contestarle, o None si no hizo nada."""
    coincide = RE_BOTON.match((texto or '').strip())
    if not coincide or rol_de_telefono(telefono) != 'DIRECCION':
        return None
    vigencia = Vigencia.objects.select_related('tramite').filter(pk=int(coincide.group(1))).first()
    if vigencia is None or not vigencia.por_confirmar or vigencia.estado != 'EN_TRAMITE':
        return None
    vigencia.estado = 'OBTENIDA'
    vigencia.por_confirmar = False
    try:
        vigencia.full_clean()
    except ValidationError as e:
        detalle = ' '.join(m for lista in e.message_dict.values() for m in lista)
        return f'No pude confirmarla: {detalle} Corrígela aquí: {_url(vigencia)}'
    vigencia.save()
    tipo = ContentType.objects.get_for_model(Vigencia)
    for p in Pendiente.objects.filter(content_type=tipo, object_id=vigencia.pk, estado='ABIERTO'):
        resolver(p)
    if vigencia.fecha_vencimiento:
        return f'✅ Listo, {vigencia.tramite.nombre} quedó vigente hasta el {vigencia.fecha_vencimiento:%d/%m/%Y}.'
    return f'✅ Listo, {vigencia.tramite.nombre} quedó registrado como vigente.'
