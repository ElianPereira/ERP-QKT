"""
Inspección del depósito en garantía por WhatsApp (Fase 3, Issue #434).

MUEVE DINERO. Tras el servicio se le pide al ENCARGADO «Sin daños» (botón) o
`daños <monto> cot <n> <descripción>` más al menos una foto. Todo dinero sale
por `comercial.services_deposito.liquidar`, nunca por otra vía:

- «Sin daños», o sin reporte en `ERP_DEPOSITO_INSPECCION_HORAS` → devolución total.
- Daños hasta `ERP_DEPOSITO_TOPE_RETENCION` → se retiene lo reportado.
- Daños por encima del tope (con tope 0, cualquiera) → no se mueve nada hasta
  que DIRECCION toque Aprobar (retiene) o Devolver todo.

Solo se automatiza lo que se puede cumplir de verdad: si lo que hay que devolver
no se cobró con tarjeta en Openpay (SPEI, efectivo, mixto), `liquidar` solo
*registraría* una devolución que nadie hizo, así que la inspección queda en
MANUAL con un pendiente URGENTE para Dirección. El estado PROCESANDO se toma con
un UPDATE condicional antes de llamar a `liquidar`: dos corridas, o un botón y
el cron a la vez, no pueden reembolsar dos veces, y una corrida que se muera a
medias queda PROCESANDO (se avisa, no se reintenta sola).
"""
import logging
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from comercial import services_deposito
from comercial.models import DepositoGarantia
from core_erp.impuestos import centavos

from .models import FotoInspeccion, InspeccionDeposito, Pendiente
from .services import descargar_media_wa, normalizar_telefono_wa
from .services_pendientes import _base_url, _enviar_aviso, registrar_pendiente, resolver
from .services_responsables import SIN_DESTINO, rol_de_telefono, telefono_de, telefonos_aviso

logger = logging.getLogger(__name__)

ESTADOS_SERVICIO_PRESTADO = ('CONFIRMADA', 'EJECUTADA', 'CERRADA')
MAX_DIAS_ATRAS = 14  # lo más viejo se atiende a mano: no se barre el histórico del primer día
ESTADOS_ABIERTOS = ('SOLICITADA', 'DANOS')
ROLES_QUE_REPORTAN = ('ENCARGADO', 'DIRECCION')
PROCESANDO_MAX = timedelta(hours=1)

RE_BOTON = re.compile(r'^insp:(\d+):(ok|apr|rech)$')
RE_SIN_DANOS = re.compile(r'^\s*sin\s+da[ñn]os?(?:\s+cot(?:izaci[oó]n)?\s*-?(\d+))?\s*$', re.IGNORECASE)
RE_DANOS = re.compile(
    r'^\s*da[ñn]os?\s+\$?\s*(\d[\d,]*(?:\.\d{1,2})?)(?:\s+cot(?:izaci[oó]n)?\s*-?(\d+))?\s*(.*)$',
    re.IGNORECASE | re.DOTALL)
MIN_DESGLOSE = 5


def id_boton(inspeccion_id: int, accion: str) -> str:
    return f'insp:{inspeccion_id}:{accion}'


def es_boton(texto: str) -> bool:
    return bool(RE_BOTON.match((texto or '').strip()))


def horas_espera() -> int:
    return int(getattr(settings, 'ERP_DEPOSITO_INSPECCION_HORAS', 48) or 48)


def tope_retencion() -> Decimal:
    """Retención máxima que se aplica sin el botón de Dirección (0 si falta o es inválida)."""
    try:
        return max(centavos(Decimal(str(getattr(settings, 'ERP_DEPOSITO_TOPE_RETENCION', '0') or '0'))), Decimal('0'))
    except (InvalidOperation, ValueError):
        return Decimal('0.00')


def _folio(deposito) -> str:
    return f'COT-{deposito.cotizacion_id:03d}'


def _url(inspeccion) -> str:
    return _base_url() + reverse('admin:comunicacion_inspecciondeposito_change', args=[inspeccion.pk])


