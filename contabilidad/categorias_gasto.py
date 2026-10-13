"""
Catálogo de categorías de gasto (Issue #390): fuente única de qué categoría
existe, a qué grupo del Estado de resultados pertenece y qué operación de
`ConfiguracionContable` le da su cuenta. Una categoría = una cuenta; nada cae
en otra cuenta "por parecido".

Decisiones del propietario y el contador (2026-10-12):
- Comisiones bancarias y de cobro: gasto financiero (602.03), no administración.
- Staff externo de eventos: costo del evento (501.03). Lo que se renta o
  subcontrata para revenderse en un evento, también costo (501.05/501.06).
- Combustible de la planta de luz: energía eléctrica; el de vehículos: vehículos.
- Mobiliario y equipo: activo fijo (115.03), no gasto.
- No deducibles (compra sin CFDI): subcuenta no deducible del grupo
  (`NO_DEDUCIBLE_POR_GRUPO`), no una sola bolsa ni «Otros».

Sin imports de modelos: `comercial.models` toma de aquí las choices.
"""

COSTO = 'COSTO'
OPERACION = 'OPERACION'
ADMINISTRACION = 'ADMINISTRACION'
VENTA = 'VENTA'
FINANCIERO = 'FINANCIERO'
ACTIVO = 'ACTIVO'
OTROS = 'OTROS'
POR_CLASIFICAR = 'POR_CLASIFICAR'

# (clave, etiqueta, grupo, operación de ConfiguracionContable)
CATALOGO = [
    ('SIN_CLASIFICAR', 'Sin Clasificar', POR_CLASIFICAR, 'GASTOS_GENERALES'),
    # Costo del evento
    ('ALIMENTOS', 'Alimentos', COSTO, 'COSTO_ALIMENTOS'),
    ('BEBIDAS_SIN_ALCOHOL', 'Bebidas Sin Alcohol', COSTO, 'GASTOS_BEBIDAS'),
    ('BEBIDAS_CON_ALCOHOL', 'Bebidas Con Alcohol', COSTO, 'GASTOS_BEBIDAS'),
    ('INSUMOS', 'Insumos para eventos', COSTO, 'GASTO_INSUMOS'),
    ('NOMINA_EXT', 'Staff externo de eventos', COSTO, 'GASTOS_NOMINA_EXT'),
    ('RENTA_EVENTOS', 'Renta de mobiliario y equipo para eventos', COSTO, 'COSTO_RENTA_EVENTOS'),
    ('SERVICIOS_EVENTOS', 'Servicios subcontratados para eventos', COSTO, 'COSTO_SERVICIOS_EVENTOS'),
    # Gastos de operación
    ('LUZ', 'Energía eléctrica (incluye combustible de la planta)', OPERACION, 'GASTO_LUZ'),
    ('AGUA', 'Agua', OPERACION, 'GASTO_AGUA'),
    ('GAS', 'Gas', OPERACION, 'GASTO_GAS'),
    ('TELEFONO_INTERNET', 'Teléfono e internet', OPERACION, 'GASTO_TELEFONO'),
    ('MANTENIMIENTO', 'Mantenimiento Y Reparaciones', OPERACION, 'GASTO_MANTENIMIENTO'),
    ('JARDINERIA', 'Jardinería', OPERACION, 'GASTO_JARDINERIA'),
    ('LIMPIEZA', 'Limpieza Y Desechables', OPERACION, 'GASTO_LIMPIEZA'),
    ('SEGURIDAD', 'Vigilancia y sistemas de seguridad', OPERACION, 'GASTO_SEGURIDAD'),
    ('SEGUROS', 'Seguros y fianzas', OPERACION, 'GASTO_SEGUROS'),
    ('VEHICULOS', 'Vehículos (combustible, mantenimiento y refacciones)', OPERACION, 'GASTO_VEHICULOS'),
    # Gastos de administración
    ('PAPELERIA', 'Papelería y oficina', ADMINISTRACION, 'GASTO_OFICINA'),
    ('HONORARIOS', 'Honorarios profesionales', ADMINISTRACION, 'GASTO_HONORARIOS'),
    ('CONTABILIDAD', 'Servicios contables', ADMINISTRACION, 'GASTO_CONTABLES'),
    ('SOFTWARE', 'Software y suscripciones', ADMINISTRACION, 'GASTO_SOFTWARE'),
    ('IMPUESTOS', 'Impuestos locales y derechos (predial, licencias)', ADMINISTRACION, 'GASTO_IMPUESTOS'),
    # Gastos de venta
    ('PUBLICIDAD', 'Publicidad Y Marketing', VENTA, 'GASTO_PUBLICIDAD'),
    ('FOTOGRAFIA', 'Fotografía y video', VENTA, 'GASTO_FOTOGRAFIA'),
    # Gastos financieros
    ('COMISIONES_BANCARIAS', 'Comisiones bancarias y de cobro', FINANCIERO, 'GASTO_BANCARIOS'),
    # Activo fijo
    ('MOBILIARIO_EQ', 'Mobiliario Y Equipo (activo)', ACTIVO, 'ACTIVO_MOBILIARIO'),
    # Otros
    ('OTRO', 'Otros Gastos', OTROS, 'GASTO_OTROS'),
]

CATEGORIAS_GASTO = [(clave, etiqueta) for clave, etiqueta, _, _ in CATALOGO]
GRUPO_POR_CATEGORIA = {clave: grupo for clave, _, grupo, _ in CATALOGO}
OPERACION_POR_CATEGORIA = {clave: operacion for clave, _, _, operacion in CATALOGO}

# Compra sin CFDI: la subcuenta no deducible de su grupo. Sin entrada (activo
# fijo, por clasificar) se queda en la cuenta de su categoría.
NO_DEDUCIBLE_POR_GRUPO = {
    COSTO: 'NO_DEDUCIBLE_COSTO',
    OPERACION: 'NO_DEDUCIBLE_OPERACION',
    ADMINISTRACION: 'GASTO_NO_DEDUCIBLE',
    VENTA: 'NO_DEDUCIBLE_VENTA',
    FINANCIERO: 'NO_DEDUCIBLE_FINANCIERO',
    OTROS: 'NO_DEDUCIBLE_OTROS',
}
