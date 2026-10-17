"""
Plan óptimo anual (Issue #410)
==============================
Pregunta: con los días del año, los costos fijos, y el precio y costo variable de cada servicio,
¿qué combinación de eventos, pasadías y hospedaje deja más utilidad? Sin topes de demanda: las
únicas restricciones son las del calendario.

**Restricciones.** Un servicio por día (la Quinta es un solo recurso); cada servicio solo en los
días de la semana de `ParametrosNegocio.dias_de`; máximo `max_eventos_semana` eventos por semana
ISO; un evento ocupa `1 + dias_preparacion_evento` días consecutivos; los días con `BloqueoFecha`
no se venden.

**Valor de un día.** Evento y pasadía = su margen de contribución (MC = precio neto − costo
variable); hospedaje = `HABITACIONES × MC` de una noche (supone que las dos habitaciones se rentan
la misma noche). El plan maximiza la suma de esos valores.

**Algoritmo.** Programación dinámica exacta sobre los días del año. El estado es (eventos de la
semana ISO ya usados, días de preparación pendientes); cada día se asigna a un servicio permitido
o se deja vacío. Como el único acoplamiento entre días es el tope semanal y la preparación, el
resultado es óptimo (los tests lo comparan contra fuerza bruta). La preparación puede cruzar de
una semana a la siguiente; el año termina el 31 de diciembre.

**Precio para entrar.** Para un servicio que no entra al plan, bisección sobre su precio (pesos
enteros, IVA incluido) volviendo a correr el DP hasta que aparece en la solución.

Todo el dinero es `Decimal` con `ROUND_HALF_UP`.
"""
from collections import defaultdict
from datetime import date
from decimal import ROUND_CEILING, Decimal
from typing import Dict, List, Optional

from core_erp.impuestos import con_iva, sin_iva

from ..models import DIAS_ISO, ParametrosNegocio
from .base import (
    D0,
    ETIQUETAS,
    HABITACIONES,
    TIPOS,
    corte,
    dias_bloqueados,
    dias_del_rango,
    ventana_promedio,
)
from .base import q2 as _q2
from .costos_fijos import CostosFijosService
from .servicio import economia_servicios
from .unitarios import EconomiaUnitariaService

PORCENTAJES = (25, 50, 75, 100)
PRECIO_MAXIMO_BUSQUEDA = 2 ** 40   # tope de la bisección (pesos); más allá, el servicio no puede entrar
PREPARACION = 'PREPARACION'


# --- Núcleo: la programación dinámica ------------------------------------------------------------

def optimizar(dias: List[date], valores: Dict[str, Decimal], p: ParametrosNegocio,
              bloqueados=frozenset()) -> Dict[date, str]:
    """
    Asignación óptima de `dias` (contiguos, ordenados): `{día: tipo}` con `tipo` en EVENTO, PASADIA,
    HOSPEDAJE o PREPARACION; un día que no aparece queda vacío. `valores` es lo que deja una
    asignación de cada servicio (evento: su MC; hospedaje: HABITACIONES × MC de la noche); un
    servicio sin valor positivo no se asigna.
    """
    n = len(dias)
    preparacion = p.dias_preparacion_evento
    max_eventos = p.max_eventos_semana
    valores = {t: v for t, v in valores.items() if v is not None and v > 0}
    permitido = {t: p.dias_de(t) for t in valores}
    inicio_semana = [i == 0 or dias[i].isocalendar()[:2] != dias[i - 1].isocalendar()[:2] for i in range(n)]

    estados = [(e, r) for e in range(max_eventos + 1) for r in range(preparacion + 1)]
    mejor: List[Dict[tuple, Decimal]] = [dict.fromkeys(estados, D0) for _ in range(n + 1)]
    eleccion: List[Dict[tuple, tuple]] = [dict() for _ in range(n)]

    def siguiente(i, e):   # eventos de la semana vistos desde el día i + 1
        return 0 if i + 1 >= n or inicio_semana[i + 1] else e

    for i in range(n - 1, -1, -1):
        dia, bloqueado = dias[i], dias[i] in bloqueados
        for e, r in estados:
            if r > 0:      # día de preparación de un evento anterior
                mejor[i][(e, r)] = mejor[i + 1][(siguiente(i, e), r - 1)]
                eleccion[i][(e, r)] = (PREPARACION, (siguiente(i, e), r - 1))
                continue
            en = siguiente(i, e)
            candidato = [(mejor[i + 1][(en, 0)], None, (en, 0))]
            if not bloqueado:
                for tipo in ('HOSPEDAJE', 'PASADIA'):
                    if tipo in valores and dia.isoweekday() in permitido[tipo]:
                        candidato.append((valores[tipo] + mejor[i + 1][(en, 0)], tipo, (en, 0)))
                if 'EVENTO' in valores and e < max_eventos and dia.isoweekday() in permitido['EVENTO']:
                    destino = (siguiente(i, e + 1), preparacion)
                    candidato.append((valores['EVENTO'] + mejor[i + 1][destino], 'EVENTO', destino))
            valor, tipo, destino = candidato[0]
            for c in candidato[1:]:
                if c[0] > valor:   # el empate lo gana lo que ya estaba (vacío primero), así es determinista
                    valor, tipo, destino = c
            mejor[i][(e, r)] = valor
            eleccion[i][(e, r)] = (tipo, destino)

    asignacion, estado = {}, (0, 0)
    for i in range(n):
        tipo, estado_siguiente = eleccion[i][estado]
        if tipo:
            asignacion[dias[i]] = tipo
        estado = estado_siguiente
    return asignacion