def _dinero(valor) -> str:
    return f'${Decimal(valor):,.2f}'


# ───────────────────────────── Qué se puede automatizar ─────────────────────────────

def automatizable(deposito, retencion: Decimal) -> bool:
    """¿Lo que hay que devolver se puede reembolsar de verdad en Openpay?"""
    devolucion = deposito.en_custodia - retencion
    if devolucion <= 0:
        return True
    tarjeta = sum((m.monto for m in services_deposito.recepciones_con_tarjeta(deposito)), Decimal('0.00'))
    return tarjeta >= devolucion


def _cerrar_pendientes(inspeccion) -> None:
    claves = [f'inspeccion:{inspeccion.pk}', f'inspeccion:{inspeccion.pk}:aprobar']
    for p in Pendiente.objects.filter(clave_idempotencia__in=claves, estado='ABIERTO'):
        resolver(p)


def _pasar_a_manual(inspeccion, motivo: str) -> None:
    """La inspección queda para una persona, con pendiente URGENTE para Dirección."""
    InspeccionDeposito.objects.filter(pk=inspeccion.pk).update(
        estado='MANUAL', nota=motivo[:1000], updated_at=timezone.now())
    deposito = inspeccion.deposito
    registrar_pendiente(
        titulo=f'Depósito de {_folio(deposito)} requiere atención manual', rol='DIRECCION', tipo='DEPOSITO',
        severidad='URGENTE', objeto=deposito.cotizacion, detalle=f'{motivo} Inspección: {_url(inspeccion)}',
        clave_idempotencia=f'inspeccion:{inspeccion.pk}:manual')
    _cerrar_pendientes(inspeccion)


def _liquidar(inspeccion_id: int, *, retencion: Decimal, resolucion: str, desglose: str = '',
              desde=('SOLICITADA',)) -> Optional[dict]:
    """Devuelve el depósito (y retiene `retencion`) UNA sola vez. None si otra
    corrida ya lo tomó o la inspección no está en un estado válido."""
    retencion = centavos(Decimal(retencion or 0))
    tomada = InspeccionDeposito.objects.filter(pk=inspeccion_id, estado__in=desde).update(
        estado='PROCESANDO', updated_at=timezone.now())
    if not tomada:
        return None
    inspeccion = InspeccionDeposito.objects.select_related('deposito__cotizacion').get(pk=inspeccion_id)
    deposito = DepositoGarantia.objects.get(pk=inspeccion.deposito_id)
    if deposito.liquidado:
        InspeccionDeposito.objects.filter(pk=inspeccion.pk).update(
            estado='LIQUIDADA', nota='El depósito ya se había liquidado por otra vía.',
            resuelto_en=timezone.now(), updated_at=timezone.now())
        _cerrar_pendientes(inspeccion)
        return {'devuelto': Decimal('0.00'), 'retenido': Decimal('0.00'), 'previo': True}
    if not automatizable(deposito, retencion):
        _pasar_a_manual(inspeccion, (
            f'Hay {_dinero(deposito.en_custodia)} en custodia y no se cobraron con tarjeta en Openpay, así que no '
            'se puede reembolsar solo: devuélvelo por transferencia y regístralo desde Depósitos.'))
        return {'manual': True}
    try:
        resultado = services_deposito.liquidar(
            deposito, retencion_danos=retencion, desglose=desglose, reembolsar_openpay=True)
    except services_deposito.DepositoError as e:
        _pasar_a_manual(inspeccion, f'No se pudo liquidar solo: {e}')
        return {'manual': True}
    except Exception:
        logger.exception("Inspección %s: falló la liquidación del depósito", inspeccion.pk)
        InspeccionDeposito.objects.filter(pk=inspeccion.pk).update(
            nota='La liquidación falló a medias: revisa el depósito y Openpay antes de reintentar.',
            updated_at=timezone.now())
        registrar_pendiente(
            titulo=f'Revisar el depósito de {_folio(deposito)}: la liquidación falló', rol='DIRECCION',
            tipo='DEPOSITO', severidad='URGENTE', objeto=deposito.cotizacion,
            detalle=f'Revisa Openpay y el depósito antes de reintentar. Inspección: {_url(inspeccion)}',
            clave_idempotencia=f'inspeccion:{inspeccion.pk}:fallo')
        return {'manual': True}
    InspeccionDeposito.objects.filter(pk=inspeccion.pk).update(
        estado='LIQUIDADA', resolucion=resolucion, resuelto_en=timezone.now(), updated_at=timezone.now(),
        nota=f'Devuelto {_dinero(resultado["devuelto"])}; retenido {_dinero(resultado["retenido"])}.')
    _cerrar_pendientes(inspeccion)
    return resultado


