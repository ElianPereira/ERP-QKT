"""Reglas de negocio del cotizador de Eventos, en un solo sitio.

Las comparten modelos, servicios, vistas, formularios y tests: si el aforo
máximo o el paso del slider vivieran duplicados en el JS y en el backend,
el frontend podría ofrecer una combinación que el servidor luego rechaza
(o peor, que acepta sin querer).
"""

from decimal import ROUND_CEILING, Decimal

# Aforo. Tope duro en AMBAS modalidades — la Quinta no opera eventos de más
# de 100 personas, no hay ruta alterna ni excepción por autorización.
MAX_PERSONAS_EVENTO = 100

# La modalidad de paquete arranca en 50 y avanza de 10 en 10: los tiers de
# mobiliario/taquiza se cotizan por tramos, no persona a persona.
MIN_PERSONAS_PAQUETE = 50
PASO_PERSONAS_PAQUETE = 10

MODALIDAD_ARRENDAMIENTO = 'ARRENDAMIENTO'
MODALIDAD_PAQUETE = 'PAQUETE'
MODALIDAD_CHOICES = [
    (MODALIDAD_ARRENDAMIENTO, 'Solo arrendamiento del espacio'),
    (MODALIDAD_PAQUETE, 'Paquete'),
]


def personas_validas_paquete():
    """Los únicos aforos cotizables en modalidad de paquete: 50, 60, … 100."""
    return list(range(MIN_PERSONAS_PAQUETE, MAX_PERSONAS_EVENTO + 1, PASO_PERSONAS_PAQUETE))


def redondear_personas_paquete(n):
    """Sube al siguiente múltiplo de 10 cotizable, sin pasar del tope.

    El cliente escribe 57 y el paquete se cotiza para 60: cobrar por 57 daría
    un tramo de mobiliario que no existe. El redondeo es hacia ARRIBA a
    propósito — hacia abajo dejaría invitados sin silla.
    """
    n = int(n)
    if n <= MIN_PERSONAS_PAQUETE:
        return MIN_PERSONAS_PAQUETE
    escalones = -(-(n - MIN_PERSONAS_PAQUETE) // PASO_PERSONAS_PAQUETE)
    return min(MIN_PERSONAS_PAQUETE + escalones * PASO_PERSONAS_PAQUETE, MAX_PERSONAS_EVENTO)


def resolver_cantidad(*, cantidad_por_persona, cantidad_fija, num_personas):
    """Unidades a cobrar de una asignación, dado el aforo.

    Redondea hacia ARRIBA (no ROUND_HALF_UP): es una cantidad física, no un
    importe. Media silla o medio taco no existen, y quedarse corto en el
    servicio es peor que servir de más. El redondeo monetario sigue viviendo
    entero en `core_erp.impuestos`, sobre el importe ya resuelto.
    """
    if cantidad_por_persona is not None:
        bruta = Decimal(cantidad_por_persona) * Decimal(int(num_personas))
        return bruta.to_integral_value(rounding=ROUND_CEILING)
    return Decimal(cantidad_fija or 0)