def _valores(servicios: Dict[str, dict]) -> Dict[str, Decimal]:
    """Lo que deja una asignación de cada servicio con margen positivo."""
    resultado = {}
    for tipo, s in servicios.items():
        if s['mc'] > 0:
            resultado[tipo] = s['mc'] * (HABITACIONES if tipo == 'HOSPEDAJE' else 1)
    return resultado


# --- Texto ---------------------------------------------------------------------------------------

def _dias_texto(dias_semana: set) -> str:
    """{5, 6, 7} → «vie-dom»; {1, 2, 3, 4} → «entre semana»; salteados → «lun, mié»."""
    ordenados = sorted(dias_semana)
    if len(ordenados) == 7:
        return ''
    if len(ordenados) >= 3 and ordenados[-1] <= 5:
        return 'entre semana'
    if ordenados == list(range(ordenados[0], ordenados[-1] + 1)) and len(ordenados) > 1:
        return f'{DIAS_ISO[str(ordenados[0])]}-{DIAS_ISO[str(ordenados[-1])]}'
    return ', '.join(DIAS_ISO[str(d)] for d in ordenados)


def lista_dias(dias_semana: set) -> str:
    """{5, 6, 7} → «vie, sáb y dom»."""
    nombres = [DIAS_ISO[str(d)] for d in sorted(dias_semana)]
    if not nombres:
        return 'ningún día'
    return nombres[0] if len(nombres) == 1 else ', '.join(nombres[:-1]) + ' y ' + nombres[-1]


def _plural(n: int, singular: str, plural: str) -> str:
    return f'{n} {singular if n == 1 else plural}'


def _frase(anio: int, servicios: Dict[str, dict], unidades: Dict[str, int], dias_usados: Dict[str, set]) -> str:
    """«En 2027 lo que más te conviene es: 104 eventos (vie-dom), 0 pasadías y 520 noches-habitación entre semana»."""
    nombres = {'EVENTO': ('evento', 'eventos'), 'PASADIA': ('pasadía', 'pasadías'),
               'HOSPEDAJE': ('noche-habitación', 'noches-habitación')}
    partes = []
    for tipo in TIPOS:
        if tipo not in servicios:
            continue
        texto = _plural(unidades[tipo], *nombres[tipo])
        donde = _dias_texto(dias_usados[tipo]) if unidades[tipo] else ''
        if donde:
            texto += f' ({donde})' if tipo == 'EVENTO' else f' {donde}'
        partes.append(texto)
    if not partes:
        return f'En {anio} no hay servicios con precio para calcular un plan.'
    cuerpo = partes[0] if len(partes) == 1 else ', '.join(partes[:-1]) + ' y ' + partes[-1]
    return f'En {anio} lo que más te conviene es: {cuerpo}'