# ─────────────────────────────── Pedir la inspección ───────────────────────────────

def depositos_para_inspeccion(hoy=None):
    hoy = hoy or timezone.localdate()
    candidatos = (DepositoGarantia.objects
                  .filter(inspeccion__isnull=True, cotizacion__estado__in=ESTADOS_SERVICIO_PRESTADO)
                  .select_related('cotizacion__cliente'))
    return [d for d in candidatos
            if hoy - timedelta(days=MAX_DIAS_ATRAS) <= d.fin_servicio < hoy and d.recibido > 0 and not d.liquidado]


def solicitar_inspeccion(deposito):
    """Pide al encargado la inspección. Idempotente: una por depósito."""
    destinos = telefonos_aviso('ENCARGADO')
    if not destinos:
        logger.error("%s; la inspección de %s no se pidió", SIN_DESTINO, _folio(deposito))
        return None
    with transaction.atomic():
        inspeccion, creada = InspeccionDeposito.objects.get_or_create(
            deposito=deposito,
            defaults={'vence_en': timezone.now() + timedelta(hours=horas_espera())})
    if not creada:
        return None
    folio, cliente = _folio(deposito), deposito.cotizacion.cliente.nombre
    limite = timezone.localtime(inspeccion.vence_en)
    texto = (f'Inspección de {folio} ({cliente}): ¿hubo daños? Si no, toca «Sin daños». Si hubo, responde '
             f'«daños 800 cot {deposito.cotizacion_id} descripción» y manda al menos una foto. Si no hay reporte '
             f'antes del {limite:%d/%m a las %H:%M}, el depósito de {_dinero(deposito.en_custodia)} se devuelve '
             'al cliente.')
    registrar_pendiente(
        titulo=f'Inspeccionar {folio} antes de devolver el depósito', rol='ENCARGADO', tipo='DEPOSITO',
        objeto=deposito.cotizacion, vence_en=inspeccion.vence_en,
        detalle=f'{cliente}: depósito de {_dinero(deposito.en_custodia)}.', clave_idempotencia=f'inspeccion:{inspeccion.pk}')
    for destino in destinos:
        _enviar_aviso(destino, texto, botones=[(id_boton(inspeccion.pk, 'ok'), 'Sin daños')],
                      clave=f'inspeccion:{inspeccion.pk}:solicitud')
    return inspeccion


# ─────────────────────────── Devolución sin reporte (cron) ───────────────────────────

def devolver_sin_reporte(ahora=None, aplicar=True) -> list:
    """Devuelve los depósitos cuyo plazo venció sin reporte de daños. Con
    `aplicar=False` solo lista cuáles se devolverían."""
    ahora = ahora or timezone.now()
    vencidas = InspeccionDeposito.objects.filter(
        estado='SOLICITADA', vence_en__lte=ahora, fotos__isnull=True, monto_danos__isnull=True
    ).select_related('deposito__cotizacion').order_by('id')
    hechas = []
    for inspeccion in vencidas:
        if aplicar:
            resultado = _liquidar(inspeccion.pk, retencion=Decimal('0'), resolucion='SIN_REPORTE')
            if resultado is None:
                continue
        hechas.append(inspeccion)
    return hechas


