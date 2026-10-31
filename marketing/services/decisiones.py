"""Decisiones de marketing (Issue #468, fase 4).

Reglas diarias que leen lo ya medido (gasto, prospectos, KPIs, agenda, caja) y proponen una acción a
Dirección. Cada recomendación es un `comunicacion.Hallazgo` (lo que ve el resumen del lunes) más una
`marketing.Decision` con la acción en JSON para que la fase 5 la ejecute. **Aquí no se ejecuta nada**:
aprobar solo deja constancia.

Reglas comunes:

* Una decisión abierta por regla y objeto (restricción única). Además, la misma regla sobre el mismo
  objeto no se vuelve a proponer durante `EXPIRA_DIAS`, se haya decidido o no: así un rechazo no se
  convierte en una insistencia diaria.
* Caduca a los `EXPIRA_DIAS` días: la decisión queda EXPIRADA y su hallazgo ignorado.
* Con muestra insuficiente la regla no propone nada; el motivo queda en `Resultado.insuficientes` y en el log.
* Los importes son `Decimal` sin IVA; en el JSON viajan como texto.
* El tope de ±20 % por acción (`AJUSTE_PCT`) se respeta ya desde la propuesta.

La regla del prospecto sin respuesta no es una decisión sino un `Pendiente` URGENTE al encargado
(`revisar_sin_respuesta`, cada 5 minutos).
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterator, List, Optional

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from comunicacion.models import Hallazgo, MensajeWhatsApp
from comunicacion.services_direccion import dinero
from comunicacion.services_pendientes import _base_url, registrar_pendiente
from indicadores.services.base import D0, ETIQUETAS, q2, venta_neta

from ..models import Anuncio, Campana, Decision, GastoPublicitario, Lead, ParametroMarketing, SnapshotKPI
from . import kpis

logger = logging.getLogger(__name__)

EXPIRA_DIAS = 14
POSPONER_DIAS = 7
AJUSTE_PCT = Decimal('20')
HORIZONTE_OCUPACION = 30
HORIZONTE_ESCALAR = 60
HORIZONTE_FINES = 45
SEMANAS_ESCALAR = 3
VENTANA_ANOMALIA = 7
MIN_DIAS_ANOMALIA = 4
VENTANA_FRECUENCIA = 3
MIN_IMPRESIONES_FRECUENCIA = 1000
HORA_INICIO, HORA_FIN = 8, 20
MAX_ESPERA_HORAS = 24

TIPO_HALLAZGO = {
    'PAUSAR_ANUNCIO': 'MKT_PAUSAR', 'REASIGNAR': 'MKT_REASIGNAR', 'ESCALAR': 'MKT_ESCALAR',
    'OCUPACION_ALTA': 'MKT_OCUPACION', 'FINES_LIBRES': 'MKT_OCUPACION', 'CAJA': 'MKT_CAJA',
    'CONCENTRACION': 'MKT_CONCENTRA', 'GASTO_ANOMALO': 'MKT_ANOMALO', 'FRECUENCIA': 'MKT_ANOMALO',
    'NO_CONCILIADO': 'MKT_CONCILIA',
}


@dataclass
class Propuesta:
    regla: str
    objeto: str
    tipo_accion: str
    titulo: str
    lineas: List[str]
    accion: dict
    evidencia: dict = field(default_factory=dict)


@dataclass
class Contexto:
    hoy: date
    ahora: datetime
    p: ParametroMarketing
    insuficientes: List[str] = field(default_factory=list)

    def sin_datos(self, regla: str, motivo: str):
        self.insuficientes.append(f'{regla}: {motivo}')
        logger.info('Marketing: datos insuficientes (%s): %s', regla, motivo)


@dataclass
class Resultado:
    creadas: List[Decision] = field(default_factory=list)
    insuficientes: List[str] = field(default_factory=list)
    expiradas: int = 0
    reabiertas: int = 0


def _s(valor) -> str:
    return str(q2(valor))


def _periodo(desde: date, hasta: date) -> str:
    return f'{desde:%d/%m} al {hasta:%d/%m}'


# ───────────────────────────── Agenda ─────────────────────────────────────────

def agenda(inicio: date, fin: date) -> dict:
    """Días [inicio, fin] ocupados por una reservación CONFIRMADA o cerrados con un bloqueo, y la ocupación
    sobre los días vendibles (sin los bloqueados). Reutiliza `comercial.disponibilidad`."""
    from comercial.disponibilidad import bloqueos_activos, cotizaciones_apartadas

    fin_ex = fin + timedelta(days=1)

    def dias(rango):
        i, f = rango
        return {i + timedelta(days=n) for n in range((f - i).days) if inicio <= i + timedelta(days=n) < fin_ex}

    ocupados, bloqueados = set(), set()
    for cot in cotizaciones_apartadas(inicio, fin_ex):
        ocupados |= dias(cot.rango_ocupado())
    for bloqueo in bloqueos_activos(inicio, fin_ex):
        bloqueados |= dias(bloqueo.rango_ocupado())
    bloqueados -= ocupados
    todos = [inicio + timedelta(days=n) for n in range((fin_ex - inicio).days)]
    vendibles = len(todos) - len(bloqueados)
    pct = q2(Decimal(len(ocupados)) * 100 / vendibles) if vendibles > 0 else D0
    return {'dias': todos, 'ocupados': ocupados, 'bloqueados': bloqueados, 'ocupacion': pct,
            'libres': [d for d in todos if d not in ocupados and d not in bloqueados]}


def _campanas_activas():
    return list(Campana.objects.filter(estado__iexact='ACTIVE', presupuesto_diario__gt=0).order_by('id'))


def _ajuste_abierto(campana) -> bool:
    return Decision.objects.filter(estado__in=Decision.ABIERTAS, tipo_accion=Decision.AJUSTAR,
                                   objeto_clave=f'campana:{campana.pk}').exists()


def _ajuste(campana, pct: Decimal) -> dict:
    actual = Decimal(campana.presupuesto_diario)
    nuevo = (actual * (100 + pct) / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return {'tipo': Decision.AJUSTAR, 'campana_id': campana.pk, 'campana': campana.nombre,
            'presupuesto_actual': _s(actual), 'presupuesto_nuevo': _s(nuevo), 'cambio_pct': str(pct)}


# ───────────────────────────── Reglas ─────────────────────────────────────────

def _pausar_anuncios(ctx: Contexto) -> Iterator[Propuesta]:
    """Gasto >= umbral y ninguna cotización de sus prospectos en los últimos N días."""
    p, hoy = ctx.p, ctx.hoy
    desde = hoy - timedelta(days=p.pausa_dias - 1)
    gastos = (GastoPublicitario.objects.filter(fecha__range=(desde, hoy), anuncio__isnull=False)
              .values('anuncio_id').annotate(t=Sum('monto')).filter(t__gte=p.pausa_gasto_minimo))
    anuncios = {a.pk: a for a in Anuncio.objects.filter(pk__in=[g['anuncio_id'] for g in gastos])
                .select_related('campana')}
    if not anuncios:
        return
    atribuyendo = Lead.objects.filter(
        fecha_primer_contacto__date__gte=hoy - timedelta(days=p.ventana_dias - 1)).exclude(anuncio_id='').exists()
    if not atribuyendo:
        ctx.sin_datos('PAUSAR_ANUNCIO', f'ningún prospecto se atribuyó a un anuncio en {p.ventana_dias} días')
        return
    for g in sorted(gastos, key=lambda x: x['anuncio_id']):
        anuncio = anuncios[g['anuncio_id']]
        if anuncio.estado.upper() != 'ACTIVE' or anuncio.campana.estado.upper() != 'ACTIVE':
            continue
        cotizaciones = Lead.objects.filter(anuncio_id=anuncio.id_externo, cotizacion__isnull=False,
                                           cotizacion__created_at__date__gte=desde).count()
        if cotizaciones:
            continue
        nombre = anuncio.nombre or anuncio.id_externo
        yield Propuesta(
            'PAUSAR_ANUNCIO', f'anuncio:{anuncio.pk}', Decision.PAUSAR,
            f'Pausar el anuncio «{nombre}»',
            [f'Periodo: {_periodo(desde, hoy)} ({p.pausa_dias} días).',
             f'Gasto sin IVA: {dinero(g["t"])}; cotizaciones de sus prospectos: 0.',
             f'Campaña: {anuncio.campana.nombre}.',
             'Acción propuesta: pausar el anuncio. No se ejecuta hasta que Dirección lo apruebe.'],
            {'tipo': Decision.PAUSAR, 'nivel': 'anuncio', 'anuncio_id': anuncio.pk,
             'anuncio_externo': anuncio.id_externo, 'campana_id': anuncio.campana_id},
            {'desde': str(desde), 'hasta': str(hoy), 'gasto': _s(g['t']), 'cotizaciones': 0})


def _reasignar(ctx: Contexto) -> Iterator[Propuesta]:
    """El CAC de una campaña supera en >= N % al de otra, con muestra mínima en ambas."""
    p, hoy = ctx.p, ctx.hoy
    desde = hoy - timedelta(days=p.ventana_dias - 1)
    datos = kpis.calcular(desde, hoy, parametros=p)
    grupos = [g for g in datos['por_campana'] if g['gasto'] > 0]
    activas = {c.pk: c for c in _campanas_activas()}
    comparables = [g for g in grupos if g['suficiente'] and g['cac'] is not None and g['campana_id'] in activas]
    if len(comparables) < 2:
        if len(grupos) >= 2:
            ctx.sin_datos('REASIGNAR', f'menos de 2 campañas con {p.muestra_minima_leads} prospectos y ventas ganadas')
        return
    barata = min(comparables, key=lambda g: (g['cac'], g['campana_id']))
    cara = max(comparables, key=lambda g: (g['cac'], -g['campana_id']))
    if cara['campana_id'] == barata['campana_id'] or cara['cac'] < barata['cac'] * (100 + p.reasignar_diferencia_pct) / 100:
        return
    c_cara, c_barata = activas[cara['campana_id']], activas[barata['campana_id']]
    monto = (min(Decimal(c_cara.presupuesto_diario), Decimal(c_barata.presupuesto_diario)) * AJUSTE_PCT / 100
             ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    yield Propuesta(
        'REASIGNAR', f'campanas:{c_cara.pk}>{c_barata.pk}', Decision.REASIGNAR,
        f'Mover presupuesto de «{c_cara.nombre}» a «{c_barata.nombre}»',
        [f'Periodo: {_periodo(desde, hoy)} ({p.ventana_dias} días).',
         f'CAC de «{c_cara.nombre}»: {dinero(cara["cac"])} ({cara["ganados"]} ventas, {cara["prospectos"]} prospectos).',
         f'CAC de «{c_barata.nombre}»: {dinero(barata["cac"])} ({barata["ganados"]} ventas, '
         f'{barata["prospectos"]} prospectos).',
         f'Acción propuesta: mover {dinero(monto)} diarios de la primera a la segunda. No se ejecuta sin aprobación.'],
        {'tipo': Decision.REASIGNAR, 'desde_campana_id': c_cara.pk, 'hacia_campana_id': c_barata.pk,
         'monto_diario': _s(monto),
         'desde_nuevo': _s(Decimal(c_cara.presupuesto_diario) - monto),
         'hacia_nuevo': _s(Decimal(c_barata.presupuesto_diario) + monto)},
        {'desde': str(desde), 'hasta': str(hoy), 'cac_alto': _s(cara['cac']), 'cac_bajo': _s(barata['cac'])})


def _snapshot_cercano(campana, objetivo: date) -> Optional[SnapshotKPI]:
    return SnapshotKPI.objects.filter(campana=campana, fecha__lte=objetivo,
                                      fecha__gte=objetivo - timedelta(days=2)).order_by('-fecha').first()


def _escalar(ctx: Contexto) -> Iterator[Propuesta]:
    """CAC bajo el tope tres semanas seguidas (SnapshotKPI) y agenda con espacio: +20 %."""
    p, hoy = ctx.p, ctx.hoy
    campanas = _campanas_activas()
    if not campanas:
        return
    ocup = agenda(hoy + timedelta(days=1), hoy + timedelta(days=HORIZONTE_ESCALAR))
    if ocup['ocupacion'] >= p.escalar_ocupacion_max:
        return
    for campana in campanas:
        if _ajuste_abierto(campana):
            continue
        snaps = [_snapshot_cercano(campana, hoy - timedelta(days=7 * n)) for n in range(SEMANAS_ESCALAR)]
        if any(s is None for s in snaps):
            if GastoPublicitario.objects.filter(campana=campana, fecha__gte=hoy - timedelta(days=14)).exists():
                ctx.sin_datos('ESCALAR', f'«{campana.nombre}» no tiene {SEMANAS_ESCALAR} semanas de indicadores')
            continue
        if any(s.cac is None or s.cac_tope is None or s.prospectos < p.muestra_minima_leads for s in snaps):
            ctx.sin_datos('ESCALAR', f'«{campana.nombre}» sin CAC, tope o muestra mínima en alguna semana')
            continue
        if not all(s.cac < s.cac_tope for s in snaps):
            continue
        ultimo = snaps[0]
        yield Propuesta(
            'ESCALAR', f'campana:{campana.pk}', Decision.AJUSTAR,
            f'Subir {AJUSTE_PCT} % el presupuesto de «{campana.nombre}»',
            [f'CAC bajo el tope {SEMANAS_ESCALAR} semanas seguidas (al {snaps[-1].fecha:%d/%m}, '
             f'{snaps[1].fecha:%d/%m} y {ultimo.fecha:%d/%m}).',
             f'CAC más reciente: {dinero(ultimo.cac)} contra tope {dinero(ultimo.cac_tope)}.',
             f'Ocupación de los próximos {HORIZONTE_ESCALAR} días: {ocup["ocupacion"]} % (límite {p.escalar_ocupacion_max} %).',
             'Acción propuesta: subir el presupuesto diario. No se ejecuta sin aprobación.'],
            _ajuste(campana, AJUSTE_PCT),
            {'cac': [_s(s.cac) for s in snaps], 'tope': [_s(s.cac_tope) for s in snaps],
             'fechas': [str(s.fecha) for s in snaps], 'ocupacion': str(ocup['ocupacion'])})


def _ocupacion_alta(ctx: Contexto) -> Iterator[Propuesta]:
    """Agenda de los próximos 30 días llena: bajar la pauta de cada campaña activa."""
    p, hoy = ctx.p, ctx.hoy
    inicio, fin = hoy + timedelta(days=1), hoy + timedelta(days=HORIZONTE_OCUPACION)
    ocup = agenda(inicio, fin)
    if ocup['ocupacion'] < p.umbral_ocupacion_pausa:
        return
    for campana in _campanas_activas():
        if _ajuste_abierto(campana):
            continue
        yield Propuesta(
            'OCUPACION_ALTA', f'campana:{campana.pk}', Decision.AJUSTAR,
            f'Bajar {AJUSTE_PCT} % el presupuesto de «{campana.nombre}»: agenda llena',
            [f'Ocupación del {inicio:%d/%m} al {fin:%d/%m}: {ocup["ocupacion"]} % '
             f'({len(ocup["ocupados"])} días ocupados, {len(ocup["bloqueados"])} bloqueados).',
             f'Umbral para pausar: {p.umbral_ocupacion_pausa} %.',
             'Acción propuesta: bajar el presupuesto diario. No se ejecuta sin aprobación.'],
            _ajuste(campana, -AJUSTE_PCT),
            {'desde': str(inicio), 'hasta': str(fin), 'ocupacion': str(ocup['ocupacion'])})


def _fines_libres(ctx: Contexto) -> Iterator[Propuesta]:
    """Fines de semana libres en los próximos 45 días (y agenda no llena): subir la pauta de eventos."""
    p, hoy = ctx.p, ctx.hoy
    inicio, fin = hoy + timedelta(days=1), hoy + timedelta(days=HORIZONTE_FINES)
    ocup = agenda(inicio, fin)
    libres = [d for d in ocup['libres'] if d.weekday() >= 5]
    if len(libres) < p.fines_libres_min:
        return
    if agenda(inicio, hoy + timedelta(days=HORIZONTE_OCUPACION))['ocupacion'] >= p.umbral_ocupacion_pausa:
        return
    for campana in _campanas_activas():
        if campana.tipo_servicio not in ('EVENTO', 'PASADIA') or _ajuste_abierto(campana):
            continue
        yield Propuesta(
            'FINES_LIBRES', f'campana:{campana.pk}', Decision.AJUSTAR,
            f'Subir {AJUSTE_PCT} % el presupuesto de «{campana.nombre}»: fines de semana libres',
            [f'Sábados y domingos libres del {inicio:%d/%m} al {fin:%d/%m}: {len(libres)} '
             f'(mínimo {p.fines_libres_min}).',
             f'Primeros días libres: {", ".join(f"{d:%d/%m}" for d in libres[:6])}.',
             'Acción propuesta: subir el presupuesto diario. No se ejecuta sin aprobación.'],
            _ajuste(campana, AJUSTE_PCT),
            {'desde': str(inicio), 'hasta': str(fin), 'dias_libres': [str(d) for d in libres]})


def cobros_esperados(hoy: date, p: ParametroMarketing) -> dict:
    """Cobros esperados en 30 días, sin IVA: pronóstico de anticipos de prospectos abiertos más el saldo de
    las confirmadas cuyo plazo de pago total (`DIAS_PAGO_TOTAL`) vence en ese lapso o ya venció."""
    from comercial.models import Cotizacion

    anticipos = kpis.pronostico_anticipos(p)['total']['esperado']
    saldos = D0
    confirmadas = Cotizacion.objects.filter(estado='CONFIRMADA', es_historica=False, fecha_evento__isnull=False)
    for cot in confirmadas.prefetch_related('pagos'):
        limite = cot.fecha_evento - timedelta(days=Cotizacion.DIAS_PAGO_TOTAL.get(cot.tipo_servicio, 15))
        saldo = cot.saldo_pendiente()
        if limite <= hoy + timedelta(days=30) and saldo > 0 and cot.precio_final > 0:
            saldos += saldo * venta_neta(cot) / cot.precio_final
    return {'anticipos': q2(anticipos), 'saldos': q2(saldos), 'total': q2(anticipos + saldos)}


def _caja(ctx: Contexto) -> Iterator[Propuesta]:
    """Pauta que queda por gastar este mes + costos fijos > cobros esperados a 30 días."""
    from calendar import monthrange

    from indicadores.services.costos_fijos import CostosFijosService

    p, hoy = ctx.p, ctx.hoy
    campanas = _campanas_activas()
    if not campanas:
        return
    restantes = monthrange(hoy.year, hoy.month)[1] - hoy.day + 1
    comprometida = q2(sum((Decimal(c.presupuesto_diario) for c in campanas), D0) * restantes)
    costos = CostosFijosService.generar(hoy)
    if costos['fuente'] == 'sin_datos' or costos['cf'] <= 0:
        ctx.sin_datos('CAJA', 'sin costos fijos (ni gastos contabilizados ni presupuesto en Indicadores)')
        return
    cobros = cobros_esperados(hoy, p)
    egresos = q2(comprometida + costos['cf'])
    if egresos <= cobros['total']:
        return
    yield Propuesta(
        'CAJA', f'mes:{hoy:%Y-%m}', Decision.INFORMATIVA,
        f'La pauta y los costos fijos de {hoy:%m/%Y} superan los cobros esperados',
        [f'Pauta comprometida hasta el fin de mes: {dinero(comprometida)} '
         f'({restantes} días × {dinero(sum((Decimal(c.presupuesto_diario) for c in campanas), D0))} diarios).',
         f'Costos fijos mensuales ({costos["fuente"]}): {dinero(costos["cf"])}.',
         f'Cobros esperados del {hoy:%d/%m} al {hoy + timedelta(days=30):%d/%m}: {dinero(cobros["total"])} '
         f'(anticipos pronosticados {dinero(cobros["anticipos"])} y saldos por vencer {dinero(cobros["saldos"])}).',
         f'Faltan {dinero(egresos - cobros["total"])}. Todo sin IVA. Revisa si conviene bajar la pauta.'],
        {'tipo': Decision.INFORMATIVA, 'nota': 'Pauta y costos fijos mayores que los cobros esperados a 30 días.'},
        {'pauta_comprometida': _s(comprometida), 'costos_fijos': _s(costos['cf']), 'cobros': _s(cobros['total']),
         'anticipos': _s(cobros['anticipos']), 'saldos': _s(cobros['saldos']), 'dias_restantes': restantes})


def _concentracion(ctx: Contexto) -> Iterator[Propuesta]:
    """Más de N % de los prospectos del periodo vienen de un solo canal."""
    p, hoy = ctx.p, ctx.hoy
    desde = hoy - timedelta(days=p.ventana_dias - 1)
    leads = Lead.objects.filter(fecha_primer_contacto__date__range=(desde, hoy))
    total = leads.count()
    if total < p.muestra_minima_leads:
        if total:
            ctx.sin_datos('CONCENTRACION', f'{total} de {p.muestra_minima_leads} prospectos')
        return
    por_fuente = {}
    for fuente in leads.values_list('fuente', flat=True):
        por_fuente[fuente] = por_fuente.get(fuente, 0) + 1
    fuente, cuantos = max(por_fuente.items(), key=lambda kv: (kv[1], kv[0]))
    pct = q2(Decimal(cuantos) * 100 / total)
    if pct <= p.concentracion_max_pct:
        return
    etiqueta = dict(Lead.FUENTE_CHOICES).get(fuente, fuente)
    yield Propuesta(
        'CONCENTRACION', f'fuente:{fuente}', Decision.INFORMATIVA,
        f'{pct} % de los prospectos vienen de un solo canal: {etiqueta}',
        [f'Periodo: {_periodo(desde, hoy)} ({p.ventana_dias} días).',
         f'{cuantos} de {total} prospectos llegaron por {etiqueta} (límite {p.concentracion_max_pct} %).',
         'Depender de un canal expone al negocio si ese canal falla. Considera probar otro.'],
        {'tipo': Decision.INFORMATIVA, 'nota': 'Diversificar canales de captación.'},
        {'desde': str(desde), 'hasta': str(hoy), 'fuente': fuente, 'prospectos': cuantos, 'total': total,
         'porcentaje': str(pct)})


def _gasto_anomalo(ctx: Contexto) -> Iterator[Propuesta]:
    """El gasto de ayer de una campaña supera N veces el promedio de los 7 días previos."""
    p, hoy = ctx.p, ctx.hoy
    ayer = hoy - timedelta(days=1)
    previo = ayer - timedelta(days=VENTANA_ANOMALIA)
    filas = (GastoPublicitario.objects.filter(fecha__range=(previo, ayer))
             .values('campana_id', 'fecha').annotate(t=Sum('monto')))
    por_campana = {}
    for f in filas:
        por_campana.setdefault(f['campana_id'], {})[f['fecha']] = Decimal(f['t'])
    nombres = dict(Campana.objects.filter(pk__in=por_campana).values_list('pk', 'nombre'))
    for campana_id in sorted(por_campana):
        dias = por_campana[campana_id]
        monto = dias.get(ayer, D0)
        anteriores = [v for d, v in dias.items() if d < ayer]
        if monto <= 0:
            continue
        if len(anteriores) < MIN_DIAS_ANOMALIA:
            ctx.sin_datos('GASTO_ANOMALO', f'«{nombres[campana_id]}» con {len(anteriores)} de {MIN_DIAS_ANOMALIA} días previos')
            continue
        promedio = q2(sum(anteriores, D0) / len(anteriores))
        if promedio <= 0 or monto <= promedio * p.anomalia_factor:
            continue
        yield Propuesta(
            'GASTO_ANOMALO', f'campana:{campana_id}', Decision.INFORMATIVA,
            f'Gasto anómalo en «{nombres[campana_id]}»',
            [f'Gasto del {ayer:%d/%m}: {dinero(monto)} sin IVA.',
             f'Promedio de los {len(anteriores)} días previos ({_periodo(previo, ayer - timedelta(days=1))}): '
             f'{dinero(promedio)}.',
             f'Supera {p.anomalia_factor}× el promedio. Revisa presupuesto, puja y si el anuncio se disparó.'],
            {'tipo': Decision.INFORMATIVA, 'nota': 'Gasto diario fuera de lo normal.'},
            {'fecha': str(ayer), 'gasto': _s(monto), 'promedio': _s(promedio), 'dias': len(anteriores)})


def _frecuencia(ctx: Contexto) -> Iterator[Propuesta]:
    """Frecuencia promedio de los últimos días por encima del máximo."""
    p, hoy = ctx.p, ctx.hoy
    desde = hoy - timedelta(days=VENTANA_FRECUENCIA - 1)
    por_campana = {}
    for f in GastoPublicitario.objects.filter(fecha__range=(desde, hoy), frecuencia__isnull=False
                                              ).select_related('campana'):
        fila = por_campana.setdefault(f.campana_id, {'campana': f.campana, 'valores': [], 'impresiones': 0})
        fila['valores'].append(Decimal(f.frecuencia))
        fila['impresiones'] += f.impresiones
    for campana_id in sorted(por_campana):
        fila = por_campana[campana_id]
        promedio = q2(sum(fila['valores'], D0) / len(fila['valores']))
        if promedio <= p.frecuencia_max:
            continue
        if fila['impresiones'] < MIN_IMPRESIONES_FRECUENCIA:
            ctx.sin_datos('FRECUENCIA', f'«{fila["campana"].nombre}» con {fila["impresiones"]} impresiones')
            continue
        yield Propuesta(
            'FRECUENCIA', f'campana:{campana_id}', Decision.INFORMATIVA,
            f'Frecuencia alta en «{fila["campana"].nombre}»',
            [f'Periodo: {_periodo(desde, hoy)}.',
             f'Frecuencia promedio: {promedio} (máximo {p.frecuencia_max}), con {fila["impresiones"]} impresiones.',
             'La audiencia ya vio el anuncio demasiadas veces: cambia el creativo o amplía la audiencia.'],
            {'tipo': Decision.INFORMATIVA, 'nota': 'Audiencia saturada.'},
            {'desde': str(desde), 'hasta': str(hoy), 'frecuencia': str(promedio),
             'impresiones': fila['impresiones']})


def _no_conciliado(ctx: Contexto) -> Iterator[Propuesta]:
    """Meses cerrados cuyo gasto de Meta no cuadra con los cargos del banco (estado de la fase 2)."""
    hoy = ctx.hoy
    primero_actual = hoy.replace(day=1)
    limite = (primero_actual - timedelta(days=1)).replace(day=1) - timedelta(days=1)
    limite = limite.replace(day=1)
    meses = {}
    for f in GastoPublicitario.objects.filter(estado_conciliacion=GastoPublicitario.DIFERENCIA,
                                              fecha__gte=limite, fecha__lt=primero_actual):
        meses[(f.fecha.year, f.fecha.month)] = meses.get((f.fecha.year, f.fecha.month), D0) + Decimal(f.monto)
    for (anio, mes), total in sorted(meses.items()):
        yield Propuesta(
            'NO_CONCILIADO', f'mes:{anio}-{mes:02d}', Decision.INFORMATIVA,
            f'El gasto de Meta de {mes:02d}/{anio} no cuadra con el banco',
            [f'Gasto de Meta del mes: {dinero(total)} sin IVA.',
             'Los cargos clasificados como publicidad en el estado de cuenta no coinciden dentro de la tolerancia.',
             'Clasifica los cargos de Meta pendientes o revisa el gasto en Pauta.'],
            {'tipo': Decision.INFORMATIVA, 'nota': 'Conciliar el gasto de Meta con el banco.'},
            {'mes': f'{anio}-{mes:02d}', 'gasto': _s(total)})


REGLAS = (_pausar_anuncios, _reasignar, _escalar, _ocupacion_alta, _fines_libres, _caja, _concentracion,
          _gasto_anomalo, _frecuencia, _no_conciliado)


# ───────────────────────────── Ciclo de vida ──────────────────────────────────

def _cerrar_hallazgo(hallazgo, estado: str, usuario=None, ahora=None):
    if hallazgo is None:
        return
    ahora = ahora or timezone.now()
    Hallazgo.objects.filter(pk=hallazgo.pk, estado='PROPUESTO').update(
        estado=estado, resuelto_en=ahora, resuelto_por=usuario if getattr(usuario, 'pk', None) else None,
        updated_at=ahora)


def expirar(ahora: Optional[datetime] = None) -> dict:
    """Pasa a EXPIRADA lo abierto que ya caducó (y cierra su hallazgo) y reabre lo pospuesto cuyo plazo venció."""
    ahora = ahora or timezone.now()
    expiradas = 0
    for decision in Decision.objects.filter(estado__in=Decision.ABIERTAS, expira_en__lte=ahora).select_related('hallazgo'):
        with transaction.atomic():
            filas = Decision.objects.filter(pk=decision.pk, estado__in=Decision.ABIERTAS).update(
                estado=Decision.EXPIRADA, updated_at=ahora)
            if filas:
                _cerrar_hallazgo(decision.hallazgo, 'IGNORADO', ahora=ahora)
                expiradas += 1
    reabiertas = Decision.objects.filter(estado=Decision.POSPUESTA, pospuesta_hasta__lte=ahora,
                                         expira_en__gt=ahora).update(estado=Decision.PENDIENTE, updated_at=ahora)
    return {'expiradas': expiradas, 'reabiertas': reabiertas}


def _crear(prop: Propuesta, ctx: Contexto) -> Optional[Decision]:
    """Hallazgo + Decisión, idempotente: nada si ya hay una abierta o una reciente de la misma regla y objeto."""
    try:
        with transaction.atomic():
            if Decision.objects.filter(regla=prop.regla, objeto_clave=prop.objeto,
                                       created_at__gte=ctx.ahora - timedelta(days=EXPIRA_DIAS)).exists():
                return None
            hallazgo, _ = Hallazgo.objects.get_or_create(
                clave=f'mkt:{prop.regla}:{prop.objeto}:{ctx.hoy:%Y%m%d}'[:191],
                defaults={'tipo': TIPO_HALLAZGO[prop.regla], 'titulo': prop.titulo[:200],
                          'detalle': '\n'.join(prop.lineas)})
            return Decision.objects.create(
                regla=prop.regla, objeto_clave=prop.objeto, tipo_accion=prop.tipo_accion, hallazgo=hallazgo,
                titulo=prop.titulo[:200], evidencia=prop.evidencia, accion_propuesta=prop.accion,
                expira_en=ctx.ahora + timedelta(days=EXPIRA_DIAS))
    except IntegrityError:
        logger.info('Marketing: decisión %s/%s ya existía (carrera)', prop.regla, prop.objeto)
        return None


def generar(hoy: Optional[date] = None, ahora: Optional[datetime] = None,
            parametros: Optional[ParametroMarketing] = None, reglas=REGLAS) -> Resultado:
    """Corre las reglas y deja las decisiones nuevas. Cada regla falla por su cuenta."""
    ahora = ahora or timezone.now()
    hoy = hoy or timezone.localtime(ahora).date()
    ctx = Contexto(hoy=hoy, ahora=ahora, p=parametros or ParametroMarketing.vigente())
    resultado = Resultado()
    ciclo = expirar(ahora)
    resultado.expiradas, resultado.reabiertas = ciclo['expiradas'], ciclo['reabiertas']
    for regla in reglas:
        try:
            for prop in regla(ctx):
                decision = _crear(prop, ctx)
                if decision is not None:
                    resultado.creadas.append(decision)
        except Exception:
            logger.exception('Marketing: falló la regla %s', getattr(regla, '__name__', regla))
    resultado.insuficientes = ctx.insuficientes
    return resultado


def resolver(decision_id: int, accion: str, usuario) -> tuple:
    """Aprobar, rechazar o posponer. Devuelve (hecho, mensaje). Aprobar solo deja constancia: la ejecución
    es un paso aparte (`services/acciones.py`). Solo un superusuario; una decisión atendida o caducada no se vuelve a mover."""
    if not (getattr(usuario, 'is_active', False) and getattr(usuario, 'is_superuser', False)):
        return False, 'Solo Dirección puede decidir.'
    if accion not in ('aprobar', 'rechazar', 'posponer'):
        return False, 'Acción no válida.'
    ahora = timezone.now()
    with transaction.atomic():
        decision = Decision.objects.select_for_update().select_related('hallazgo').filter(pk=decision_id).first()
        if decision is None:
            return False, 'No existe esa decisión.'
        if not decision.abierta:
            return False, f'Esa decisión ya estaba {decision.get_estado_display().lower()}.'
        if decision.expira_en <= ahora:
            expirar(ahora)
            return False, 'Esa decisión ya caducó.'
        decision.updated_by = usuario
        if accion == 'posponer':
            decision.estado = Decision.POSPUESTA
            decision.pospuesta_hasta = min(ahora + timedelta(days=POSPONER_DIAS), decision.expira_en)
            decision.save(update_fields=['estado', 'pospuesta_hasta', 'updated_by', 'updated_at'])
            return True, f'Pospuesta hasta el {timezone.localtime(decision.pospuesta_hasta):%d/%m}.'
        decision.estado = Decision.APROBADA if accion == 'aprobar' else Decision.RECHAZADA
        decision.decidido_por, decision.decidido_en = usuario, ahora
        decision.save(update_fields=['estado', 'decidido_por', 'decidido_en', 'updated_by', 'updated_at'])
        _cerrar_hallazgo(decision.hallazgo, 'ATENDIDO' if accion == 'aprobar' else 'IGNORADO', usuario, ahora)
    if accion == 'aprobar':
        return True, 'Aprobada. Queda registrada; ejecútala desde la misma sección.'
    return True, 'Rechazada.'


def sincronizar_hallazgo_ignorado(hallazgo) -> None:
    """Si el hallazgo se ignora desde la bandeja de Comunicación, la decisión se rechaza."""
    Decision.objects.filter(hallazgo=hallazgo, estado__in=Decision.ABIERTAS).update(
        estado=Decision.RECHAZADA, decidido_en=timezone.now(), updated_at=timezone.now())


# ───────────────────────────── Prospecto sin respuesta ────────────────────────

def en_horario(ahora: datetime) -> bool:
    return HORA_INICIO <= timezone.localtime(ahora).hour < HORA_FIN


def revisar_sin_respuesta(ahora: Optional[datetime] = None, parametros: Optional[ParametroMarketing] = None) -> list:
    """Pendiente URGENTE al ENCARGADO por cada prospecto abierto cuyo último mensaje es suyo y lleva más de
    N minutos sin respuesta, dentro de 8:00 a 20:00 (Mérida). La espera de lo escrito fuera de horario se
    cuenta desde las 8:00. Idempotente por prospecto; el detalle nunca lleva texto del cliente."""
    ahora = ahora or timezone.now()
    if not en_horario(ahora):
        return []
    p = parametros or ParametroMarketing.vigente()
    local = timezone.localtime(ahora)
    apertura = timezone.make_aware(datetime.combine(local.date(), time(HORA_INICIO)))
    creados = []
    leads = (Lead.objects.exclude(etapa__in=Lead.CERRADAS).filter(conversacion__isnull=False)
             .select_related('conversacion').order_by('id'))
    for lead in leads:
        ultimo = MensajeWhatsApp.objects.filter(conversacion=lead.conversacion).order_by('-created_at', '-id').first()
        if ultimo is None or ultimo.direccion != 'ENTRADA' or ultimo.created_at < ahora - timedelta(hours=MAX_ESPERA_HORAS):
            continue
        espera = ahora - max(ultimo.created_at, apertura)
        if espera < timedelta(minutes=p.sin_respuesta_minutos):
            continue
        minutos = int(espera.total_seconds() // 60)
        pendiente = registrar_pendiente(
            titulo=f'Prospecto sin respuesta hace {minutos} min', rol='ENCARGADO', tipo='PROSPECTO',
            severidad='URGENTE', objeto=lead, clave_idempotencia=f'mkt_sin_respuesta:{lead.pk}',
            detalle=(f'Prospecto: {lead.nombre or "sin nombre"} (teléfono terminado en {lead.telefono[-4:]})\n'
                     f'Canal: {lead.get_fuente_display()}\n'
                     f'Su último mensaje espera respuesta desde las {timezone.localtime(max(ultimo.created_at, apertura)):%H:%M}.\n'
                     f'Responde desde Conversaciones: {_base_url()}{reverse("admin:comunicacion_conversacionwhatsapp_changelist")}'))
        if pendiente is not None:
            creados.append(pendiente)
    return creados


# ───────────────────────────── Resumen del lunes ──────────────────────────────

def bloque_resumen(hoy: Optional[date] = None, parametros: Optional[ParametroMarketing] = None) -> Optional[dict]:
    """Bloque «Marketing» del resumen del lunes: gasto de la semana, prospectos, cotizaciones, ganados y CAC
    (o ROAS cobrado) contra su tope, más hasta 3 decisiones abiertas. `None` si no hay nada que contar."""
    hoy = hoy or timezone.localdate()
    p = parametros or ParametroMarketing.vigente()
    semana = kpis.calcular(hoy - timedelta(days=6), hoy, parametros=p)['total']
    decisiones = list(Decision.objects.filter(estado=Decision.PENDIENTE).order_by('created_at', 'id')[:3])
    abiertas = Decision.objects.filter(estado=Decision.PENDIENTE).count()
    if not (semana['gasto'] or semana['prospectos'] or decisiones):
        return None

    ventana = kpis.calcular(hoy - timedelta(days=p.ventana_dias - 1), hoy, parametros=p)
    topes = kpis.topes_cac(hoy, p)
    grupo = max(ventana['por_servicio'].values(), key=lambda g: g['gasto'], default=None) \
        if any(g['gasto'] > 0 for g in ventana['por_servicio'].values()) else None
    ref = grupo or ventana['total']
    tope = topes.get(ref.get('tipo_servicio'), {}).get('tope') if grupo else None
    nombre = f' de {ETIQUETAS.get(grupo["tipo_servicio"], grupo["nombre"])}' if grupo else ''
    if ref['cac'] is not None:
        cac = f'CAC ({p.ventana_dias} días){nombre}: {dinero(ref["cac"])}'
        cac += f' contra tope {dinero(tope)}' if tope is not None else ' (sin tope: captura el margen en Indicadores)'
    elif ref['roas_cobrado'] is not None:
        cac = f'ROAS cobrado ({p.ventana_dias} días){nombre}: {ref["roas_cobrado"]}×'
        cac += f' (CAC sin cifra: {ref["motivos"].get("cac", "sin datos")})'
    else:
        cac = f'CAC ({p.ventana_dias} días): sin cifra, {ref["motivos"].get("cac", "datos insuficientes")}'
    lineas = [
        f'Marketing, gasto de la semana: {dinero(semana["gasto"])} (sin IVA)',
        f'Marketing, prospectos {semana["prospectos"]}, cotizaciones {semana["cotizaciones"]}, '
        f'ganados {semana["ganados"]}',
        f'Marketing, {cac}',
    ]
    lineas.extend(f'Marketing propone: {d.titulo}' for d in decisiones)
    if abiertas > len(decisiones):
        lineas.append(f'Marketing tiene {abiertas - len(decisiones)} decisiones más por revisar')
    enlace = _base_url() + reverse('marketing:tablero') + '#decisiones' if abiertas else ''
    return {'lineas': lineas, 'enlace': enlace, 'abiertas': abiertas}
