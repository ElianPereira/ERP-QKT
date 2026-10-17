"""
Utilidades comunes de los services de Indicadores (Issue #402)
==============================================================
Definiciones que comparten todos los cálculos (documentadas aquí una vez):

- **Venta neta** = `subtotal - descuento` de la cotización (sin IVA ni ISH), solo
  ventas reales (`ESTADOS_VENTA_REAL`) y sin cortesías (`precio_final == 0`).
- **Precio especial**: una venta con un descuento activo marcado `es_cortesia` (por ejemplo,
  un evento cobrado a una asociación) cuenta como venta y como día ocupado, pero no entra al
  precio típico ni al costo histórico (ver `unitarios.py`).
- **Día-quinta**: la Quinta es un solo recurso por día; Evento, Pasadía y
  Hospedaje no conviven el mismo día. Todo se mide en contribución por día.
- **Unidad de hospedaje** = una habitación, una noche. Con `HABITACIONES` habitaciones, una
  unidad ocupa `1 / HABITACIONES` de día-quinta y los días que consume un grupo de unidades se
  redondean hacia arriba (`consumo_dias`).
- Todo es `Decimal` con `ROUND_HALF_UP`; nada pasa por `float`.
"""
from calendar import monthrange
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Iterator, Set

from reportes.services.operacion import _porcentaje

D0 = Decimal('0')
CENTAVO = Decimal('0.01')

TIPOS = ('EVENTO', 'PASADIA', 'HOSPEDAJE')
ETIQUETAS = {'EVENTO': 'Evento', 'PASADIA': 'Pasadía', 'HOSPEDAJE': 'Hospedaje'}
MIN_MUESTRA = 5          # ventas con gasto ligado para confiar en el costo medido
HABITACIONES = 2         # Ka'an y Otoch
ESTANCIA_DEFAULT = Decimal('2')   # noches por reservación si aún no hay ventas de hospedaje
UMBRAL_GASTO_LIGADO = Decimal('70')

porcentaje = _porcentaje


def q2(valor) -> Decimal:
    return Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def q4(valor) -> Decimal:
    return Decimal(valor).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)


def consumo_dias(unidades, dias_unidad) -> Decimal:
    """Días-quinta enteros que ocupan `unidades` de un servicio (3 habitaciones-noche = 2 días)."""
    return (Decimal(unidades) * Decimal(dias_unidad)).to_integral_value(rounding=ROUND_CEILING)


def dividir(numerador, denominador, *, cuantizar=q2):
    """Cociente cuantizado, o `None` si el denominador es cero o negativo (nunca `ZeroDivisionError`)."""
    if denominador is None or numerador is None or Decimal(denominador) <= 0:
        return None
    return cuantizar(Decimal(numerador) / Decimal(denominador))


def mes_rango(anio: int, mes: int):
    """(primer día, último día) del mes."""
    return date(anio, mes, 1), date(anio, mes, monthrange(anio, mes)[1])


def mes_anterior(anio: int, mes: int):
    return (anio - 1, 12) if mes == 1 else (anio, mes - 1)


def ventana_promedio(anio: int, mes: int, parametros):
    """Los últimos `meses_promedio` meses hasta el fin de (anio, mes), sin pasar de `inicio_datos`."""
    _, hasta = mes_rango(anio, mes)
    a, m = anio, mes
    for _ in range(parametros.meses_promedio - 1):
        a, m = mes_anterior(a, m)
    desde = max(date(a, m, 1), parametros.inicio_datos)
    return desde, hasta


def corte(anio: int, mes: int, hoy: date) -> date:
    """Fecha hasta la que se consideran cerrados los meses: el fin del mes o hoy, lo que ocurra antes."""
    return min(mes_rango(anio, mes)[1], hoy)


def dias_del_rango(desde: date, hasta: date) -> Iterator[date]:
    """Cada día entre `desde` y `hasta`, ambos incluidos."""
    for i in range((hasta - desde).days + 1):
        yield desde + timedelta(days=i)


def dias_bloqueados(desde: date, hasta: date) -> Set[date]:
    """Días de [desde, hasta] cerrados por un `BloqueoFecha` activo."""
    from comercial.disponibilidad import bloqueos_activos

    cerrados = set()
    for bloqueo in bloqueos_activos(desde, hasta + timedelta(days=1)):
        inicio, fin = bloqueo.rango_ocupado()
        cerrados.update(d for d in dias_del_rango(inicio, fin - timedelta(days=1)) if desde <= d <= hasta)
    return cerrados


def ventas_reales(desde: date, hasta: date):
    """Cotizaciones con venta real cuyo día de servicio cae en [desde, hasta], sin cortesías."""
    from comercial.models import Cotizacion
    from comercial.views import ESTADOS_VENTA_REAL

    return Cotizacion.objects.filter(
        estado__in=ESTADOS_VENTA_REAL, fecha_evento__gte=desde, fecha_evento__lte=hasta,
    ).exclude(precio_final=0)


def venta_neta(cotizacion) -> Decimal:
    return max(cotizacion.subtotal - cotizacion.descuento, D0)