def revisar_atoradas(ahora=None) -> int:
    """Avisa a Dirección de lo que se quedó a medias: reportes de daños
    incompletos y liquidaciones que no terminaron."""
    ahora = ahora or timezone.now()
    avisadas = 0
    limite = ahora - timedelta(hours=horas_espera())
    for inspeccion in InspeccionDeposito.objects.filter(estado='DANOS', updated_at__lte=limite).select_related(
            'deposito__cotizacion'):
        registrar_pendiente(
            titulo=f'Reporte de daños incompleto en {_folio(inspeccion.deposito)}', rol='DIRECCION', tipo='DEPOSITO',
            severidad='URGENTE', objeto=inspeccion.deposito.cotizacion,
            detalle=f'Falta el monto o la foto, y el depósito sigue retenido. Inspección: {_url(inspeccion)}',
            clave_idempotencia=f'inspeccion:{inspeccion.pk}:incompleto')
        avisadas += 1
    for inspeccion in InspeccionDeposito.objects.filter(
            estado='PROCESANDO', updated_at__lte=ahora - PROCESANDO_MAX).select_related('deposito__cotizacion'):
        registrar_pendiente(
            titulo=f'Liquidación detenida en {_folio(inspeccion.deposito)}', rol='DIRECCION', tipo='DEPOSITO',
            severidad='URGENTE', objeto=inspeccion.deposito.cotizacion,
            detalle=f'Revisa Openpay y el depósito antes de reintentar. Inspección: {_url(inspeccion)}',
            clave_idempotencia=f'inspeccion:{inspeccion.pk}:detenida')
        avisadas += 1
    return avisadas


# ─────────────────────────────── Lo que contesta el personal ───────────────────────────────

def _abiertas():
    return InspeccionDeposito.objects.filter(estado__in=ESTADOS_ABIERTOS).select_related(
        'deposito__cotizacion__cliente').order_by('-solicitada_en')


def _elegir(folio: Optional[str]):
    """La inspección a la que se refiere un reporte: por folio o la única abierta.
    Devuelve `(inspección | None, aclaración | None)`."""
    abiertas = list(_abiertas())
    if folio:
        coinciden = [i for i in abiertas if i.deposito.cotizacion_id == int(folio)]
        if coinciden:
            return coinciden[0], None
        return None, f'No tengo una inspección abierta para COT-{int(folio):03d}.'
    if not abiertas:
        return None, 'No tengo ninguna inspección de depósito abierta.'
    if len(abiertas) > 1:
        folios = ', '.join(_folio(i.deposito) for i in abiertas)
        return None, f'Tengo varias inspecciones abiertas ({folios}). Escribe «cot» y el número, por ejemplo «cot {abiertas[0].deposito.cotizacion_id}».'
    return abiertas[0], None


def atender(telefono: str, texto: str):
    """Texto o botón de la inspección. Devuelve `(atendido, respuesta)`:
    `atendido` es False si el mensaje no es de este flujo (sigue su camino)."""
    texto = (texto or '').strip()
    rol = rol_de_telefono(telefono)
    boton = RE_BOTON.match(texto)
    if boton:
        return True, _atender_boton(rol, int(boton.group(1)), boton.group(2))
    if rol not in ROLES_QUE_REPORTAN:
        return False, None
    sin = RE_SIN_DANOS.match(texto)
    if sin:
        inspeccion, aclaracion = _elegir(sin.group(1))
        return True, aclaracion or _confirmar_sin_danos(inspeccion)
    danos = RE_DANOS.match(texto)
    if danos:
        return True, _reportar_danos(telefono, danos)
    return False, None


def _atender_boton(rol, inspeccion_id: int, accion: str) -> Optional[str]:
    inspeccion = InspeccionDeposito.objects.select_related('deposito__cotizacion').filter(pk=inspeccion_id).first()
    if inspeccion is None:
        return None
    if accion == 'ok' and rol in ROLES_QUE_REPORTAN:
        return _confirmar_sin_danos(inspeccion)
    if accion in ('apr', 'rech') and rol == 'DIRECCION':
        return _decidir(inspeccion, aprobar=accion == 'apr')
    return None  # botón ajeno: se descarta sin hacer nada


