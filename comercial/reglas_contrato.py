"""Reglas de negocio de los contratos propios (Issue #318), en un solo sitio.

Decisiones del propietario del 2026-09-25. Las plantillas de
`contratos/propio/` solo muestran estos valores: si una regla cambia, se
cambia aquí y el contrato la refleja sin tocar el texto.
"""
from decimal import Decimal

from core_erp.impuestos import centavos

# Versión del modelo de contrato. Súbela cada vez que cambie el texto: queda
# impresa en el contrato para saber qué redacción firmó cada cliente.
VERSION_MODELO = '1.0'

# Depósito en garantía. Pasadía no lleva: con un precio de $2,000 a $3,000
# el depósito le mete fricción a la venta y los daños se cobran con el
# Reglamento.
PORCENTAJE_DEPOSITO_EVENTO = Decimal('0.10')
DEPOSITO_POR_HABITACION = Decimal('500.00')
DEPOSITO_POR_MASCOTA = Decimal('500.00')
DIAS_DEVOLUCION_DEPOSITO = 7  # naturales

# Registro previo de proveedores externos, en días naturales.
DIAS_REGISTRO_PROVEEDORES = 7

# Personas no declaradas: tarifa de persona extra más este recargo.
RECARGO_PERSONA_NO_DECLARADA = 50  # %

# Hospedaje: salida tardía. Tolerancia, cargo por hora como porcentaje de la
# tarifa por noche y hora a partir de la cual se cobra la noche completa
# (la habitación ya no alcanza a quedar lista para el check-in de las 14:00).
TOLERANCIA_SALIDA_MIN = 30
CARGO_HORA_SALIDA_TARDIA = 10  # % de la tarifa por noche
HORA_NOCHE_COMPLETA = '1:00 p.m.'

# Pasadía con mal clima: una reprogramación sin costo dentro de este plazo.
DIAS_REPROGRAMACION_CLIMA = 60

# Tablas de cancelación de la Política de Cancelación y Reembolso vigente
# (§2 para Evento y Pasadía, §8 para Hospedaje). Si la Política cambia, estas
# filas cambian con ella: el contrato no puede prometer algo distinto.
TABLA_CANCELACION = {
    'SERVICIO': [
        ('Más de 60 días naturales antes', '10%', '90%'),
        ('De 31 a 60 días naturales antes', '25%', '75%'),
        ('De 16 a 30 días naturales antes', '50%', '50%'),
        ('15 días naturales o menos', '100%', '0%'),
        ('Caso fortuito o fuerza mayor documentado', '0%', '100%'),
    ],
    'HOSPEDAJE': [
        ('Más de 15 días naturales antes de la llegada', '0%', '100%'),
        ('De 7 a 15 días naturales antes de la llegada', '50%', '50%'),
        ('Menos de 7 días naturales o no presentación', '100%', '0%'),
        ('Caso fortuito o fuerza mayor documentado', '0%', '100%'),
    ],
}


def habitaciones_contratadas(cotizacion):
    """Habitaciones distintas de la cotización. La cantidad del renglón son
    noches, no habitaciones, así que se cuentan productos, no cantidades."""
    return (
        cotizacion.items
        .filter(producto__rol_cotizador='HABITACION_HOSPEDAJE')
        .values('producto').distinct().count()
    )


def deposito_sugerido(cotizacion):
    """Depósito en garantía que corresponde según el tipo de servicio.

    No incluye el recargo por mascota: el ERP todavía no registra si el
    huésped la trae, así que el contrato lo menciona como condición.
    """
    if cotizacion.tipo_servicio == 'EVENTO':
        return centavos((cotizacion.precio_final or Decimal('0')) * PORCENTAJE_DEPOSITO_EVENTO)
    if cotizacion.tipo_servicio == 'HOSPEDAJE':
        return DEPOSITO_POR_HABITACION * max(habitaciones_contratadas(cotizacion), 1)
    return Decimal('0.00')


# Días naturales mínimos de cada fila de TABLA_CANCELACION (misma posición); la
# última fila (caso fortuito) no es un tramo de fechas. Solo los umbrales viven
# aquí: los porcentajes se leen de la tabla, nunca se repiten.
_DESDE_DIAS = {'SERVICIO': (61, 31, 16, 0), 'HOSPEDAJE': (16, 7, 0)}


def _porcentaje(texto):
    return Decimal(texto.rstrip('%'))


def condicion_cancelacion(tipo_servicio, dias, fuerza_mayor=False):
    """Fila aplicable de `TABLA_CANCELACION` → (tramo, % de penalización, % de reembolso).

    `dias` son los días naturales entre la solicitud y la fecha del evento (o la
    llegada, en Hospedaje). Para Evento y Pasadía la penalización se calcula
    sobre el valor total del servicio; en Hospedaje el reembolso es sobre lo
    pagado (Política §2 y §8).
    """
    clave = 'HOSPEDAJE' if tipo_servicio == 'HOSPEDAJE' else 'SERVICIO'
    filas = TABLA_CANCELACION[clave]
    if fuerza_mayor:
        fila = filas[-1]
    else:
        fila = next(f for f, desde in zip(filas, _DESDE_DIAS[clave]) if dias >= desde)
    return fila[0], _porcentaje(fila[1]), _porcentaje(fila[2])
