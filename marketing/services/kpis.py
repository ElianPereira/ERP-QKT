"""Indicadores de marketing (Issue #468, fase 3).

Todo importe va **sin IVA**, en MXN, `Decimal` con `ROUND_HALF_UP`. Una razón sin base (denominador cero,
gasto cero o muestra pequeña) vale `None` y su motivo queda en `motivos`; nunca lanza ni inventa una cifra.

Cortes de ingreso, por prospecto ganado (la cotización que lo ganó):

- **Contratado** = `subtotal - descuento` de la cotización (sin IVA ni ISH), vía `indicadores.services.base.venta_neta`.
- **Cobrado** = pagos de la venta menos reembolsos (`Cotizacion.total_pagado_neto()`), llevado a su base sin
  IVA con la misma proporción de la cotización: `cobrado × contratado / precio_final`. El ISH queda fuera
  porque no es ingreso.
- **Neto** = cobrado − gasto de pauta, en pesos.

Atribución por **cohorte**: los prospectos se agrupan por el mes de su primer contacto y se les carga el
gasto de ese mismo mes. Una cohorte es *madura* cuando ya pasó el ciclo típico de venta del servicio desde
que terminó su mes; antes de eso su cierre y su ROAS todavía suben.

Fórmulas (la unidad entre paréntesis):

- CPL = gasto / prospectos (MXN por prospecto)
- Costo por cotización = gasto / prospectos con cotización (MXN)
- Tasa de cierre = ganados / prospectos × 100 (%)
- CAC = gasto / ganados (MXN por venta ganada); el gasto incluye pauta y plantillas de WhatsApp
- ROAS cobrado (el oficial) = cobrado / gasto (veces); ROAS contratado = contratado / gasto (veces)
- Tope de CAC = margen de contribución de una venta × reinversión / 100 (MXN)

El total mezcla todos los prospectos (también los orgánicos); por campaña y por servicio solo cuentan
los prospectos atribuidos a una campaña por `Lead.anuncio_id`, y el servicio es el de la campaña.
"""
from calendar import monthrange
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, Optional

from django.db.models import Sum
from django.utils import timezone

from core_erp.impuestos import centavos
from indicadores.models import ParametrosNegocio
from indicadores.services.base import D0, ESTANCIA_DEFAULT, ETIQUETAS, TIPOS, dividir, q2, venta_neta, ventana_promedio
from indicadores.services.servicio import economia_servicio
from indicadores.services.unitarios import EconomiaUnitariaService

from ..models import Anuncio, Campana, GastoPublicitario, Lead, ParametroMarketing

ESTADOS_VIVOS = ('BORRADOR', 'COTIZADA')
SIN_SERVICIO = ''
CLAVES_RAZON = ('cpl', 'costo_cotizacion', 'tasa_cierre', 'cac', 'roas_cobrado', 'roas_contratado')


def cobrado_sin_iva(cotizacion) -> Decimal:
    """Cobrado neto de reembolsos de una cotización, en su base sin IVA (ni ISH). Nunca negativo."""
    cobrado = cotizacion.total_pagado_neto()
    if cobrado <= 0 or cotizacion.precio_final <= 0:
        return D0
    base = venta_neta(cotizacion)
    return min(centavos(cobrado * base / cotizacion.precio_final), base)


def _razones(*, gasto, prospectos, cotizaciones, ganados, contratado, cobrado, minimo) -> dict:
    """Las seis razones del grupo y el motivo de cada `None`."""
    motivos = {}
    suficiente = prospectos >= minimo

    def razon(clave, numerador, denominador, sin_base, *, necesita_gasto=False):
        if not suficiente:
            motivos[clave] = f'Muestra insuficiente: {prospectos} de {minimo} prospectos'
            return None
        if necesita_gasto and gasto <= 0:
            motivos[clave] = 'Sin gasto en el periodo'
            return None
        if denominador <= 0:
            motivos[clave] = sin_base
            return None
        return dividir(numerador, denominador)

    return {
        'cpl': razon('cpl', gasto, prospectos, 'Sin prospectos', necesita_gasto=True),
        'costo_cotizacion': razon('costo_cotizacion', gasto, cotizaciones, 'Sin cotizaciones', necesita_gasto=True),
        'tasa_cierre': razon('tasa_cierre', ganados * 100, prospectos, 'Sin prospectos'),
        'cac': razon('cac', gasto, ganados, 'Sin ventas ganadas', necesita_gasto=True),
        'roas_cobrado': razon('roas_cobrado', cobrado, gasto, 'Sin gasto en el periodo'),
        'roas_contratado': razon('roas_contratado', contratado, gasto, 'Sin gasto en el periodo'),
        'motivos': motivos, 'suficiente': suficiente,
    }