def supuestos(p: ParametrosNegocio) -> str:
    """Una línea con las restricciones del calendario, tal como las toma el cálculo."""
    eventos = f'Eventos solo {lista_dias(p.dias_de("EVENTO"))}, máximo {p.max_eventos_semana} por semana'
    if p.dias_preparacion_evento:
        eventos += f', con {_plural(p.dias_preparacion_evento, "día", "días")} de preparación'
    otros = []
    for tipo, nombre in (('PASADIA', 'pasadías'), ('HOSPEDAJE', 'hospedaje')):
        if len(p.dias_de(tipo)) < 7:
            otros.append(f'{nombre} solo {lista_dias(p.dias_de(tipo))}')
    extra = (', ' + ' y '.join(otros)) if otros else ''
    return (f'{eventos}{extra}; {HABITACIONES} habitaciones; un servicio por día; '
            'se excluyen los días bloqueados en el calendario.')


# --- Cálculo del plan ----------------------------------------------------------------------------

def _servicio(tipo: str, neto: Decimal, costo: Decimal, p: ParametrosNegocio) -> dict:
    neto, costo = _q2(neto), _q2(costo)
    mc = neto - costo
    return {
        'tipo': tipo, 'nombre': ETIQUETAS[tipo], 'neto': neto, 'costo': costo, 'mc': mc,
        'precio': con_iva(neto), 'mc_dia': _q2(valor_por_dia(tipo, mc, p)) if mc > 0 else None,
    }


def dias_que_ocupa(tipo: str, p: ParametrosNegocio) -> int:
    """Días de la Quinta que consume una asignación: un evento, su día más la preparación."""
    return 1 + p.dias_preparacion_evento if tipo == 'EVENTO' else 1


def valor_por_dia(tipo: str, mc: Decimal, p: ParametrosNegocio) -> Decimal:
    """Lo que deja por cada día de la Quinta ocupado (hospedaje: el MC de las dos habitaciones)."""
    return mc * (HABITACIONES if tipo == 'HOSPEDAJE' else 1) / dias_que_ocupa(tipo, p)


def optimizar_tipos(dias, servicios, p, bloqueados) -> set:
    """Tipos que aparecen en la solución óptima de `servicios`."""
    return {t for t in optimizar(dias, _valores(servicios), p, bloqueados).values() if t != PREPARACION}


def precio_para_entrar(tipo: str, costo: Decimal, resto: Dict[str, dict], dias, p, bloqueados) -> Optional[Decimal]:
    """
    Precio mínimo (pesos enteros, IVA incluido) con el que `tipo` aparece en el plan, o `None` si no
    puede entrar ni con un precio altísimo (no tiene días permitidos libres).
    """
    def entra(precio: int) -> bool:
        servicios = {**resto, tipo: _servicio(tipo, sin_iva(Decimal(precio)), costo, p)}
        return tipo in optimizar_tipos(dias, servicios, p, bloqueados)

    alto = 1
    while not entra(alto):
        alto *= 2
        if alto > PRECIO_MAXIMO_BUSQUEDA:
            return None
    bajo = alto // 2   # no entra (o es 0)
    while alto - bajo > 1:
        medio = (alto + bajo) // 2
        if entra(medio):
            alto = medio
        else:
            bajo = medio
    return Decimal(alto)


