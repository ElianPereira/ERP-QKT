"""
Fuente única de verdad para el cálculo de impuestos.

Toda conversión base <-> total pasa por aquí. Fuera de este módulo (y de sus
tests) no debe existir ninguna aparición literal de 0.16, 1.16, 0.0125 ni
1.1475 — ver la verificación de la invariante I2 en el brief:

    grep -rn "1\\.16\\|0\\.16\\|1\\.1475" --include="*.py" --include="*.html" .

Reglas que este módulo garantiza:

- `Decimal` en todo. Un `float` en la frontera lanza `TypeError` en vez de
  degradar la precisión en silencio.
- `ROUND_HALF_UP` en toda salida monetaria, nunca el `ROUND_HALF_EVEN` que
  el módulo `decimal` usa por defecto y que Django aplica al guardar un
  `DecimalField`.
- La conversión se hace una sola vez sobre el importe agregado, nunca por
  línea: convertir cada línea y sumarlas produce un total distinto (ver
  `total_desde_bases`).

Marco fiscal
------------
IVA: 16% (tasa general, art. 1 LIVA).

Retención de ISR del 1.25%: art. 113-J LISR, aplicable cuando el prestador
tributa en RESICO y el receptor es persona moral. Corresponde al RFC
PECE010202IA0.

El RFC CERU580518QZ5 tributa bajo actividades empresariales a través de
plataformas tecnológicas. Ese esquema —el de los ingresos de Airbnb— sí está
implementado, en `retenciones_plataforma()`, pero solo como referencia para
detectar descuadres: las retenciones que valen fiscalmente son las que la
plataforma efectivamente aplicó y reporta en su constancia, no las que
calculemos aquí.

El factor 1.1475 que se usaba en `facturacion` era `1 + 0.16 - 0.0125`, es
decir el divisor para obtener la base a partir de un total que ya trae IVA
trasladado y retención de ISR aplicada. Se conserva la fórmula, expresada a
partir de las tasas en vez de como constante mágica.
"""

from decimal import Decimal, ROUND_HALF_UP

# --- Tasas -----------------------------------------------------------------

TASA_IVA = Decimal('0.16')

# Art. 113-J LISR (RESICO). Solo aplica cuando el receptor es persona moral.
TASA_RET_ISR_RESICO = Decimal('0.0125')

# --- Plataformas tecnológicas (Airbnb) -------------------------------------
#
# Art. 113-A LISR: la plataforma retiene ISR sobre el ingreso efectivamente
# cobrado. La tasa depende de si el anfitrión le dio su RFC:
#   con RFC  -> 4%   (servicios de hospedaje)
#   sin RFC  -> 20%
#
# Art. 18-J LIVA: la plataforma retiene el 50% del IVA trasladado si hay RFC,
# y el 100% si no. El 50% de la tasa del 16% es el "8%" con el que se suele
# resumir la regla, y se aplica sobre la BASE.
#
# Cuidado con la columna "Ingresos brutos" del CSV de Airbnb: pese al nombre,
# NO incluye el IVA — es la base. El IVA viaja aparte, en las filas
# "Impuestos liquidados como anfitrión", y Airbnb lo transfiere al anfitrión
# para que sea él quien lo entere.
TASA_RET_ISR_PLATAFORMA_CON_RFC = Decimal('0.04')
TASA_RET_ISR_PLATAFORMA_SIN_RFC = Decimal('0.20')
PROPORCION_IVA_RETENIDO_CON_RFC = Decimal('0.50')
PROPORCION_IVA_RETENIDO_SIN_RFC = Decimal('1.00')

CENTAVO = Decimal('0.01')


class ImporteInvalido(TypeError):
    """Se intentó operar con un tipo que no preserva la precisión decimal."""


class DesgloseIrreconciliable(ValueError):
    """
    No existe un desglose que satisfaga simultáneamente
    `base + iva - retenciones == total` y `iva == redondeo(base * tasa)`.

    Se lanza en vez de devolver un desglose inválido en silencio: un CFDI con
    esa desviación sería rechazado por el PAC (tolerancia de ±0.01 al validar
    el Importe del traslado contra Base × TasaOCuota).
    """


# --- Primitivas ------------------------------------------------------------

def _exigir_decimal(valor, nombre='importe') -> Decimal:
    """Acepta Decimal e int; rechaza float y str numérico."""
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise ImporteInvalido(
            f"{nombre} debe ser Decimal (o int), no {type(valor).__name__}. "
            "Los float no preservan la precisión de importes monetarios."
        )
    return Decimal(valor)


def centavos(valor) -> Decimal:
    """Redondea a 2 decimales con ROUND_HALF_UP. Única salida monetaria válida."""
    return _exigir_decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)


# --- Conversiones ----------------------------------------------------------

def iva_de(base) -> Decimal:
    """IVA correspondiente a una base ya redondeada."""
    return centavos(_exigir_decimal(base, 'base') * TASA_IVA)


def con_iva(base) -> Decimal:
    """Base -> total con IVA incluido. Es el precio que se exhibe al consumidor."""
    base = _exigir_decimal(base, 'base')
    return centavos(base + iva_de(base))


def sin_iva(total) -> Decimal:
    """Total con IVA incluido -> base. Inversa de `con_iva`."""
    total = _exigir_decimal(total, 'total')
    return centavos(total / (Decimal('1') + TASA_IVA))


def ret_isr_de(base) -> Decimal:
    """Retención de ISR (RESICO) sobre una base. Solo aplica a persona moral."""
    return centavos(_exigir_decimal(base, 'base') * TASA_RET_ISR_RESICO)