def _grupo(nombre, filas, gasto, minimo, **extra) -> dict:
    gasto = q2(gasto)
    contratado = sum((f['contratado'] for f in filas), D0)
    cobrado = sum((f['cobrado'] for f in filas), D0)
    cifras = {
        'prospectos': len(filas),
        'cotizaciones': sum(1 for f in filas if f['cotizado']),
        'ganados': sum(1 for f in filas if f['ganado']),
        'contratado': q2(contratado), 'cobrado': q2(cobrado), 'gasto': gasto, 'neto': q2(cobrado) - gasto,
    }
    return {'nombre': nombre, **cifras, **extra,
            **_razones(gasto=gasto, prospectos=cifras['prospectos'], cotizaciones=cifras['cotizaciones'],
                       ganados=cifras['ganados'], contratado=contratado, cobrado=cobrado, minimo=minimo)}


def calcular(desde: date, hasta: date, *, parametros: Optional[ParametroMarketing] = None) -> Dict:
    """Total, por servicio y por campaña de los prospectos cuyo primer contacto cae en [desde, hasta]
    contra el gasto de esas mismas fechas."""
    p = parametros or ParametroMarketing.vigente()
    minimo = p.muestra_minima_leads
    campana_de = dict(Anuncio.objects.values_list('id_externo', 'campana_id'))
    campanas = {c.pk: c for c in Campana.objects.all()}

    filas = []
    leads = Lead.objects.filter(fecha_primer_contacto__date__gte=desde, fecha_primer_contacto__date__lte=hasta
                                ).select_related('cotizacion')
    for lead in leads:
        cot = lead.cotizacion
        ganado = lead.etapa == Lead.GANADO and cot is not None
        campana_id = campana_de.get(lead.anuncio_id) if lead.anuncio_id else None
        filas.append({
            'campana_id': campana_id, 'cotizado': cot is not None, 'ganado': ganado,
            'contratado': venta_neta(cot) if ganado else D0,
            'cobrado': cobrado_sin_iva(cot) if ganado else D0,
        })

    gasto_por_campana = {
        r['campana_id']: r['t'] for r in GastoPublicitario.objects.filter(fecha__gte=desde, fecha__lte=hasta)
        .values('campana_id').annotate(t=Sum('monto'))}

    por_campana = {}
    for campana_id in sorted(set(gasto_por_campana) | {f['campana_id'] for f in filas if f['campana_id']}):
        c = campanas.get(campana_id)
        por_campana[campana_id] = _grupo(
            c.nombre if c else f'Campaña {campana_id}', [f for f in filas if f['campana_id'] == campana_id],
            gasto_por_campana.get(campana_id, D0), minimo,
            campana_id=campana_id, tipo_servicio=c.tipo_servicio if c else SIN_SERVICIO)

    por_servicio = {}
    for tipo in (*TIPOS, SIN_SERVICIO):
        ids = {cid for cid, g in por_campana.items() if g['tipo_servicio'] == tipo}
        if not ids and tipo == SIN_SERVICIO:
            continue
        por_servicio[tipo] = _grupo(
            ETIQUETAS.get(tipo, 'Sin servicio asignado'), [f for f in filas if f['campana_id'] in ids],
            sum((gasto_por_campana.get(i, D0) for i in ids), D0), minimo, tipo_servicio=tipo)

    total = _grupo('Total', filas, sum(gasto_por_campana.values(), D0), minimo)
    return {'desde': desde, 'hasta': hasta, 'total': total, 'por_servicio': por_servicio,
            'por_campana': list(por_campana.values()), 'minimo': minimo}