def calcular_plan(anio: int, p: ParametrosNegocio, servicios_netos: Dict[str, dict], cf_anual: Decimal,
                  bloqueados=None, con_umbrales: bool = False) -> Dict:
    """
    Plan del año `anio`. `servicios_netos` es `{tipo: {'neto': precio sin IVA, 'costo': costo variable}}`
    solo para los servicios con precio; el resto no entra al cálculo.
    """
    dias = list(dias_del_rango(date(anio, 1, 1), date(anio, 12, 31)))
    bloqueados = dias_bloqueados(dias[0], dias[-1]) if bloqueados is None else bloqueados
    cf_anual = _q2(cf_anual)
    servicios = {tipo: _servicio(tipo, s['neto'], s['costo'], p) for tipo, s in servicios_netos.items()}
    asignacion = optimizar(dias, _valores(servicios), p, bloqueados)

    unidades = dict.fromkeys(TIPOS, 0)
    dias_usados = {t: set() for t in TIPOS}
    for dia, tipo in asignacion.items():
        if tipo == PREPARACION:
            continue
        unidades[tipo] += HABITACIONES if tipo == 'HOSPEDAJE' else 1
        dias_usados[tipo].add(dia.isoweekday())

    for tipo, s in servicios.items():
        n = unidades[tipo]
        asignaciones = n // HABITACIONES if tipo == 'HOSPEDAJE' else n
        s.update(unidades=n, dias=asignaciones * dias_que_ocupa(tipo, p),
                 venta=_q2(n * s['neto']), contribucion=_q2(n * s['mc']), entra=n > 0, umbral=None)

    venta = _q2(sum((s['venta'] for s in servicios.values()), D0))
    contribucion = _q2(sum((s['contribucion'] for s in servicios.values()), D0))
    costo_directo = venta - contribucion
    dias_ocupados = len(asignacion)
    disponibles = [d for d in dias if d not in bloqueados]

    if con_umbrales:
        for tipo, s in servicios.items():
            if s['entra']:
                continue
            resto = {t: v for t, v in servicios.items() if t != tipo}
            precio = precio_para_entrar(tipo, s['costo'], resto, dias, p, bloqueados)
            if precio is None:
                continue
            s['umbral'] = {'precio': precio,
                           'mc_dia': _q2(valor_por_dia(tipo, sin_iva(precio) - s['costo'], p))}

    return {
        'anio': anio, 'servicios': servicios, 'unidades': unidades, 'asignacion': asignacion,
        'frase': _frase(anio, servicios, unidades, dias_usados), 'supuestos': supuestos(p),
        'venta': venta, 'costo_directo': costo_directo, 'contribucion': contribucion,
        'cf': cf_anual, 'utilidad': contribucion - cf_anual,
        'dias_ocupados': dias_ocupados, 'dias_disponibles': len(disponibles),
        'dias_bloqueados': len(dias) - len(disponibles),
        'meses': _meses(anio, servicios, asignacion, cf_anual, disponibles),
        'escenarios': _escenarios(contribucion, cf_anual),
        'equilibrio': _equilibrio(contribucion, cf_anual, dias_ocupados),
        'vacio': not any(s['entra'] for s in servicios.values()),
    }


def _meses(anio: int, servicios: Dict[str, dict], asignacion: Dict[date, str], cf_anual: Decimal,
           disponibles: List[date]) -> List[dict]:
    """Las 12 filas del año; el costo fijo se reparte entre 12 y el último mes absorbe el redondeo."""
    por_mes = defaultdict(lambda: {'EVENTO': 0, 'PASADIA': 0, 'HOSPEDAJE': 0, 'ocupados': 0})
    for dia, tipo in asignacion.items():
        fila = por_mes[dia.month]
        fila['ocupados'] += 1
        if tipo != PREPARACION:
            fila[tipo] += HABITACIONES if tipo == 'HOSPEDAJE' else 1
    cf_mes = _q2(cf_anual / 12)
    filas, acumulado = [], D0
    for mes in range(1, 13):
        fila = por_mes[mes]
        cf = cf_anual - acumulado if mes == 12 else cf_mes
        acumulado += cf
        venta = _q2(sum((fila[t] * servicios[t]['neto'] for t in servicios), D0))
        contribucion = _q2(sum((fila[t] * servicios[t]['mc'] for t in servicios), D0))
        libres = sum(1 for d in disponibles if d.month == mes)
        filas.append({
            'mes': mes, 'etiqueta': f'{mes:02d}/{anio}', 'eventos': fila['EVENTO'], 'pasadias': fila['PASADIA'],
            'noches': fila['HOSPEDAJE'], 'venta': venta, 'contribucion': contribucion, 'cf': cf,
            'utilidad': contribucion - cf, 'dias_ocupados': fila['ocupados'], 'dias_disponibles': libres,
            'ocupacion': _q2(Decimal(fila['ocupados']) * 100 / libres) if libres else D0,
        })
    return filas


def _escenarios(contribucion: Decimal, cf_anual: Decimal) -> List[dict]:
    """La utilidad si logras 25, 50, 75 o 100 % del plan (misma mezcla, proporcionalmente menos días)."""
    return [{'pct': pct, 'contribucion': _q2(contribucion * pct / 100),
             'utilidad': _q2(contribucion * pct / 100) - cf_anual} for pct in PORCENTAJES]


