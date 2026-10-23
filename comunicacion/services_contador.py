"""
Kooxi le pregunta al CONTADOR por los CFDI emitidos ambiguos (Issue #431, fase 4).

Un CFDI emitido que el ERP no pudo ligar solo (`CfdiSAT` SIN_VINCULAR: ninguna o
varias solicitudes de factura candidatas) se le consulta al contador en un solo
mensaje agrupado por día. El primero con varias candidatas lleva un botón por
candidata; su respuesta liga el comprobante con `contabilidad.services_sat.vincular`
(la misma función que usa el admin). Cada CFDI se pregunta una vez y, sin
respuesta en 3 días, una más; después se avisa a Dirección. Nada lanza.
"""
import logging
import re
from datetime import timedelta
from typing import List, Optional

from django.urls import reverse
from django.utils import timezone

from contabilidad import services_sat
from contabilidad.models import CfdiSAT

from .models import ConsultaCfdi
from .services import normalizar_telefono_wa
from .services_direccion import dinero
from .services_pendientes import _base_url, _enviar_aviso, registrar_pendiente
from .services_responsables import responsable

logger = logging.getLogger(__name__)

MAX_EN_MENSAJE = 5
MAX_CANDIDATAS_BOTON = 2
DIAS_RECORDATORIO = 3
MAX_CONSULTAS = 2

RE_BOTON = re.compile(r'^cfdi:(\d+):(\d+|no)$')


def id_boton(cfdi_id: int, destino) -> str:
    return f'cfdi:{cfdi_id}:{destino}'


def es_boton(texto: str) -> bool:
    return bool(RE_BOTON.match((texto or '').strip()))


def _contador():
    """El responsable activo de CONTADOR (nunca un número pendiente de «ACEPTO»)."""
    resp = responsable('CONTADOR')
    return resp if resp is not None and normalizar_telefono_wa(resp.telefono_wa) else None


def _es_contador(telefono: str) -> bool:
    resp, tel = _contador(), normalizar_telefono_wa(telefono)
    return bool(resp and tel and normalizar_telefono_wa(resp.telefono_wa) == tel)


def _candidatas(cfdi) -> list:
    if cfdi.fecha is None:
        return []
    return services_sat.candidatas_emitido(cfdi.rfc_receptor, cfdi.total, cfdi.fecha, cfdi.rfc_emisor)


def _etiqueta(solicitud) -> str:
    return f'SOL-{solicitud.pk:04d}'


def _detalle_solicitud(solicitud) -> str:
    cot = f'COT-{solicitud.cotizacion_id:03d}, ' if solicitud.cotizacion_id else ''
    return f'{_etiqueta(solicitud)} ({cot}{dinero(solicitud.monto)}, {solicitud.fecha_solicitud:%d/%m})'


def _url_comprobantes() -> str:
    return _base_url() + reverse('admin:contabilidad_cfdisat_changelist') + '?resultado__exact=SIN_VINCULAR'


def por_preguntar(ahora=None) -> List[tuple]:
    """[(cfdi, candidatas)] de los emitidos ambiguos que toca preguntar: sin consulta
    previa, o con una sin respuesta de más de `DIAS_RECORDATORIO` días y menos de
    `MAX_CONSULTAS`. Con exactamente una candidata no es ambiguo (el ligue
    automático lo resuelve en la siguiente sincronización)."""
    ahora = ahora or timezone.now()
    resultado = []
    for cfdi in CfdiSAT.objects.filter(direccion='EMITIDO', resultado='SIN_VINCULAR').order_by('fecha', 'id'):
        consultas = list(cfdi.consultas.all())
        if any(c.estado in ('LIGADA', 'NINGUNA') for c in consultas):
            continue
        if consultas:
            ultima = max(c.created_at for c in consultas)
            if len(consultas) >= MAX_CONSULTAS or ultima > ahora - timedelta(days=DIAS_RECORDATORIO):
                continue
        candidatas = _candidatas(cfdi)
        if len(candidatas) != 1:
            resultado.append((cfdi, candidatas))
    return resultado


