"""
Mezcla óptima de servicios (Issue #402)
=======================================
Pregunta: con los días que tengo este mes, ¿qué combinación de eventos,
pasadías y hospedaje maximiza la utilidad?

**Modelo.** El recurso escaso es el día-quinta (un solo servicio por día). Los
días vendibles del mes (sin bloqueos) se agrupan en dos segmentos, **fin de
semana (vie-dom)** y **entre semana (lun-jue)**, y dentro de cada segmento en
*bolsas* según qué servicios se pueden vender ese día de la semana. Cada
servicio consume `dias_por_unidad` días de una bolsa donde está permitido y
aporta su margen de contribución (MC) por unidad. Restricciones: el tope de
eventos (`max_eventos_semana` por semana) y `demanda_max_*_mes`.

**Por qué el voraz es óptimo.** Es una mochila *fraccional* con un solo recurso
(el día) por bolsa y topes por servicio: ordenando por **MC por día**
descendente y llenando cada servicio hasta su tope, ninguna reasignación de días
a un servicio de menor MC/día puede mejorar el resultado, así que el voraz es
exacto con unidades fraccionales. Para llevarlo a unidades enteras se
redondea hacia abajo y el sobrante de días queda disponible para el siguiente
servicio. Con unidades de un día (evento y pasadía) la solución entera también
es óptima. Las estancias de varios días (hospedaje) dejan días muertos al
redondear; por eso `asignar_optimo` enumera cuántas estancias van en cada bolsa
y deja que el voraz llene el resto: exacto, y barato porque son pocas bolsas.
Entre bolsas, un servicio consume primero la bolsa donde menos servicios de
menor MC/día caben, para no quitarles días.

Si algún día se permite vender dos servicios el mismo día, este modelo cambia.
"""
import itertools
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from ..models import ParametrosNegocio
from .base import (
    D0,
    ETIQUETAS,
    TIPOS,
    corte,
    dias_bloqueados,
    dias_del_rango,
    mes_anterior,
    mes_rango,
    porcentaje,
    q2,
    ventana_promedio,
)
from .capacidad import CapacidadService, eventos_maximos
from .costos_fijos import CostosFijosService
from .unitarios import EconomiaUnitariaService

FIN_DE_SEMANA = 'FIN_DE_SEMANA'
ENTRE_SEMANA = 'ENTRE_SEMANA'
SEGMENTOS = {FIN_DE_SEMANA: 'Fin de semana (vie-dom)', ENTRE_SEMANA: 'Entre semana (lun-jue)'}


