"""Atribución de cotizaciones al personal de ventas (Issue #505, PR 1B).

Solo lo que ella atiende cuenta: la cotizó ella, o el cliente reservó tras una
visita o una cotización suya. Lo que cierran solos Kooxi o la web no se atribuye.

Reglas mecánicas (con rastro en `vendedor_origen`):
  COTIZO       alta de la cotización en el admin por un usuario con Empleado VENTAS activo.
  VISITA       cotización de web/Kooxi cuyo teléfono tiene una Visita ATENDIDA con vendedor
               dentro de `dias_atribucion`; al cerrar la visita también se atribuyen las
               BORRADOR/COTIZADA sin vendedor de ese teléfono.
  SEGUIMIENTO  cotización de web/Kooxi cuyo cliente tiene otra cotización suya creada
               dentro de `dias_atribucion`.
  MANUAL       Dirección (superusuario), por acción con confirmación.

`atribuir()` usa `update()` (no recotiza ni dispara señales de precio), nunca pisa una
atribución existente y se congela al quedar CONFIRMADA.
"""
import logging
from datetime import timedelta

from django.utils import timezone

from comercial.models import Cotizacion
from nomina.models import CondicionesPago, Empleado
from nomina.services_expediente import condiciones_vigentes

logger = logging.getLogger(__name__)

ESTADOS_CONGELADOS = ('CONFIRMADA', 'EJECUTADA', 'CERRADA')
ORIGENES = dict(Cotizacion.ORIGENES_VENDEDOR)
DIAS_ATRIBUCION_POR_DEFECTO = CondicionesPago._meta.get_field('dias_atribucion').default
VENTANA_MAXIMA_DIAS = 365  # tope de búsqueda; la ventana real sale de las condiciones del vendedor


def ultimos_10(telefono) -> str:
    digitos = ''.join(filter(str.isdigit, str(telefono or '')))
    return digitos[-10:] if len(digitos) >= 10 else ''


def _en_servicio(empleado, hoy=None) -> bool:
    hoy = hoy or timezone.localdate()
    return bool(empleado and empleado.activo and empleado.puesto == 'VENTAS'
                and (empleado.fecha_baja is None or empleado.fecha_baja >= hoy))


def vendedor_de_usuario(usuario):
    """Empleado de Ventas activo (y sin baja vencida) ligado a este usuario, o None."""
    if not getattr(usuario, 'pk', None):
        return None
    empleado = Empleado.objects.filter(usuario=usuario, puesto='VENTAS').first()
    return empleado if _en_servicio(empleado) else None


def usuario_de_telefono(telefono):
    """Usuario del ERP del empleado cuyo teléfono coincide (últimos 10 dígitos), o None."""
    buscado = ultimos_10(telefono)
    if not buscado:
        return None
    for empleado in Empleado.objects.filter(usuario__isnull=False, activo=True):
        if ultimos_10(empleado.telefono) == buscado:
            return empleado.usuario
    return None


def dias_atribucion(vendedor, hoy=None) -> int:
    condiciones = condiciones_vigentes(vendedor, hoy or timezone.localdate())
    return condiciones.dias_atribucion if condiciones else DIAS_ATRIBUCION_POR_DEFECTO


def atribuir(cotizacion, vendedor, origen, usuario=None, forzar=False) -> bool:
    """Atribuye la cotización; devuelve si cambió algo.

    Nunca pisa una atribución existente ni toca una cotización congelada
    (CONFIRMADA o posterior). `forzar=True` solo vale para un superusuario
    (cambio manual de Dirección).
    """
    if origen not in ORIGENES:
        raise ValueError(f'Origen de atribución desconocido: {origen}')
    if forzar and not getattr(usuario, 'is_superuser', False):
        return False
    actual = Cotizacion.objects.filter(pk=cotizacion.pk).values('vendedor_id', 'estado', 'es_historica').first()
    if not actual or actual['es_historica']:
        return False
    if actual['vendedor_id'] and not forzar:
        return False
    if actual['estado'] in ESTADOS_CONGELADOS and not forzar:
        return False
    if actual['vendedor_id'] == vendedor.pk and origen == cotizacion.vendedor_origen:
        return False
    ahora = timezone.now()
    asignador = usuario if getattr(usuario, 'pk', None) else None
    Cotizacion.objects.filter(pk=cotizacion.pk).update(
        vendedor=vendedor, vendedor_origen=origen, vendedor_asignado_en=ahora, vendedor_asignado_por=asignador)
    cotizacion.vendedor, cotizacion.vendedor_origen = vendedor, origen
    cotizacion.vendedor_asignado_en, cotizacion.vendedor_asignado_por = ahora, asignador
    logger.info('Atribución: COT-%s → empleado %s (%s)', cotizacion.pk, vendedor.pk, origen)
    return True


