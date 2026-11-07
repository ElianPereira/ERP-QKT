"""Cuenta bancaria de la Quinta para transferencias directas del cliente.

Fuente única de los datos que se muestran al cliente (contratos, plan de
pagos, recordatorios, portal y Kooxi) y de la instrucción de escribir el folio
en el concepto: con el folio, el abono del estado de cuenta se identifica solo
(`contabilidad.services_abonos`). Son datos públicos del cobro, no un secreto.
"""
BANCO = 'BBVA'
TITULAR = 'Elian Pereira Ceh'
CLABE = '012910004895703146'


def folio(cotizacion) -> str:
    return f'COT-{cotizacion.pk:03d}'


def instruccion_transferencia(cotizacion) -> str:
    """«Transfiere a BBVA, CLABE …, a nombre de …; en el concepto escribe COT-145.»"""
    return (f'Transfiere a {BANCO}, CLABE {CLABE}, a nombre de {TITULAR}; '
            f'en el concepto escribe {folio(cotizacion)}.')


def datos_transferencia(cotizacion) -> dict:
    """Datos estructurados para el portal y para las herramientas de Kooxi."""
    return {
        'banco': BANCO,
        'titular': TITULAR,
        'clabe': CLABE,
        'concepto': folio(cotizacion),
        'instruccion': instruccion_transferencia(cotizacion),
    }


def filas_portal(cotizacion) -> list:
    """(etiqueta, valor) en el orden en que se capturan en la banca en línea."""
    return [('Banco', BANCO), ('CLABE interbancaria', CLABE), ('Beneficiario', TITULAR),
            ('Concepto (tu folio)', folio(cotizacion))]
