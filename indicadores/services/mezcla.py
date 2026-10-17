"""
Mezcla óptima de servicios (Issue #402, #409)
=============================================
Pregunta: con los días que tengo este mes y lo que creo poder vender de cada servicio, ¿qué
combinación de eventos, pasadías y hospedaje deja más utilidad neta?

**Qué entra por servicio.** Precio neto, costo y margen salen de `servicio.economia_servicio`, la
misma fuente de capacidad, equilibrio y metas. La unidad de hospedaje es una habitación, una
noche, y ocupa `1 / HABITACIONES` de día-quinta (supone que, si hay demanda, las dos habitaciones
se rentan la misma noche); los días que consume un grupo de unidades en una bolsa se redondean
hacia arriba (3 habitaciones-noche = 2 días) para no dejar medio día a un evento.

**Topes.** Cada servicio con precio necesita su «Tope al mes» (lo que cree poder vender). Con todos
los topes el resultado es un `plan` (`modo='plan'`); si a algún servicio le falta, es un «techo
teórico» (`modo='techo'`): lo que cabría si se vendiera todo lo que cabe, que no se puede
prometer. El tope de eventos además está acotado por `max_eventos_semana`.

**Resultado.** `plan` (frase, venta, costo directo, contribución, costos fijos y utilidad neta) y
`escenarios` para compararlo: plan óptimo, mes anterior real, y solo eventos / pasadías /
hospedaje, cada uno con su tope. Utilidad neta = contribución − costos fijos.

**Modelo.** El recurso escaso es el día-quinta (un solo servicio por día). Los
días vendibles del mes (sin bloqueos) se agrupan en dos segmentos, **fin de
semana (vie-dom)** y **entre semana (lun-jue)**, y dentro de cada segmento en
*bolsas* según qué servicios se pueden vender ese día de la semana. Cada
servicio consume `dias_unidad` días de una bolsa donde está permitido y
aporta su margen de contribución (MC) por unidad. Restricciones: el tope de
eventos (`max_eventos_semana` por semana) y el tope al mes de cada servicio.

**Por qué el voraz es óptimo.** Es una mochila *fraccional* con un solo recurso
(el día) por bolsa y topes por servicio: ordenando por **MC por día**
descendente y llenando cada servicio hasta su tope, ninguna reasignación de días
a un servicio de menor MC/día puede mejorar el resultado, así que el voraz es
exacto con unidades de un día o menos (evento sin preparación, pasadía,
habitación-noche: el medio día que sobra de una cantidad impar no lo puede usar nadie).
Las estancias de varios días (evento con preparación) dejan días muertos al
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
    consumo_dias,
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
from .servicio import economia_servicios
from .unitarios import EconomiaUnitariaService

FIN_DE_SEMANA = 'FIN_DE_SEMANA'
ENTRE_SEMANA = 'ENTRE_SEMANA'
SEGMENTOS = {FIN_DE_SEMANA: 'Fin de semana (vie-dom)', ENTRE_SEMANA: 'Entre semana (lun-jue)'}
NOTA_HOSPEDAJE = 'Supone que, si hay demanda, las dos habitaciones se rentan la misma noche.'
NOMBRES_SOLO = {'EVENTO': 'Solo eventos', 'PASADIA': 'Solo pasadías', 'HOSPEDAJE': 'Solo hospedaje'}


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
            libres[clave] -= consumo_dias(n, s['dias_unidad'])
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
    Solución entera exacta. Los servicios de un día o menos se resuelven con el voraz (exacto);
    los de varios días (evento con preparación) crean días muertos al
    redondear, así que se enumeran cuántas estancias van en cada bolsa y, para cada
    caso, el voraz llena el resto. Con más de `LIMITE_COMBINACIONES` casos (calendarios
    muy fragmentados) cae al voraz puro, que pierde a lo más una estancia por bolsa.
    """
    multi = [s for s in servicios if s['dias_unidad'] > 1]
    if not multi:
        return asignar_voraz(bolsas, servicios)
    unitarios = [s for s in servicios if s['dias_unidad'] <= 1]
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


def _por_tipo(asignado: Dict[tuple, int]) -> Dict[str, int]:
    return {tipo: sum(n for (t, _), n in asignado.items() if t == tipo) for tipo in TIPOS}