def total_desde_bases(bases) -> Decimal:
    """
    Total con IVA a partir de varias bases: suma primero, convierte una sola vez.

    Convertir cada línea y sumar produce un resultado distinto. Con tres líneas
    de 100.05:

        por línea : con_iva(100.05) * 3      -> 348.18
        agregado  : con_iva(300.15)          -> 348.17   <- el que se cobra

    Usar siempre esta función para totales de varias líneas, para que el número
    exhibido coincida con el que produce `Cotizacion.calcular_totales()`.
    """
    suma = sum((_exigir_decimal(b, 'base') for b in bases), Decimal('0'))
    return con_iva(suma)


# --- Desglose --------------------------------------------------------------

def desglosar(total, *, con_retencion_isr: bool = False) -> dict:
    """
    Desglosa un total cobrado en base, IVA y retenciones.

    Garantiza simultáneamente:
        base + iva - ret_isr == total
        iva == centavos(base * TASA_IVA)

    Para lograrlo ajusta la BASE, nunca el IVA: el PAC valida el Importe del
    traslado contra Base × TasaOCuota, así que el IVA debe ser exactamente el
    que resulta de la base declarada.

    Args:
        total: importe efectivamente cobrado (Decimal).
        con_retencion_isr: True si el receptor es persona moral (art. 113-J LISR).

    Raises:
        DesgloseIrreconciliable: si no se logra cuadrar dentro de un centavo.
    """
    total = centavos(_exigir_decimal(total, 'total'))

    if total == 0:
        return {'base': Decimal('0.00'), 'iva': Decimal('0.00'),
                'ret_isr': Decimal('0.00'), 'total': Decimal('0.00')}

    divisor = Decimal('1') + TASA_IVA
    if con_retencion_isr:
        divisor -= TASA_RET_ISR_RESICO

    estimada = centavos(total / divisor)
    pasos = (0, 1, -1, 2, -2, 3, -3)

    # Primera pasada: base cuyo IVA teórico cuadra el total EXACTAMENTE.
    for paso in pasos:
        base = centavos(estimada + CENTAVO * paso)
        if base < 0:
            continue
        iva = iva_de(base)
        ret = ret_isr_de(base) if con_retencion_isr else Decimal('0.00')
        if base + iva - ret == total:
            return {'base': base, 'iva': iva, 'ret_isr': ret, 'total': total}

    # Segunda pasada: no siempre existe esa base. La función base -> total
    # avanza de 0.01 en 0.01 o de 0.02 en 0.02, así que alrededor del 14% de
    # los totales posibles no es imagen de ninguna base de dos decimales
    # (ej. 0.04: base 0.03 da 0.03 y base 0.04 da 0.05). Como un pago parcial
    # puede ser de cualquier importe, fallar en esos casos rompería uno de cada
    # siete pagos.
    #
    # En su lugar se deja que el IVA absorba un centavo. El SAT valida el
    # Importe del traslado contra Base x TasaOCuota con tolerancia de +/-0.01,
    # de modo que el desglose sigue siendo timbrable, y el total cuadra al
    # centavo, que es lo que el cliente ve cobrado.
    for paso in pasos:
        base = centavos(estimada + CENTAVO * paso)
        if base < 0:
            continue
        ret = ret_isr_de(base) if con_retencion_isr else Decimal('0.00')
        iva = centavos(total - base + ret)
        if iva < 0:
            continue
        if abs(iva - iva_de(base)) <= CENTAVO:
            return {'base': base, 'iva': iva, 'ret_isr': ret, 'total': total}

    raise DesgloseIrreconciliable(
        f"No existe un desglose válido para {total} "
        f"(con_retencion_isr={con_retencion_isr}). Base estimada {estimada}; "
        f"ninguna base cercana cuadra el total con un IVA dentro de la "
        f"tolerancia de {CENTAVO} que admite el SAT."
    )


def retenciones_plataforma(base, *, con_rfc: bool = True) -> dict:
    """
    Retenciones que una plataforma tecnológica (Airbnb) *debería* aplicar
    sobre un ingreso por hospedaje, según arts. 113-A LISR y 18-J LIVA.

    IMPORTANTE — esto es una referencia, no la verdad fiscal. Lo que se
    declara son las retenciones que la plataforma efectivamente aplicó y que
    constan en su constancia; este cálculo sirve para *detectar descuadres*
    contra ese dato, no para sustituirlo. Airbnb puede no retener en una
    reserva (huésped exento, ajuste, reserva cancelada y reexpedida), y en
    ese caso inventarle una retención al registro produce una declaración
    que no cuadra con la constancia.

    `base` es el ingreso SIN IVA — la columna "Ingresos brutos" del CSV de
    Airbnb, que pese al nombre no incluye el impuesto. Verificado contra el
    reporte real de marzo de 2026: en las tres reservas el IVA trasladado es
    exactamente el 16% de esa columna, el ISR el 4% y el IVA retenido la
    mitad del trasladado, al centavo.
    """
    base = centavos(_exigir_decimal(base, 'base'))

    tasa_isr = (TASA_RET_ISR_PLATAFORMA_CON_RFC if con_rfc
                else TASA_RET_ISR_PLATAFORMA_SIN_RFC)
    proporcion_iva = (PROPORCION_IVA_RETENIDO_CON_RFC if con_rfc
                      else PROPORCION_IVA_RETENIDO_SIN_RFC)

    iva_trasladado = iva_de(base)

    return {
        'base': base,
        'iva_trasladado': iva_trasladado,
        'ret_isr': centavos(base * tasa_isr),
        'ret_iva': centavos(iva_trasladado * proporcion_iva),
    }
