"""Catálogo sugerido de trámites (Issue #403): editable, sin fechas ni costos."""

CATALOGO_SUGERIDO = [
    {
        'nombre': 'Constancia de extintores', 'orden': 1, 'tipo': 'CONSTANCIA', 'emisor': 'Proveedor de recarga de extintores',
        'periodicidad_meses': 12, 'dias_aviso': 60, 'critico': False,
        'requisitos': ['Comprobante de recarga o mantenimiento', 'Inventario de extintores con ubicación'],
    },
    {
        'nombre': 'Constancia de Protección Civil', 'orden': 2, 'requiere': 'Constancia de extintores', 'tipo': 'CONSTANCIA', 'emisor': 'Protección Civil Yucatán',
        'periodicidad_meses': 12, 'dias_aviso': 60, 'critico': True,
        'requisitos': ['Programa interno de protección civil', 'Dictamen eléctrico', 'Dictamen de gas',
                       'Constancia de extintores vigente', 'Comprobante de domicilio'],
    },
    {
        'nombre': 'Licencia de uso de suelo', 'orden': 3, 'requiere': 'Constancia de Protección Civil', 'tipo': 'LICENCIA', 'emisor': 'Ayuntamiento de Umán',
        'periodicidad_meses': 12, 'dias_aviso': 90, 'critico': True,
        'requisitos': ['Identificación oficial del representante', 'Escritura o contrato de arrendamiento',
                       'Comprobante de domicilio', 'Pago de predial al corriente'],
    },
    {
        'nombre': 'Licencia de funcionamiento', 'orden': 4, 'requiere': 'Licencia de uso de suelo', 'tipo': 'LICENCIA', 'emisor': 'Ayuntamiento de Umán',
        'periodicidad_meses': 12, 'dias_aviso': 60, 'critico': True,
        'requisitos': ['Licencia de uso de suelo vigente', 'Constancia de Protección Civil vigente',
                       'Alta en Hacienda (constancia de situación fiscal)', 'Identificación oficial del representante'],
    },
]