def _avisar_sin_respuesta(ahora) -> None:
    """Dos preguntas sin respuesta: se le avisa a Dirección una sola vez por CFDI."""
    limite = ahora - timedelta(days=DIAS_RECORDATORIO)
    for cfdi in CfdiSAT.objects.filter(direccion='EMITIDO', resultado='SIN_VINCULAR'):
        consultas = list(cfdi.consultas.all())
        if (len(consultas) >= MAX_CONSULTAS and all(c.estado == 'PREGUNTADA' for c in consultas)
                and max(c.created_at for c in consultas) <= limite):
            registrar_pendiente(
                titulo=f'El contador no respondió por el CFDI de {dinero(cfdi.total)} ({cfdi.fecha:%d/%m})',
                detalle=f'Se le preguntó {len(consultas)} veces. Ligarlo en Contabilidad → Comprobantes: {_url_comprobantes()}',
                rol='DIRECCION', tipo='SAT', severidad='NORMAL', clave_idempotencia=f'cfdi:{cfdi.pk}:sin_respuesta')


def _linea(n: int, cfdi, candidatas) -> str:
    cuando = f'{cfdi.fecha:%d/%m}' if cfdi.fecha else 's/f'
    contraparte = (cfdi.nombre_contraparte or cfdi.rfc_receptor)[:40]
    base = f'{n}) {dinero(cfdi.total)} del {cuando} a {contraparte}'
    return base + (f': {len(candidatas)} candidatas' if candidatas else ': sin solicitud que coincida')


def _botones(cfdi, candidatas) -> list:
    botones = [(id_boton(cfdi.pk, s.pk), _etiqueta(s)) for s in candidatas[:MAX_CANDIDATAS_BOTON]]
    return botones + [(id_boton(cfdi.pk, 'no'), 'Ninguna')]


def _pregunta_con_botones(cfdi, candidatas, prefijo: str = '') -> str:
    detalle = '; '.join(_detalle_solicitud(s) for s in candidatas[:MAX_CANDIDATAS_BOTON])
    return (f'{prefijo}{dinero(cfdi.total)} del {cfdi.fecha:%d/%m} a '
            f'{(cfdi.nombre_contraparte or cfdi.rfc_receptor)[:40]}: ¿de cuál solicitud es? {detalle}.').strip()


def preguntar_contador(hoy=None, ahora=None) -> int:
    """Un mensaje agrupado al día al CONTADOR con los CFDI ambiguos. Devuelve 1 si salió."""
    hoy = hoy or timezone.localdate()
    ahora = ahora or timezone.now()
    _avisar_sin_respuesta(ahora)
    ambiguos = por_preguntar(ahora)
    if not ambiguos:
        return 0
    if ConsultaCfdi.objects.filter(created_at__date=hoy).exists():
        return 0  # ya se le escribió hoy
    contador = _contador()
    if contador is None:
        registrar_pendiente(
            titulo=f'Hay {len(ambiguos)} CFDI emitidos sin ligar y no hay contador en el directorio',
            detalle=f'Captura al contador en Comunicación → Responsables, o ligarlos en {_url_comprobantes()}',
            rol='DIRECCION', tipo='SAT', severidad='NORMAL',
            clave_idempotencia=f'cfdi:sin_contador:{hoy.isocalendar()[0]}-{hoy.isocalendar()[1]}')
        return 0
    lote = ambiguos[:MAX_EN_MENSAJE]
    primero = next(((c, cand) for c, cand in lote if len(cand) >= 2), None)
    lineas = ' '.join(_linea(n, c, cand) for n, (c, cand) in enumerate(lote, 1))
    extra = f' y {len(ambiguos) - len(lote)} más' if len(ambiguos) > len(lote) else ''
    texto = (f'Hola, soy Kooxi. Hay {len(ambiguos)} CFDI emitidos que no pude ligar a una solicitud de factura: '
             f'{lineas}{extra}. Los sin candidata se ligan en {_url_comprobantes()}')
    botones = None
    if primero is not None:
        cfdi, candidatas = primero
        texto += ' ' + _pregunta_con_botones(cfdi, candidatas, prefijo='Empiezo con el CFDI de ')
        botones = _botones(cfdi, candidatas)
    comm = _enviar_aviso(contador.telefono_wa, texto, botones=botones)
    if comm is None or comm.estado == 'FALLIDO':
        return 0
    for cfdi, candidatas in lote:
        ConsultaCfdi.objects.create(cfdi=cfdi, candidatas=','.join(str(s.pk) for s in candidatas))
    return 1