def topes_cac(hoy: Optional[date] = None, parametros: Optional[ParametroMarketing] = None) -> Dict[str, dict]:
    """Margen de contribución de una venta y tope de CAC por servicio (solo lectura sobre Indicadores).

    margen = `mc` de `economia_servicio` (precio neto − costo directo); en hospedaje la unidad es una
    habitación-noche, así que se multiplica por las noches-habitación promedio de una reservación.
    Tope = margen × reinversión / 100, salvo que el propietario haya capturado uno manual.
    """
    hoy = hoy or timezone.localdate()
    p = parametros or ParametroMarketing.vigente()
    negocio = ParametrosNegocio.vigente()
    desde, hasta = ventana_promedio(hoy.year, hoy.month, negocio)
    historico = EconomiaUnitariaService.generar(desde, hasta, negocio)
    manual = p.cac_tope_manual or {}
    resultado = {}
    for tipo in TIPOS:
        fila = historico['tipos'][tipo]
        eco = economia_servicio(tipo, fila, negocio)
        margen = eco['mc']
        if margen is not None and tipo == 'HOSPEDAJE':
            noches = dividir(fila['noches_habitacion'], fila['ventas']) or ESTANCIA_DEFAULT
            margen = q2(margen * noches)
        if margen is not None and margen <= 0:
            margen = None
        if manual.get(tipo) is not None:
            tope, fuente, motivo = q2(Decimal(str(manual[tipo]))), 'manual', ''
        elif margen is None:
            tope, fuente = None, ''
            motivo = 'Sin margen de contribución: captura precio y costo en Indicadores'
        else:
            tope = (margen * p.pct_reinversion / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            fuente, motivo = 'margen', ''
        resultado[tipo] = {'margen': margen, 'tope': tope, 'fuente': fuente, 'motivo': motivo}
    return resultado


def semaforo(cac, tope, roas_cobrado) -> str:
    """'exito', 'alerta', 'error' o 'neutro' (sin datos). Error si el CAC rebasa el tope; alerta si el ROAS
    cobrado no llega a 1 (la pauta aún no se paga con lo cobrado); éxito si ninguna falla y hay al menos
    un dato; neutro si no hay ninguno."""
    if cac is not None and tope is not None and cac > tope:
        return 'error'
    if roas_cobrado is not None and roas_cobrado < 1:
        return 'alerta'
    if (cac is not None and tope is not None) or roas_cobrado is not None:
        return 'exito'
    return 'neutro'


def pronostico_anticipos(parametros: Optional[ParametroMarketing] = None) -> Dict:
    """Anticipos esperados de los prospectos abiertos con cotización viva.

    Por cotización: prob(etapa del prospecto) × valor sin IVA × % de anticipo que confirma
    (`Cotizacion.porcentaje_anticipo_confirmacion()`) / 100, en MXN. Sin cotización viva no hay valor que
    ponderar, así que esas etapas no aportan.
    """
    p = parametros or ParametroMarketing.vigente()
    por_servicio = {}
    leads = Lead.objects.exclude(etapa__in=Lead.CERRADAS).filter(cotizacion__estado__in=ESTADOS_VIVOS
                                                                  ).select_related('cotizacion')
    for lead in leads:
        cot = lead.cotizacion
        valor = venta_neta(cot)
        esperado = q2(valor * p.prob_de(lead.etapa) * cot.porcentaje_anticipo_confirmacion() / 100)
        fila = por_servicio.setdefault(cot.tipo_servicio, {'cotizaciones': 0, 'valor': D0, 'esperado': D0})
        fila['cotizaciones'] += 1
        fila['valor'] += valor
        fila['esperado'] += esperado
    total = {'cotizaciones': sum(f['cotizaciones'] for f in por_servicio.values()),
             'valor': q2(sum((f['valor'] for f in por_servicio.values()), D0)),
             'esperado': q2(sum((f['esperado'] for f in por_servicio.values()), D0))}
    return {'total': total, 'por_servicio': {t: {**f, 'valor': q2(f['valor']), 'esperado': q2(f['esperado']),
                                                 'nombre': ETIQUETAS.get(t, t)}
                                             for t, f in sorted(por_servicio.items())}}


def _mes_atras(hoy: date, n: int) -> date:
    indice = hoy.year * 12 + hoy.month - 1 - n
    return date(indice // 12, indice % 12 + 1, 1)


def cohortes(hoy: Optional[date] = None, meses: int = 6, parametros: Optional[ParametroMarketing] = None) -> list:
    """Una fila por mes de primer contacto (el más antiguo primero) con su `calcular` y su madurez.

    La cohorte es madura cuando el fin de su mes más el ciclo de venta ya pasó: del servicio más largo
    para el total (`madura`) y de cada servicio en `madura_por_servicio`.
    """
    hoy = hoy or timezone.localdate()
    p = parametros or ParametroMarketing.vigente()
    filas = []
    for n in range(meses - 1, -1, -1):
        inicio = _mes_atras(hoy, n)
        fin = date(inicio.year, inicio.month, monthrange(inicio.year, inicio.month)[1])
        datos = calcular(inicio, fin, parametros=p)
        madura_por = {t: fin + timedelta(days=p.ciclo_de(t)) <= hoy for t in TIPOS}
        filas.append({'mes': inicio, 'etiqueta': f'{inicio:%m/%Y}', 'datos': datos, 'total': datos['total'],
                      'madura': all(madura_por.values()), 'madura_por_servicio': madura_por})
    return filas


def resumen(hoy: Optional[date] = None, parametros: Optional[ParametroMarketing] = None) -> Dict:
    """Todo lo que muestra la pantalla: ventana reciente con semáforo por servicio, cohortes y pronóstico."""
    hoy = hoy or timezone.localdate()
    p = parametros or ParametroMarketing.vigente()
    datos = calcular(hoy - timedelta(days=p.ventana_dias - 1), hoy, parametros=p)
    topes = topes_cac(hoy, p)
    for tipo, grupo in datos['por_servicio'].items():
        t = topes.get(tipo, {'margen': None, 'tope': None, 'fuente': '', 'motivo': 'Sin servicio asignado'})
        grupo['cac_tope'], grupo['margen'], grupo['tope_motivo'] = t['tope'], t['margen'], t['motivo']
        grupo['semaforo'] = semaforo(grupo['cac'], t['tope'], grupo['roas_cobrado'])
    return {'hoy': hoy, 'parametros': p, 'ventana': datos, 'topes': topes, 'cohortes': cohortes(hoy, parametros=p),
            'pronostico': pronostico_anticipos(p)}