def _plural(n: int, singular: str, plural: str) -> str:
    return f'{n} {singular if n == 1 else plural}'


def _frase(unidades: Dict[str, int], eventos_fin_de_semana: int) -> str:
    """«Vende 4 eventos (3 en fin de semana), 6 pasadías y 20 noches-habitación»."""
    partes = []
    if unidades['EVENTO']:
        texto = _plural(unidades['EVENTO'], 'evento', 'eventos')
        if eventos_fin_de_semana:
            texto += f' ({eventos_fin_de_semana} en fin de semana)'
        partes.append(texto)
    if unidades['PASADIA']:
        partes.append(_plural(unidades['PASADIA'], 'pasadía', 'pasadías'))
    if unidades['HOSPEDAJE']:
        partes.append(_plural(unidades['HOSPEDAJE'], 'noche-habitación', 'noches-habitación'))
    if not partes:
        return 'Sin servicios que vender con los datos actuales.'
    return 'Vende ' + (partes[0] if len(partes) == 1 else ', '.join(partes[:-1]) + ' y ' + partes[-1])


def _escenario(nombre: str, unidades: Dict[str, int], por_tipo: dict, cf: Decimal, dias) -> Dict:
    """Valúa `unidades` con el precio y el costo de cada servicio; un servicio sin precio vale cero."""
    venta = sum((n * por_tipo[t]['ticket'] for t, n in unidades.items() if t in por_tipo), D0)
    costo = sum((n * por_tipo[t]['costo'] for t, n in unidades.items() if t in por_tipo), D0)
    return {
        'nombre': nombre, 'unidades': unidades, 'venta': q2(venta), 'costo_directo': q2(costo),
        'contribucion': q2(venta - costo), 'cf': cf, 'utilidad_neta': q2(venta - costo - cf), 'dias': q2(dias),
    }


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
        economia = economia or EconomiaUnitariaService.generar(*ventana_promedio(anio, mes, p), parametros=p)
        costos = costos or CostosFijosService.generar(corte(anio, mes, hoy), p)
        capacidad = CapacidadService.generar(inicio, fin, p, economia, overrides)
        servicios = economia_servicios(economia, p, overrides)
        bolsas = _bolsas(inicio, fin, p)
        total_dias = sum(bolsas.values(), D0)

        candidatos, sin_tope, avisos = [], [], list(economia['avisos'])
        for tipo in TIPOS:
            u = servicios[tipo]
            avisos.extend(u['avisos'])
            if u['ticket'] is None or u['mc'] <= 0:
                continue
            tope = overrides.get(f'demanda_{tipo}', p.demanda_de(tipo))
            if tope is None:
                sin_tope.append(ETIQUETAS[tipo])
            if tipo == 'EVENTO':
                maximo = eventos_maximos(inicio, fin, p)
                tope = maximo if tope is None else min(int(tope), maximo)
            candidatos.append({
                'tipo': tipo, 'dias_unidad': u['dias_unidad'], 'tope': None if tope is None else int(tope),
                'mc_dia': q2(u['mc'] / u['dias_unidad']), 'mc': u['mc'], 'ticket': u['ticket'], 'costo': u['costo'],
                'fuente_precio': u['fuente_precio'], 'fuente_costo': u['fuente_costo'],
            })
        candidatos.sort(key=lambda c: (-c['mc_dia'], TIPOS.index(c['tipo'])))
        modo = 'techo' if sin_tope else 'plan'
        if sin_tope:
            avisos.append(f'Falta el tope al mes de {", ".join(sin_tope)} en Parámetros: el resultado es un techo '
                          'teórico (todo lo que cabe en los días), no un plan que se pueda vender.')

        asignado = asignar_optimo(bolsas, candidatos)
        por_tipo = {c['tipo']: c for c in candidatos}
        filas = []
        for tipo in TIPOS:
            c = por_tipo.get(tipo)
            segmentos = {s: asignado.get((tipo, s), 0) for s in SEGMENTOS}
            unidades = sum(segmentos.values())
            venta = unidades * c['ticket'] if c else D0
            costo = unidades * c['costo'] if c else D0
            filas.append({
                'tipo': tipo, 'nombre': ETIQUETAS[tipo], 'unidades': unidades, 'segmentos': segmentos,
                'dias': q2(consumo_dias(unidades, c['dias_unidad'])) if c else D0,
                'ingreso': q2(venta), 'costo_directo': q2(costo), 'contribucion': q2(venta - costo),
                'mc_dia': c['mc_dia'] if c else None, 'tope': c['tope'] if c else None,
                'precio': c['ticket'] if c else None, 'costo': c['costo'] if c else None,
                'fuente_precio': c['fuente_precio'] if c else None,
                'fuente_costo': c['fuente_costo'] if c else None,
            })
        unidades_plan = {f['tipo']: f['unidades'] for f in filas}
        dias_usados = sum((f['dias'] for f in filas), D0)
        cf = costos['cf']
        escenario_plan = _escenario('Plan óptimo' if modo == 'plan' else 'Techo teórico', unidades_plan,
                                    por_tipo, cf, dias_usados)

        real = cls._real(anio, mes, p, por_tipo)
        for fila in filas:
            fila['real_unidades'] = real['unidades'][fila['tipo']]
        escenarios = [escenario_plan, _escenario(
            'Mes anterior (real)', real['unidades'], por_tipo, cf, real['dias'])]
        for tipo in TIPOS:
            if tipo not in por_tipo:
                avisos.append(f'Escenario «{NOMBRES_SOLO[tipo]}» omitido: {ETIQUETAS[tipo]} no tiene precio con '
                              'margen positivo.')
                continue
            solo = _por_tipo(asignar_optimo(bolsas, [por_tipo[tipo]]))
            escenarios.append(_escenario(NOMBRES_SOLO[tipo], solo, por_tipo, cf,
                                         consumo_dias(solo[tipo], por_tipo[tipo]['dias_unidad'])))

        utilidad_objetivo = q2(p.utilidad_mensual_objetivo)
        plan = {
            'frase': _frase(unidades_plan, filas[0]['segmentos'][FIN_DE_SEMANA]),
            'venta': escenario_plan['venta'], 'costo_directo': escenario_plan['costo_directo'],
            'contribucion': escenario_plan['contribucion'], 'cf': cf, 'utilidad_neta': escenario_plan['utilidad_neta'],
            'utilidad_objetivo': utilidad_objetivo,
            'diferencia_objetivo': q2(escenario_plan['utilidad_neta'] - utilidad_objetivo),
        }
        return {
            'anio': anio, 'mes': mes, 'segmentos': SEGMENTOS, 'modo': modo, 'plan': plan, 'escenarios': escenarios,
            'dias_por_segmento': {s: sum(v for (seg, _), v in bolsas.items() if seg == s) for s in SEGMENTOS},
            'filas': filas, 'ingreso': plan['venta'], 'contribucion': plan['contribucion'], 'cf': cf,
            'fuente_cf': costos['fuente'], 'utilidad': plan['utilidad_neta'],
            'dias_ocupados': q2(dias_usados), 'ocupacion': porcentaje(dias_usados, total_dias),
            'real': real, 'brecha': q2(plan['contribucion'] - real['contribucion']),
            'capacidad': capacidad, 'avisos': avisos, 'nota_hospedaje': NOTA_HOSPEDAJE,
        }

    @staticmethod
    def _real(anio: int, mes: int, p, por_tipo: dict) -> Dict:
        """Mezcla real del mes anterior, en las unidades de la mezcla (hospedaje: habitaciones-noche)."""
        a, m = mes_anterior(anio, mes)
        previo = EconomiaUnitariaService.generar(*mes_rango(a, m), parametros=p)
        unidades, dias = {}, 0
        for t in TIPOS:
            fila = previo['tipos'][t]
            unidades[t] = fila['noches_habitacion'] if t == 'HOSPEDAJE' else fila['ventas']
            dias += fila['dias']
        contribucion = sum((unidades[t] * por_tipo[t]['mc'] for t in TIPOS if t in por_tipo), D0)
        return {'anio': a, 'mes': m, 'unidades': unidades, 'contribucion': q2(contribucion), 'dias': dias}