# ───────────────────────────── Respuesta del contador ─────────────────────────

def _siguiente_con_candidatas(hoy):
    """La siguiente consulta del día sin responder cuyo CFDI sigue ambiguo (2 o más candidatas)."""
    for consulta in ConsultaCfdi.objects.filter(estado='PREGUNTADA', created_at__date=hoy).select_related('cfdi'):
        if consulta.cfdi.resultado != 'SIN_VINCULAR':
            continue
        candidatas = _candidatas(consulta.cfdi)
        if len(candidatas) >= 2:
            return consulta.cfdi, candidatas
    return None


def _responder(telefono: str, confirmacion: str) -> Optional[str]:
    """Contesta con la confirmación y, si queda otro CFDI ambiguo del día, lo pregunta en el mismo mensaje."""
    siguiente = _siguiente_con_candidatas(timezone.localdate())
    if siguiente is None:
        return confirmacion
    cfdi, candidatas = siguiente
    _enviar_aviso(telefono, _pregunta_con_botones(cfdi, candidatas, prefijo=f'{confirmacion} Siguiente, el CFDI de '),
                  botones=_botones(cfdi, candidatas))
    return None


def atender_boton(telefono: str, texto: str) -> Optional[str]:
    """`cfdi:<id>:<solicitud>` liga el comprobante; `cfdi:<id>:no` lo deja para el admin.
    Solo vale desde el teléfono activo del CONTADOR y para una candidata de la consulta.
    Devuelve el texto con que contestarle, o None si no hizo nada."""
    coincide = RE_BOTON.match((texto or '').strip())
    if not coincide or not _es_contador(telefono):
        return None
    cfdi = CfdiSAT.objects.filter(pk=int(coincide.group(1)), direccion='EMITIDO').first()
    if cfdi is None:
        return None
    consulta = cfdi.consultas.filter(estado='PREGUNTADA').order_by('-created_at', '-id').first()
    if consulta is None:
        return 'Ese comprobante ya no estaba pendiente de tu respuesta.' if cfdi.resultado != 'SIN_VINCULAR' else None
    if cfdi.resultado != 'SIN_VINCULAR':
        return 'Ese comprobante ya estaba ligado.'
    ahora = timezone.now()
    tel = normalizar_telefono_wa(telefono)
    if coincide.group(2) == 'no':
        ConsultaCfdi.objects.filter(pk=consulta.pk, estado='PREGUNTADA').update(
            estado='NINGUNA', telefono=tel, respondida_en=ahora, updated_at=ahora)
        return _responder(telefono, 'Listo, lo dejo para revisarlo en el admin.')
    solicitud_id = int(coincide.group(2))
    if str(solicitud_id) not in consulta.candidatas.split(','):
        return None
    solicitud = next((s for s in _candidatas(cfdi) if s.pk == solicitud_id), None)
    if solicitud is None:
        return 'Esa solicitud ya no está disponible para ligar; revisa el comprobante en el admin.'
    try:
        services_sat.vincular(cfdi, solicitud)
    except Exception:
        logger.exception("No se pudo ligar el CFDI %s a SOL-%s", cfdi.pk, solicitud_id)
        return 'No pude ligarlo; revísalo en el admin.'
    ConsultaCfdi.objects.filter(pk=consulta.pk).update(
        estado='LIGADA', solicitud=solicitud, telefono=tel, respondida_en=ahora, updated_at=ahora)
    return _responder(telefono, f'Listo, ligué el CFDI de {dinero(cfdi.total)} a {_etiqueta(solicitud)}.')