def asignar_voraz(bolsas: Dict[tuple, Decimal], servicios: List[dict]) -> Dict[tuple, int]:
    """Voraz por MC/día (ver el docstring del módulo).

    `bolsas`: {(segmento, firma): días}, donde `firma` es la tupla de tipos permitidos.
    `servicios`: ya ordenados por MC/día descendente; cada uno con `tipo`, `dias_unidad`,
    `mc` (por unidad) y `tope` (unidades máximas o `None`). Devuelve {(tipo, segmento): unidades}.
    """
    libres = dict(bolsas)
    por_bolsa = {}       # (tipo, bolsa) -> unidades
    orden = [s['tipo'] for s in servicios]
    topes = {s['tipo']: s['tope'] for s in servicios}
    dias_unidad_de = {s['tipo']: s['dias_unidad'] for s in servicios}
    for s in servicios:
        tipo = s['tipo']
        inferiores = set(orden[orden.index(tipo) + 1:])

        def prioridad(clave):
            # 1) la bolsa donde caben menos servicios de menor MC/día; 2) la que, tras
            # servir a este, deja menos días muertos a una estancia de varios días
            # (resto de días no divisible); 3) fin de semana primero.
            segmento, firma = clave
            menores = inferiores & set(firma)
            estancia = max((dias_unidad_de[t] for t in menores), default=Decimal(1))
            desperdicio = estancia > 1 and (libres[clave] % estancia) > 0
            return (len(menores), not desperdicio, segmento != FIN_DE_SEMANA)

        for clave in sorted((c for c in libres if tipo in c[1]), key=prioridad):
            restante = topes[tipo]
            if restante is not None and restante <= 0:
                break
            cabe = int(libres[clave] // s['dias_unidad'])
            n = cabe if restante is None else min(cabe, restante)
            if n <= 0:
                continue
            libres[clave] -= n * s['dias_unidad']
            por_bolsa[(tipo, clave)] = por_bolsa.get((tipo, clave), 0) + n
            if restante is not None:
                topes[tipo] = restante - n

    unidades = {}
    for (tipo, (segmento, _)), n in por_bolsa.items():
        if n:
            unidades[(tipo, segmento)] = unidades.get((tipo, segmento), 0) + n
    return unidades


LIMITE_COMBINACIONES = 20000


def _valor(unidades: Dict[tuple, int], mc: Dict[str, Decimal]) -> Decimal:
    return sum((n * mc[tipo] for (tipo, _), n in unidades.items()), Decimal(0))


def asignar_optimo(bolsas: Dict[tuple, Decimal], servicios: List[dict]) -> Dict[tuple, int]:
    """
    Solución entera exacta. Los servicios de un día se resuelven con el voraz (exacto);
    los de varios días (hospedaje, o evento con preparación) crean días muertos al
    redondear, así que se enumeran cuántas estancias van en cada bolsa y, para cada
    caso, el voraz llena el resto. Con más de `LIMITE_COMBINACIONES` casos (calendarios
    muy fragmentados) cae al voraz puro, que pierde a lo más una estancia por bolsa.
    """
    multi = [s for s in servicios if s['dias_unidad'] != 1]
    if not multi:
        return asignar_voraz(bolsas, servicios)
    unitarios = [s for s in servicios if s['dias_unidad'] == 1]
    pares = [(s, c) for s in multi for c in bolsas if s['tipo'] in c[1]]
    maximos = [int(bolsas[c] // s['dias_unidad']) for s, c in pares]
    combinaciones = 1
    for m in maximos:
        combinaciones *= m + 1
    if combinaciones > LIMITE_COMBINACIONES:
        return asignar_voraz(bolsas, servicios)

    mc = {s['tipo']: s['mc'] for s in servicios}
    mejor, mejor_valor = {}, Decimal(-1)
    for conteos in itertools.product(*(range(m + 1) for m in maximos)):
        resto = dict(bolsas)
        usados = {s['tipo']: 0 for s in multi}
        asignado, factible = {}, True
        for (s, clave), n in zip(pares, conteos):
            if not n:
                continue
            resto[clave] -= n * s['dias_unidad']
            usados[s['tipo']] += n
            asignado[(s['tipo'], clave[0])] = asignado.get((s['tipo'], clave[0]), 0) + n
            if resto[clave] < 0 or (s['tope'] is not None and usados[s['tipo']] > s['tope']):
                factible = False
                break
        if not factible:
            continue
        completo = {**asignar_voraz(resto, unitarios), **asignado}
        valor = _valor(completo, mc)
        if valor > mejor_valor:
            mejor, mejor_valor = completo, valor
    return mejor


def _bolsas(desde: date, hasta: date, p: ParametrosNegocio) -> Dict[tuple, Decimal]:
    bloqueados = dias_bloqueados(desde, hasta)
    bolsas = {}
    for d in dias_del_rango(desde, hasta):
        if d in bloqueados:
            continue
        firma = tuple(t for t in TIPOS if d.isoweekday() in p.dias_de(t))
        if not firma:
            continue
        segmento = FIN_DE_SEMANA if d.isoweekday() >= 5 else ENTRE_SEMANA
        bolsas[(segmento, firma)] = bolsas.get((segmento, firma), D0) + 1
    return bolsas


class MezclaOptimaService:

    @classmethod
    def generar(cls, anio: int, mes: int, parametros: Optional[ParametrosNegocio] = None,
                overrides: Optional[dict] = None, hoy: Optional[date] = None,
                economia: Optional[dict] = None, costos: Optional[dict] = None) -> Dict:
        from django.utils import timezone

        p = parametros or ParametrosNegocio.vigente()
        overrides = overrides or {}
        hoy = hoy or timezone.localdate()
        inicio, fin = mes_rango(anio, mes)
        economia = economia or EconomiaUnitariaService.generar(
            *ventana_promedio(anio, mes, p), parametros=p, overrides=overrides)
        costos = costos or CostosFijosService.generar(corte(anio, mes, hoy), p)
        capacidad = CapacidadService.generar(inicio, fin, p, economia)
        bolsas = _bolsas(inicio, fin, p)
        total_dias = sum(bolsas.values(), D0)

        candidatos, avisos = [], list(economia['avisos'])
        for tipo in TIPOS:
            e = economia['tipos'][tipo]
            if e['mc_dia'] is None:
                continue
            dias_unidad = e['dias_por_unidad']
            if tipo == 'EVENTO':
                dias_unidad = Decimal(1 + p.dias_preparacion_evento)
            tope = overrides.get(f'demanda_{tipo}', p.demanda_de(tipo))
            if tipo == 'EVENTO':
                maximo = eventos_maximos(inicio, fin, p)
                tope = maximo if tope is None else min(int(tope), maximo)
            candidatos.append({
                'tipo': tipo, 'dias_unidad': dias_unidad, 'tope': None if tope is None else int(tope),
                'mc_dia': q2(e['mc'] / dias_unidad), 'mc': e['mc'], 'ticket': e['ticket'],
            })
        candidatos.sort(key=lambda c: (-c['mc_dia'], TIPOS.index(c['tipo'])))

        asignado = asignar_optimo(bolsas, candidatos)
        filas, contribucion, ingreso, dias_usados = [], D0, D0, D0
        por_tipo = {c['tipo']: c for c in candidatos}
        for tipo in TIPOS:
            c = por_tipo.get(tipo)
            segmentos = {s: asignado.get((tipo, s), 0) for s in SEGMENTOS}
            unidades = sum(segmentos.values())
            dias = unidades * c['dias_unidad'] if c else D0
            mc = unidades * c['mc'] if c else D0
            venta = unidades * c['ticket'] if c else D0
            contribucion += mc
            ingreso += venta
            dias_usados += dias
            filas.append({
                'tipo': tipo, 'nombre': ETIQUETAS[tipo], 'unidades': unidades, 'segmentos': segmentos,
                'dias': q2(dias), 'ingreso': q2(venta), 'contribucion': q2(mc),
                'mc_dia': c['mc_dia'] if c else None, 'tope': c['tope'] if c else None,
                'fuente_costo': economia['tipos'][tipo]['fuente_costo'],
            })

        real = cls._real(anio, mes, p, economia)
        cf = costos['cf']
        return {
            'anio': anio, 'mes': mes, 'segmentos': SEGMENTOS,
            'dias_por_segmento': {s: sum(v for (seg, _), v in bolsas.items() if seg == s) for s in SEGMENTOS},
            'filas': filas, 'ingreso': q2(ingreso), 'contribucion': q2(contribucion), 'cf': cf,
            'fuente_cf': costos['fuente'], 'utilidad': q2(contribucion - cf),
            'dias_ocupados': q2(dias_usados), 'ocupacion': porcentaje(dias_usados, total_dias),
            'real': real, 'brecha': q2(contribucion - real['contribucion']),
            'capacidad': capacidad, 'avisos': avisos,
        }

    @staticmethod
    def _real(anio: int, mes: int, p, economia: dict) -> Dict:
        """Mezcla real del mes anterior valuada con el MC unitario de la ventana de promedio."""
        a, m = mes_anterior(anio, mes)
        previo = EconomiaUnitariaService.generar(*mes_rango(a, m), parametros=p)
        unidades = {t: previo['tipos'][t]['ventas'] for t in TIPOS}
        contribucion = sum(
            (unidades[t] * economia['tipos'][t]['mc'] for t in TIPOS if economia['tipos'][t]['mc'] is not None), D0)
        return {'anio': a, 'mes': m, 'unidades': unidades, 'contribucion': q2(contribucion),
                'dias': sum(previo['tipos'][t]['dias'] for t in TIPOS)}
