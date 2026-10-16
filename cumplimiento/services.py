"""
Lógica del módulo de Cumplimiento (Issue #403): vigencias, semáforo y tablero.

`estado_tramite()` es la fuente única del semáforo: la usan el admin, el
tablero, los avisos y el PDF. Todo cálculo de fechas recibe `hoy` para poder
probarse, y por defecto usa `timezone.localdate()` (no `now().date()`).
Trabaja sobre `tramite.vigencias.all()` en Python, así aprovecha un
`prefetch_related` y no dispara consultas por fila en las listas.
"""
from calendar import monthrange
from collections import namedtuple
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from .constantes import CATALOGO_SUGERIDO
from .models import RequisitoTramite, Tramite

SIN_REGISTRO = 'SIN_REGISTRO'
EN_TRAMITE = 'EN_TRAMITE'
VENCIDO = 'VENCIDO'
POR_VENCER = 'POR_VENCER'
VIGENTE = 'VIGENTE'

ESTADOS_ETIQUETA = {
    SIN_REGISTRO: 'Sin registro', EN_TRAMITE: 'En trámite', VENCIDO: 'Vencido',
    POR_VENCER: 'Por vencer', VIGENTE: 'Vigente',
}
# Orden de urgencia en el tablero
ORDEN_ESTADOS = (VENCIDO, POR_VENCER, EN_TRAMITE, SIN_REGISTRO, VIGENTE)

_MESES = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
          'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre')

EstadoTramite = namedtuple('EstadoTramite', 'estado vigencia dias renovacion_en_curso')


def agregar_meses(fecha, meses):
    """Suma meses calendario; si el día no existe en el mes destino, usa el
    último día de ese mes (31-ene + 1 mes = 28/29-feb)."""
    indice = fecha.year * 12 + (fecha.month - 1) + meses
    anio, mes = divmod(indice, 12)
    mes += 1
    return fecha.replace(year=anio, month=mes, day=min(fecha.day, monthrange(anio, mes)[1]))


def _vigencias(tramite, estado):
    return [v for v in tramite.vigencias.all() if v.estado == estado]


def vigencia_actual(tramite):
    """La `OBTENIDA` con `fecha_vencimiento` más lejana (sin vencimiento = la
    más lejana de todas). Las canceladas no cuentan."""
    obtenidas = _vigencias(tramite, 'OBTENIDA')
    if not obtenidas:
        return None
    return max(obtenidas, key=lambda v: (v.fecha_vencimiento is None, v.fecha_vencimiento or timezone.localdate(), v.pk))


def renovacion_en_curso(tramite):
    en_tramite = _vigencias(tramite, 'EN_TRAMITE')
    return max(en_tramite, key=lambda v: v.pk) if en_tramite else None


def estado_tramite(tramite, hoy=None):
    """Semáforo de un trámite. Con renovación en trámite y la actual aún
    vigente se muestra el estado de la actual y `renovacion_en_curso`."""
    hoy = hoy or timezone.localdate()
    actual = vigencia_actual(tramite)
    renovacion = renovacion_en_curso(tramite)

    if actual is None:
        return EstadoTramite(EN_TRAMITE if renovacion else SIN_REGISTRO, None, None, renovacion)
    if actual.fecha_vencimiento is None:
        return EstadoTramite(VIGENTE, actual, None, renovacion)

    dias = (actual.fecha_vencimiento - hoy).days
    if dias < 0:
        estado = EN_TRAMITE if renovacion else VENCIDO
    elif dias <= tramite.dias_aviso:
        estado = POR_VENCER
    else:
        estado = VIGENTE
    return EstadoTramite(estado, actual, dias, renovacion)


def siguiente_vencimiento(vigencia):
    """Vencimiento sugerido de la renovación (None si el trámite no vence)."""
    periodicidad = vigencia.tramite.periodicidad_meses
    if not periodicidad:
        return None
    base = vigencia.fecha_vencimiento or timezone.localdate()
    return agregar_meses(base, periodicidad)


def datos_renovacion(tramite):
    """Valores iniciales (strings ISO, para el GET del admin) del formulario
    de una renovación: fechas sugeridas y el costo anterior como referencia."""
    actual = vigencia_actual(tramite)
    datos = {'tramite': tramite.pk, 'estado': 'EN_TRAMITE'}
    if actual is None:
        return datos
    if actual.fecha_vencimiento:
        datos['fecha_inicio'] = (actual.fecha_vencimiento + timedelta(days=1)).isoformat()
    nuevo = siguiente_vencimiento(actual)
    if nuevo:
        datos['fecha_vencimiento'] = nuevo.isoformat()
    datos['costo'] = str(actual.costo)
    return datos