def _confirmar_sin_danos(inspeccion) -> str:
    folio = _folio(inspeccion.deposito)
    resultado = _liquidar(inspeccion.pk, retencion=Decimal('0'), resolucion='SIN_DANOS')
    if resultado is None:
        return f'La inspección de {folio} ya no está esperando respuesta.'
    if resultado.get('manual'):
        return f'Anotado: {folio} sin daños. El depósito no se puede devolver solo; Dirección lo atenderá.'
    return f'Listo, {folio} sin daños. Se devolvió el depósito al cliente.'


def _reportar_danos(telefono: str, coincidencia) -> str:
    try:
        monto = centavos(Decimal(coincidencia.group(1).replace(',', '')))
    except InvalidOperation:
        return 'No entendí el monto. Escríbelo así: «daños 800 cot 145 vidrio roto».'
    desglose = ' '.join((coincidencia.group(3) or '').split())
    inspeccion, aclaracion = _elegir(coincidencia.group(2))
    if aclaracion:
        return aclaracion
    if monto <= 0:
        return 'El monto de los daños debe ser mayor a cero. Si no hubo daños, escribe «sin daños».'
    if len(desglose) < MIN_DESGLOSE:
        return 'Describe los daños para registrarlos, por ejemplo: «daños 800 cot 145 vidrio roto en baño».'
    custodia = inspeccion.deposito.en_custodia
    if monto > custodia:
        return f'El monto excede lo que hay en custodia ({_dinero(custodia)}). Revisa el importe.'
    InspeccionDeposito.objects.filter(pk=inspeccion.pk, estado__in=ESTADOS_ABIERTOS).update(
        estado='DANOS', monto_danos=monto, desglose=desglose[:2000],
        reportado_por=normalizar_telefono_wa(telefono), updated_at=timezone.now())
    inspeccion.refresh_from_db()
    return _evaluar(inspeccion)


def _evaluar(inspeccion) -> str:
    """Con monto y al menos una foto, decide: aplicar (dentro del tope) o pedir a Dirección."""
    folio = _folio(inspeccion.deposito)
    fotos = inspeccion.fotos.count()
    if inspeccion.monto_danos is None:
        return (f'Recibí {fotos} foto(s) de {folio}. Falta el monto: escribe «daños 800 cot '
                f'{inspeccion.deposito.cotizacion_id} descripción».')
    if fotos == 0:
        return f'Anoté {_dinero(inspeccion.monto_danos)} de daños en {folio}. Falta al menos una foto de evidencia.'
    monto = inspeccion.monto_danos
    if monto <= tope_retencion():
        resultado = _liquidar(inspeccion.pk, retencion=monto, resolucion='RETENCION_ENCARGADO',
                              desglose=_desglose(inspeccion), desde=('DANOS',))
        if resultado is None:
            return f'El reporte de {folio} ya no está pendiente.'
        if resultado.get('manual'):
            return f'Reporte de {folio} recibido. El depósito no se puede liquidar solo; Dirección lo atenderá.'
        _avisar_retencion(inspeccion, resultado)
        return f'Listo, retuve {_dinero(resultado["retenido"])} de {folio} y devolví {_dinero(resultado["devuelto"])} al cliente.'
    InspeccionDeposito.objects.filter(pk=inspeccion.pk, estado='DANOS').update(
        estado='POR_APROBAR', updated_at=timezone.now())
    _pedir_aprobacion(inspeccion)
    return f'Listo, mandé el reporte de {folio} ({_dinero(monto)}) a Dirección. En cuanto lo apruebe se aplica.'


def _desglose(inspeccion) -> str:
    return (f'Daños reportados por WhatsApp el {timezone.localdate():%d/%m/%Y}: {inspeccion.desglose} '
            f'Evidencia: {inspeccion.fotos.count()} foto(s) en la inspección #{inspeccion.pk}.')


