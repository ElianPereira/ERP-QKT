"""
Capacidad máxima (Issue #402)
=============================
La Quinta es **un solo recurso por día**: Hospedaje bloquea la quinta completa y
Evento/Pasadía/Hospedaje no conviven el mismo día (`comercial/disponibilidad.py`).
Por eso la capacidad de cada servicio se calcula *como si solo vendiera ese
servicio* y **las capacidades no se suman**: comparten los mismos días.
(Si algún día se permite vender dos servicios el mismo día, este service y
`MezclaOptimaService` cambian de modelo.)

- **Días vendibles de un servicio** = días del periodo, sin `BloqueoFecha`
  activo, en los días de la semana que `ParametrosNegocio` permite para él.
- **Eventos** = por semana ISO, mínimo entre `max_eventos_semana`, los días
  permitidos de la semana y los días libres de la semana entre `1 + dias_preparacion_evento`.
- **Pasadías** = días vendibles. **Hospedaje** = habitaciones-noche (noches vendibles × 2
  habitaciones): supone que, si hay demanda, las dos se rentan la misma noche.
- Precio, costo y margen salen de `servicio.economia_servicio`, igual que en los demás paneles.
"""
from collections import defaultdict
from datetime import date
from typing import Dict, Optional

from ..models import ParametrosNegocio
from .base import (
    ETIQUETAS,
    HABITACIONES,
    TIPOS,
    dias_bloqueados,
    dias_del_rango,
    mes_rango,
    q2,
    ventana_promedio,
)
from .servicio import economia_servicios
from .unitarios import EconomiaUnitariaService

NOTA_NO_SUMA = ('Las capacidades no se suman: Evento, Pasadía y Hospedaje comparten los mismos días '
                'de la Quinta. Cada una supone que solo vendes ese servicio.')


def dias_vendibles_por_tipo(desde: date, hasta: date, p: ParametrosNegocio, bloqueados=None) -> Dict[str, list]:
    bloqueados = dias_bloqueados(desde, hasta) if bloqueados is None else bloqueados
    return {
        tipo: [d for d in dias_del_rango(desde, hasta)
               if d not in bloqueados and d.isoweekday() in p.dias_de(tipo)]
        for tipo in TIPOS
    }


def eventos_maximos(desde: date, hasta: date, p: ParametrosNegocio, bloqueados=None) -> int:
    bloqueados = dias_bloqueados(desde, hasta) if bloqueados is None else bloqueados
    permitidos = defaultdict(int)
    libres = defaultdict(int)
    for d in dias_del_rango(desde, hasta):
        semana = d.isocalendar()[:2]
        if d not in bloqueados:
            libres[semana] += 1
            if d.isoweekday() in p.dias_de('EVENTO'):
                permitidos[semana] += 1
    return sum(
        min(p.max_eventos_semana, permitidos[s], libres[s] // (1 + p.dias_preparacion_evento))
        for s in permitidos)


class CapacidadService:

    @classmethod
    def generar(cls, desde: date, hasta: date, parametros: Optional[ParametrosNegocio] = None,
                economia: Optional[dict] = None, overrides: Optional[dict] = None) -> Dict:
        p = parametros or ParametrosNegocio.vigente()
        if economia is None:
            economia = EconomiaUnitariaService.generar(*ventana_promedio(hasta.year, hasta.month, p), parametros=p)
        unidad = economia_servicios(economia, p, overrides)
        bloqueados = dias_bloqueados(desde, hasta)
        vendibles = dias_vendibles_por_tipo(desde, hasta, p, bloqueados)
        unicos = {d for dias in vendibles.values() for d in dias}
        servicios, avisos = {}, list(economia['avisos'])
        for tipo in TIPOS:
            u = unidad[tipo]
            avisos.extend(u['avisos'])
            dias = len(vendibles[tipo])
            if tipo == 'EVENTO':
                unidades = eventos_maximos(desde, hasta, p, bloqueados)
            elif tipo == 'PASADIA':
                unidades = dias
            else:
                unidades = dias * HABITACIONES
            servicios[tipo] = {
                'tipo': tipo, 'nombre': ETIQUETAS[tipo], 'dias_vendibles': dias, 'unidades': unidades, 'ticket': u['ticket'],
                'ingreso_max': q2(unidades * u['ticket']) if u['ticket'] is not None else None,
                'mc_max': q2(unidades * u['mc']) if u['mc'] is not None else None,
                'fuente_precio': u['fuente_precio'], 'fuente_costo': u['fuente_costo'],
                'noches_habitacion': unidades if tipo == 'HOSPEDAJE' else None,
            }
        return {
            'desde': desde, 'hasta': hasta, 'dias_periodo': (hasta - desde).days + 1,
            'dias_bloqueados': len(bloqueados), 'dias_vendibles': len(unicos),
            'servicios': servicios, 'nota': NOTA_NO_SUMA, 'avisos': avisos,
        }


def capacidad_del_mes(anio: int, mes: int, parametros=None, economia=None, overrides=None) -> Dict:
    return CapacidadService.generar(*mes_rango(anio, mes), parametros=parametros, economia=economia,
                                    overrides=overrides)


