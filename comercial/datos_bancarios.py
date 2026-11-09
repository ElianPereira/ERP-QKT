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
    """La transferencia directa es la opción alterna: el portal (Openpay) refleja el pago al momento,
    la transferencia solo cuando llega el estado de cuenta. El texto lo dice en ese orden."""
    return (f'Lo más rápido es pagar desde tu portal (tarjeta, SPEI o efectivo en tienda): tu pago se refleja '
            f'al momento. Si prefieres transferir directo desde tu banco: {BANCO}, CLABE {CLABE}, a nombre de '
            f'{TITULAR}; escribe {folio(cotizacion)} en el concepto. Ese pago se refleja cuando lo recibimos en el banco.')


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