def _pedir_aprobacion(inspeccion) -> None:
    deposito = inspeccion.deposito
    folio = _folio(deposito)
    registrar_pendiente(
        titulo=f'Aprobar retención de {_dinero(inspeccion.monto_danos)} en {folio}', rol='DIRECCION', tipo='DEPOSITO',
        objeto=deposito.cotizacion, detalle=f'Daños reportados: {inspeccion.desglose[:300]}',
        clave_idempotencia=f'inspeccion:{inspeccion.pk}:aprobar')
    destino = telefono_de('DIRECCION')
    if not destino:
        logger.error("%s; la aprobación de %s queda solo en la bandeja", SIN_DESTINO, folio)
        return
    texto = (f'Retención por daños en {folio}: {_dinero(inspeccion.monto_danos)} de {_dinero(deposito.en_custodia)} '
             f'en custodia. «{inspeccion.desglose[:250]}». {inspeccion.fotos.count()} foto(s): {_url(inspeccion)}')
    _enviar_aviso(destino, texto, botones=[(id_boton(inspeccion.pk, 'apr'), 'Aprobar'),
                                           (id_boton(inspeccion.pk, 'rech'), 'Devolver todo')],
                  clave=f'inspeccion:{inspeccion.pk}:aprobacion')


def _decidir(inspeccion, *, aprobar: bool) -> Optional[str]:
    folio = _folio(inspeccion.deposito)
    if inspeccion.estado != 'POR_APROBAR':
        return None
    resultado = _liquidar(
        inspeccion.pk, retencion=inspeccion.monto_danos if aprobar else Decimal('0'),
        resolucion='RETENCION_APROBADA' if aprobar else 'RETENCION_RECHAZADA',
        desglose=_desglose(inspeccion) if aprobar else '', desde=('POR_APROBAR',))
    if resultado is None:
        return None
    if resultado.get('manual'):
        return f'Anotado. El depósito de {folio} no se puede liquidar solo; quedó en tu bandeja.'
    if aprobar:
        return f'Listo, retuve {_dinero(resultado["retenido"])} de {folio} y devolví {_dinero(resultado["devuelto"])}.'
    return f'Listo, devolví el depósito de {folio} completo ({_dinero(resultado["devuelto"])}).'


def _avisar_retencion(inspeccion, resultado) -> None:
    destino = telefono_de('DIRECCION')
    if destino:
        _enviar_aviso(destino, (f'Retención aplicada en {_folio(inspeccion.deposito)}: {_dinero(resultado["retenido"])} '
                                f'por daños reportados por el encargado (dentro del tope). Detalle: {_url(inspeccion)}'),
                      clave=f'inspeccion:{inspeccion.pk}:retencion')


# ────────────────────────────────── Fotos de evidencia ──────────────────────────────────

def recibir_foto(telefono: str, media_id: str, pie: str = '') -> Optional[str]:
    """Foto o PDF del personal: se guarda como evidencia de la inspección que
    corresponde. Devuelve el texto con que contestarle."""
    if rol_de_telefono(telefono) not in ROLES_QUE_REPORTAN:
        return None
    coincidencia = re.search(r'cot(?:izaci[oó]n)?\s*-?(\d+)', pie or '', re.IGNORECASE)
    inspeccion, aclaracion = _elegir(coincidencia.group(1) if coincidencia else None)
    if inspeccion is None:
        return aclaracion
    try:
        contenido, _, extension = descargar_media_wa(media_id)
    except ValueError as e:
        return str(e)
    foto = FotoInspeccion(inspeccion=inspeccion, media_id=media_id)
    foto.archivo.save(f'{_folio(inspeccion.deposito)}-{inspeccion.pk}-{timezone.now():%Y%m%d%H%M%S}{extension}',
                      ContentFile(contenido), save=False)
    foto.save()
    InspeccionDeposito.objects.filter(pk=inspeccion.pk, estado='SOLICITADA').update(
        estado='DANOS', reportado_por=normalizar_telefono_wa(telefono), updated_at=timezone.now())
    inspeccion.refresh_from_db()
    return _evaluar(inspeccion)