def _equilibrio(contribucion: Decimal, cf_anual: Decimal, dias_ocupados: int) -> dict:
    """Días ocupados (con la mezcla del plan) para cubrir los costos fijos del año."""
    if contribucion <= 0 or not dias_ocupados:
        return {'dias': None, 'pct_plan': None, 'alcanza': False}
    por_dia = contribucion / dias_ocupados
    dias = int((cf_anual / por_dia).to_integral_value(rounding=ROUND_CEILING)) if cf_anual > 0 else 0
    return {'dias': dias, 'pct_plan': _q2(cf_anual * 100 / contribucion), 'alcanza': dias <= dias_ocupados}


# --- Entradas y servicio -------------------------------------------------------------------------

class PlanAnualService:

    @staticmethod
    def contexto(anio_referencia: int, mes_referencia: int, p: ParametrosNegocio, hoy: date,
                 economia: Optional[dict] = None, costos: Optional[dict] = None) -> Dict:
        """Economía histórica y costos fijos de los que salen los números ya llenos."""
        economia = economia or EconomiaUnitariaService.generar(
            *ventana_promedio(anio_referencia, mes_referencia, p), parametros=p)
        costos = costos or CostosFijosService.generar(corte(anio_referencia, mes_referencia, hoy), p)
        return {'economia': economia, 'costos': costos}

    @classmethod
    def valores_iniciales(cls, p: ParametrosNegocio, hoy: date, economia: Optional[dict] = None,
                          costos: Optional[dict] = None, overrides: Optional[dict] = None) -> Dict:
        """
        Los números con los que abre la página: costos fijos del año (mensual × 12) y, por servicio,
        precio con IVA y costo variable sin IVA (Parámetros, o el histórico si no hay). Un servicio sin
        precio queda fuera. `avisos` explica por qué.
        """
        ctx = cls.contexto(hoy.year, hoy.month, p, hoy, economia, costos)
        servicios, avisos = {}, list(ctx['costos']['avisos'])
        for tipo, s in economia_servicios(ctx['economia'], p, overrides).items():
            avisos.extend(s['avisos'])
            if s['ticket'] is not None:
                # El precio capturado en Parámetros se muestra tal cual (no el que resulta de ir y volver del IVA).
                capturado = p.precio_de(tipo) if s['fuente_precio'] == 'parámetros' else None
                servicios[tipo] = {'neto': s['ticket'], 'costo': s['costo'],
                                   'precio': con_iva(s['ticket']) if capturado is None else capturado}
        return {'cf_anual': _q2(ctx['costos']['cf'] * 12), 'servicios': servicios, 'avisos': list(dict.fromkeys(avisos)),
                'fuente_cf': ctx['costos']['fuente']}

    @classmethod
    def generar(cls, anio: int, p: ParametrosNegocio, servicios_netos: Optional[Dict[str, dict]] = None,
                cf_anual: Optional[Decimal] = None, hoy: Optional[date] = None, economia: Optional[dict] = None,
                costos: Optional[dict] = None, overrides: Optional[dict] = None,
                con_umbrales: bool = False, iniciales: Optional[dict] = None) -> Dict:
        """
        Plan de `anio`. Sin `servicios_netos` ni `cf_anual` usa los valores iniciales. Devuelve el plan
        de `calcular_plan` más `avisos` (de dónde salieron los números) y `fuente_cf`. `iniciales` evita recalcularlos
        si quien llama ya los tiene.
        """
        from django.utils import timezone

        hoy = hoy or timezone.localdate()
        iniciales = iniciales or cls.valores_iniciales(p, hoy, economia, costos, overrides)
        if servicios_netos is None:
            servicios_netos = iniciales['servicios']
        plan = calcular_plan(anio, p, servicios_netos, iniciales['cf_anual'] if cf_anual is None else cf_anual,
                             con_umbrales=con_umbrales)
        plan['avisos'] = iniciales['avisos']
        plan['fuente_cf'] = iniciales['fuente_cf']
        plan['iniciales'] = iniciales
        return plan

    @classmethod
    def del_mes(cls, anio: int, mes: int, p: Optional[ParametrosNegocio] = None, hoy: Optional[date] = None,
                economia: Optional[dict] = None, costos: Optional[dict] = None,
                overrides: Optional[dict] = None) -> Dict:
        """La parte de `mes` del plan de `anio` con los números iniciales: lo que leen tablero, PDF, Excel y Metas."""
        p = p or ParametrosNegocio.vigente()
        plan = cls.generar(anio, p, hoy=hoy, economia=economia, costos=costos, overrides=overrides)
        return {'plan': plan, 'fila': plan['meses'][mes - 1]}