def _visita_atendida_reciente(telefono):
    from comunicacion.models import Visita

    tel = ultimos_10(telefono)
    if not tel:
        return None
    ahora = timezone.now()
    # El teléfono se compara por sus últimos 10 dígitos en Python: cada fuente lo guarda con otro formato.
    candidatas = (Visita.objects.filter(resultado='ATENDIDA', vendedor__isnull=False,
                                        inicio__gte=ahora - timedelta(days=VENTANA_MAXIMA_DIAS))
                  .select_related('vendedor').order_by('-inicio'))
    for visita in candidatas:
        if ultimos_10(visita.telefono) != tel:
            continue
        if _en_servicio(visita.vendedor) and visita.inicio >= ahora - timedelta(
                days=dias_atribucion(visita.vendedor)):
            return visita
    return None


def _cotizacion_suya_reciente(cotizacion):
    ahora = timezone.now()
    previas = (Cotizacion.objects.filter(cliente_id=cotizacion.cliente_id, vendedor__isnull=False,
                                         es_historica=False)
               .exclude(pk=cotizacion.pk).select_related('vendedor').order_by('-created_at'))
    for previa in previas:
        if _en_servicio(previa.vendedor) and previa.created_at >= ahora - timedelta(
                days=dias_atribucion(previa.vendedor)):
            return previa
    return None


def atribuir_automatica(cotizacion) -> bool:
    """Reglas VISITA y SEGUIMIENTO para una cotización creada por la web o por Kooxi."""
    if cotizacion.es_historica or cotizacion.vendedor_id:
        return False
    visita = _visita_atendida_reciente(getattr(cotizacion.cliente, 'telefono', ''))
    if visita is not None:
        return atribuir(cotizacion, visita.vendedor, 'VISITA')
    previa = _cotizacion_suya_reciente(cotizacion)
    if previa is not None:
        return atribuir(cotizacion, previa.vendedor, 'SEGUIMIENTO')
    return False


def atribuir_por_visita(visita) -> int:
    """Al marcar atendida una visita: atribuye las cotizaciones BORRADOR/COTIZADA sin vendedor
    del mismo teléfono. Devuelve cuántas atribuyó."""
    if visita.resultado != 'ATENDIDA' or not visita.vendedor_id or not _en_servicio(visita.vendedor):
        return 0
    tel = ultimos_10(visita.telefono)
    if not tel:
        return 0
    hechas = 0
    pendientes = Cotizacion.objects.filter(
        estado__in=Cotizacion.ESTADOS_SIN_APARTAR, vendedor__isnull=True, es_historica=False,
    ).select_related('cliente')
    for cotizacion in pendientes:
        if ultimos_10(cotizacion.cliente.telefono) == tel and atribuir(cotizacion, visita.vendedor, 'VISITA'):
            hechas += 1
    return hechas


def atribuir_por_creacion(cotizacion) -> bool:
    """Punto de entrada del alta: una cotización sin usuario (web o Kooxi) pasa por las
    reglas VISITA/SEGUIMIENTO. Nunca lanza: un fallo aquí no tumba el guardado."""
    try:
        if cotizacion.usuario_id:
            return False
        return atribuir_automatica(cotizacion)
    except Exception:
        logger.exception('Atribución automática falló para COT-%s', cotizacion.pk)
        return False


def tiene_cotizacion_abierta_con_vendedor(telefono) -> bool:
    """¿El cliente de este teléfono tiene una cotización viva (borrador, cotizada o confirmada) con vendedor?"""
    tel = ultimos_10(telefono)
    if not tel:
        return False
    abiertas = (Cotizacion.objects.filter(estado__in=('BORRADOR', 'COTIZADA', 'CONFIRMADA'), vendedor__isnull=False,
                                          es_historica=False).select_related('cliente'))
    return any(ultimos_10(c.cliente.telefono) == tel for c in abiertas)