def avance_requisitos(vigencia):
    """(cargados, obligatorios, faltantes) de la vigencia: un requisito cuenta
    como cargado si tiene al menos un documento activo ligado."""
    cubiertos = {d.requisito_id for d in vigencia.documentos.all() if d.activo and d.requisito_id}
    obligatorios = [r for r in vigencia.tramite.requisitos.all() if r.activo and r.obligatorio]
    faltantes = [r for r in obligatorios if r.pk not in cubiertos]
    return len(obligatorios) - len(faltantes), len(obligatorios), faltantes


def dias_texto(dias):
    if dias is None:
        return 'Sin vencimiento'
    if dias == 0:
        return 'Vence hoy'
    if dias > 0:
        return f'En {dias} día{"" if dias == 1 else "s"}'
    return f'Hace {-dias} día{"" if dias == -1 else "s"}'


def costo_anual(anio):
    """Suma (Decimal) del costo de las vigencias no canceladas cuyo inicio cae
    en el año."""
    from .models import Vigencia

    total = Decimal('0.00')
    for costo in Vigencia.objects.exclude(estado='CANCELADA').filter(
            fecha_inicio__year=anio).values_list('costo', flat=True):
        total += costo
    return total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _tramites_activos():
    return Tramite.objects.filter(activo=True).select_related('responsable').prefetch_related(
        'vigencias__documentos', 'requisitos')


def resumen_tablero(hoy=None):
    """Datos del tablero: filas por estado, KPIs, críticos vencidos y línea
    de tiempo de los próximos 12 meses."""
    hoy = hoy or timezone.localdate()
    filas = []
    for tramite in _tramites_activos():
        est = estado_tramite(tramite, hoy)
        filas.append({
            'tramite': tramite, 'estado': est.estado, 'etiqueta': ESTADOS_ETIQUETA[est.estado],
            'vigencia': est.vigencia, 'dias': est.dias, 'dias_texto': dias_texto(est.dias),
            'renovacion': est.renovacion_en_curso,
        })
    filas.sort(key=lambda f: (ORDEN_ESTADOS.index(f['estado']), not f['tramite'].critico,
                              f['dias'] if f['dias'] is not None else 10**6, f['tramite'].nombre))

    grupos = [{'estado': e, 'etiqueta': ESTADOS_ETIQUETA[e], 'filas': [f for f in filas if f['estado'] == e]}
              for e in ORDEN_ESTADOS]
    conteo = {e: len([f for f in filas if f['estado'] == e]) for e in ORDEN_ESTADOS}

    limite = agregar_meses(hoy, 12)
    meses = {}
    for f in filas:
        v = f['vigencia']
        if v and v.fecha_vencimiento and hoy <= v.fecha_vencimiento <= limite:
            meses.setdefault((v.fecha_vencimiento.year, v.fecha_vencimiento.month), []).append(f)
    linea = [{'titulo': f'{_MESES[m - 1].capitalize()} {a}',
              'filas': sorted(fs, key=lambda x: x['vigencia'].fecha_vencimiento)}
             for (a, m), fs in sorted(meses.items())]

    return {
        'hoy': hoy, 'filas': filas, 'grupos': grupos, 'conteo': conteo,
        'proximos_90': [f for f in filas if f['dias'] is not None and 0 <= f['dias'] <= 90],
        'criticos_vencidos': [f for f in filas if f['estado'] == VENCIDO and f['tramite'].critico],
        'costo_anual': costo_anual(hoy.year), 'anio': hoy.year, 'linea_tiempo': linea,
        'vacio': not filas,
    }


@transaction.atomic
def cargar_catalogo_sugerido(usuario=None):
    """Crea (idempotente, por nombre) los trámites sugeridos con sus
    requisitos. Devuelve cuántos trámites nuevos se crearon."""
    creados = 0
    for datos in CATALOGO_SUGERIDO:
        campos = {k: v for k, v in datos.items() if k not in ('nombre', 'requisitos')}
        tramite, nuevo = Tramite.objects.get_or_create(
            nombre=datos['nombre'], defaults={**campos, 'created_by': usuario, 'updated_by': usuario})
        if not nuevo:
            continue
        creados += 1
        RequisitoTramite.objects.bulk_create(
            RequisitoTramite(tramite=tramite, nombre=nombre, orden=i)
            for i, nombre in enumerate(datos['requisitos'], start=1))
    return creados
