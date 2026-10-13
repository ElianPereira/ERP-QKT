# Catálogo de categorías de gasto (Issue #390, decisiones del propietario y el
# contador): una operación de ConfiguracionContable por categoría, cada una con
# su propia cuenta. Antes, varias categorías caían en 601.02.05 «Mantenimiento y
# reparaciones» (GASTOS_GENERALES) por no tener cuenta configurada.
#
# - Comisiones bancarias y de cobro → 602.03 (gasto financiero, nueva).
# - Staff externo de eventos → 501.03; renta y servicios subcontratados para
#   eventos → 501.05 / 501.06 (costo del evento, nuevas).
# - Mobiliario y equipo → 115.03; construcciones y mejoras al inmueble → 115.01
#   (activo fijo).
# - Sin clasificar → 601.99 «Gastos por clasificar» (nueva, cuenta transitoria:
#   saldo ≠ 0 = compras pendientes de clasificar).
# - No deducibles: 601.05 pasa a ser cuenta de grupo con una subcuenta por
#   grupo del Estado de resultados, más 501.90 (costo) y 602.90 (financiero).
#
# Sobrescribe la cuenta de las operaciones del catálogo aunque ya estuvieran
# configuradas: el punto es que cada categoría tenga la suya. Lo ya asentado
# no se mueve aquí; eso es `manage.py reclasificar_compras` (simula por defecto).

from django.db import migrations, models

# (código, nombre, tipo, naturaleza, padre, permite_movimientos)
CUENTAS = [
    ('602.03', 'Comisiones bancarias y de cobro', 'GASTO', 'D', '602', True),
    ('602.90', 'Gastos financieros no deducibles', 'GASTO', 'D', '602', True),
    ('601.06', 'Otros gastos', 'GASTO', 'D', '601', True),
    ('601.99', 'Gastos por clasificar', 'GASTO', 'D', '601', True),
    ('501.05', 'Renta de mobiliario y equipo para eventos', 'COSTO', 'D', '501', True),
    ('501.06', 'Servicios subcontratados para eventos', 'COSTO', 'D', '501', True),
    ('501.90', 'Costos no deducibles', 'COSTO', 'D', '501', True),
    ('601.05.01', 'No deducibles - Gastos de operación', 'GASTO', 'D', '601.05', True),
    ('601.05.02', 'No deducibles - Gastos de administración', 'GASTO', 'D', '601.05', True),
    ('601.05.03', 'No deducibles - Gastos de venta', 'GASTO', 'D', '601.05', True),
    ('601.05.04', 'No deducibles - Otros gastos', 'GASTO', 'D', '601.05', True),
]

# (operación, código de cuenta)
CONFIGURACION = [
    ('GASTOS_GENERALES', '601.99'),
    ('COSTO_ALIMENTOS', '501.01'),
    ('GASTOS_BEBIDAS', '501.02'),
    ('GASTO_INSUMOS', '501.04'),
    ('GASTOS_NOMINA_EXT', '501.03'),
    ('COSTO_RENTA_EVENTOS', '501.05'),
    ('COSTO_SERVICIOS_EVENTOS', '501.06'),
    ('GASTO_LUZ', '601.02.01'),
    ('GASTO_AGUA', '601.02.02'),
    ('GASTO_GAS', '601.02.03'),
    ('GASTO_TELEFONO', '601.02.04'),
    ('GASTO_MANTENIMIENTO', '601.02.05'),
    ('GASTO_JARDINERIA', '601.02.06'),
    ('GASTO_LIMPIEZA', '601.02.07'),
    ('GASTO_SEGURIDAD', '601.02.08'),
    ('GASTO_SEGUROS', '601.02.09'),
    ('GASTO_VEHICULOS', '601.02.10'),
    ('GASTO_OFICINA', '601.03.01'),
    ('GASTO_HONORARIOS', '601.03.02'),
    ('GASTO_CONTABLES', '601.03.03'),
    ('GASTO_SOFTWARE', '601.03.05'),
    ('GASTO_IMPUESTOS', '601.03.06'),
    ('GASTO_PUBLICIDAD', '601.04.01'),
    ('GASTO_FOTOGRAFIA', '601.04.03'),
    ('GASTO_BANCARIOS', '602.03'),
    ('ACTIVO_MOBILIARIO', '115.03'),
    ('ACTIVO_CONSTRUCCIONES', '115.01'),
    ('GASTO_OTROS', '601.06'),
    ('NO_DEDUCIBLE_COSTO', '501.90'),
    ('NO_DEDUCIBLE_OPERACION', '601.05.01'),
    ('GASTO_NO_DEDUCIBLE', '601.05.02'),
    ('NO_DEDUCIBLE_VENTA', '601.05.03'),
    ('NO_DEDUCIBLE_FINANCIERO', '602.90'),
    ('NO_DEDUCIBLE_OTROS', '601.05.04'),
]
DESCRIPCION = 'Catálogo de categorías de gasto (Issue #390)'


def aplicar(apps, schema_editor):
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    ConfiguracionContable = apps.get_model('contabilidad', 'ConfiguracionContable')

    for codigo, nombre, tipo, naturaleza, codigo_padre, permite in CUENTAS:
        if CuentaContable.objects.filter(codigo_sat=codigo).exists():
            continue
        padre = CuentaContable.objects.filter(codigo_sat=codigo_padre).first()
        if padre is None:
            print(f"⚠️  Cuenta padre {codigo_padre} no encontrada: no se creó {codigo}")
            continue
        CuentaContable.objects.create(
            codigo_sat=codigo, nombre=nombre, tipo=tipo, naturaleza=naturaleza,
            nivel=padre.nivel + 1, padre=padre, permite_movimientos=permite,
        )
    # 601.05 se vuelve cuenta de grupo; lo que ya tenga asentado lo pasa a
    # 601.05.02 `reclasificar_compras`.
    CuentaContable.objects.filter(codigo_sat='601.02.08').update(nombre='Vigilancia y sistemas de seguridad')
    CuentaContable.objects.filter(codigo_sat='601.05').update(
        nombre='Gastos no deducibles', permite_movimientos=False)

    for operacion, codigo in CONFIGURACION:
        cuenta = CuentaContable.objects.filter(codigo_sat=codigo).first()
        if cuenta is None:
            print(f"⚠️  Cuenta {codigo} no encontrada: {operacion} queda sin configurar")
            continue
        config = ConfiguracionContable.objects.filter(operacion=operacion).first()
        if config is None:
            ConfiguracionContable.objects.create(
                operacion=operacion, cuenta=cuenta, descripcion=DESCRIPCION, activa=True)
        elif config.cuenta_id != cuenta.pk or not config.activa:
            print(f"   {operacion}: {config.cuenta.codigo_sat} → {codigo}")
            config.cuenta, config.activa = cuenta, True
            config.save(update_fields=['cuenta', 'activa'])


class Migration(migrations.Migration):

    dependencies = [
        ("contabilidad", "0037_reglas_comision_terminal"),
    ]

    operations = [
        migrations.AlterField(
            model_name="configuracioncontable",
            name="operacion",
            field=models.CharField(
                choices=[
                    ("PAGO_CLIENTE_EFECTIVO", "Pago cliente - Efectivo"),
                    ("PAGO_CLIENTE_TRANSFERENCIA", "Pago cliente - Transferencia"),
                    ("PAGO_CLIENTE_TARJETA", "Pago cliente - Tarjeta"),
                    ("INGRESO_EVENTOS", "Ingreso por eventos"),
                    ("IVA_TRASLADADO", "IVA trasladado"),
                    ("ANTICIPO_CLIENTES", "Anticipo de clientes"),
                    ("ISR_RETENIDO_CLIENTES", "ISR retenido por clientes"),
                    (
                        "OTROS_INGRESOS_CLIENTE",
                        "Otros ingresos de cliente (propinas, comisiones, etc.)",
                    ),
                    ("DEPOSITOS_GARANTIA", "Depósitos en garantía de clientes"),
                    ("IMPUESTO_HOSPEDAJE", "Impuesto al hospedaje"),
                    ("PROVEEDORES", "Proveedores"),
                    ("IVA_ACREDITABLE", "IVA acreditable"),
                    (
                        "GASTOS_GENERALES",
                        "Gastos por clasificar (compras sin categoría)",
                    ),
                    ("GASTO_INSUMOS", "Costo - Insumos para eventos"),
                    ("GASTO_SERVICIOS", "Gastos - Servicios (agua, luz, internet)"),
                    ("GASTO_MANTENIMIENTO", "Gastos - Mantenimiento y reparaciones"),
                    ("GASTO_PUBLICIDAD", "Gastos - Publicidad y marketing"),
                    ("GASTO_EQUIPO", "Gastos - Equipo y mobiliario"),
                    ("GASTO_VEHICULOS", "Gastos - Vehículos y combustible"),
                    ("GASTO_OFICINA", "Gastos - Oficina y papelería"),
                    ("GASTO_IMPUESTOS", "Gastos - Impuestos y derechos"),
                    ("GASTO_SEGUROS", "Gastos - Seguros y fianzas"),
                    (
                        "GASTO_BANCARIOS",
                        "Gastos financieros - Comisiones bancarias y de cobro",
                    ),
                    ("COMISIONES_POR_COMPROBAR", "Comisiones Openpay por comprobar"),
                    ("COSTO_ALIMENTOS", "Costo - Alimentos"),
                    ("GASTOS_BEBIDAS", "Costo - Bebidas"),
                    ("GASTOS_NOMINA_EXT", "Costo - Staff externo de eventos"),
                    (
                        "COSTO_RENTA_EVENTOS",
                        "Costo - Renta de mobiliario y equipo para eventos",
                    ),
                    (
                        "COSTO_SERVICIOS_EVENTOS",
                        "Costo - Servicios subcontratados para eventos",
                    ),
                    ("GASTO_LUZ", "Gastos - Energía eléctrica"),
                    ("GASTO_AGUA", "Gastos - Agua"),
                    ("GASTO_GAS", "Gastos - Gas"),
                    ("GASTO_TELEFONO", "Gastos - Teléfono e internet"),
                    ("GASTO_JARDINERIA", "Gastos - Jardinería"),
                    ("GASTO_LIMPIEZA", "Gastos - Limpieza"),
                    ("GASTO_SEGURIDAD", "Gastos - Vigilancia y sistemas de seguridad"),
                    ("GASTO_HONORARIOS", "Gastos - Honorarios profesionales"),
                    ("GASTO_CONTABLES", "Gastos - Servicios contables"),
                    ("GASTO_SOFTWARE", "Gastos - Software y suscripciones"),
                    ("GASTO_FOTOGRAFIA", "Gastos - Fotografía y video"),
                    ("GASTO_OTROS", "Gastos - Otros gastos"),
                    ("ACTIVO_MOBILIARIO", "Activo fijo - Mobiliario y equipo"),
                    (
                        "ACTIVO_CONSTRUCCIONES",
                        "Activo fijo - Construcciones y mejoras al inmueble",
                    ),
                    ("NO_DEDUCIBLE_COSTO", "No deducible - Costo"),
                    ("NO_DEDUCIBLE_OPERACION", "No deducible - Gastos de operación"),
                    ("NO_DEDUCIBLE_VENTA", "No deducible - Gastos de venta"),
                    ("NO_DEDUCIBLE_FINANCIERO", "No deducible - Gastos financieros"),
                    ("NO_DEDUCIBLE_OTROS", "No deducible - Otros gastos"),
                    ("SUELDOS_SALARIOS", "Sueldos y salarios"),
                    ("IMSS_PATRONAL", "IMSS patronal"),
                    ("BANCO_PRINCIPAL", "Banco principal"),
                    ("BANCO_SECUNDARIO", "Banco secundario"),
                    ("CAJA", "Caja"),
                    (
                        "AJUSTE_APERTURA",
                        "Ajuste de apertura / resultados de ejercicios anteriores",
                    ),
                    (
                        "RETIROS_DUENO",
                        "Retiros del dueño (traspasos a cuentas propias)",
                    ),
                    (
                        "APORTACIONES_DUENO",
                        "Aportaciones del dueño (traspasos desde cuentas propias)",
                    ),
                    (
                        "INVERSIONES",
                        "Inversiones (dinero enviado a o recuperado de una inversión)",
                    ),
                    (
                        "GASTO_NO_DEDUCIBLE",
                        "No deducible - Gastos de administración (sin CFDI)",
                    ),
                    ("PARTIDAS_POR_IDENTIFICAR", "Partidas bancarias por identificar"),
                ],
                max_length=50,
                unique=True,
                verbose_name="Tipo de operación",
            ),
        ),
        migrations.AlterField(
            model_name="reglaconciliacion",
            name="operacion",
            field=models.CharField(
                blank=True,
                choices=[
                    ("PAGO_CLIENTE_EFECTIVO", "Pago cliente - Efectivo"),
                    ("PAGO_CLIENTE_TRANSFERENCIA", "Pago cliente - Transferencia"),
                    ("PAGO_CLIENTE_TARJETA", "Pago cliente - Tarjeta"),
                    ("INGRESO_EVENTOS", "Ingreso por eventos"),
                    ("IVA_TRASLADADO", "IVA trasladado"),
                    ("ANTICIPO_CLIENTES", "Anticipo de clientes"),
                    ("ISR_RETENIDO_CLIENTES", "ISR retenido por clientes"),
                    (
                        "OTROS_INGRESOS_CLIENTE",
                        "Otros ingresos de cliente (propinas, comisiones, etc.)",
                    ),
                    ("DEPOSITOS_GARANTIA", "Depósitos en garantía de clientes"),
                    ("IMPUESTO_HOSPEDAJE", "Impuesto al hospedaje"),
                    ("PROVEEDORES", "Proveedores"),
                    ("IVA_ACREDITABLE", "IVA acreditable"),
                    (
                        "GASTOS_GENERALES",
                        "Gastos por clasificar (compras sin categoría)",
                    ),
                    ("GASTO_INSUMOS", "Costo - Insumos para eventos"),
                    ("GASTO_SERVICIOS", "Gastos - Servicios (agua, luz, internet)"),
                    ("GASTO_MANTENIMIENTO", "Gastos - Mantenimiento y reparaciones"),
                    ("GASTO_PUBLICIDAD", "Gastos - Publicidad y marketing"),
                    ("GASTO_EQUIPO", "Gastos - Equipo y mobiliario"),
                    ("GASTO_VEHICULOS", "Gastos - Vehículos y combustible"),
                    ("GASTO_OFICINA", "Gastos - Oficina y papelería"),
                    ("GASTO_IMPUESTOS", "Gastos - Impuestos y derechos"),
                    ("GASTO_SEGUROS", "Gastos - Seguros y fianzas"),
                    (
                        "GASTO_BANCARIOS",
                        "Gastos financieros - Comisiones bancarias y de cobro",
                    ),
                    ("COMISIONES_POR_COMPROBAR", "Comisiones Openpay por comprobar"),
                    ("COSTO_ALIMENTOS", "Costo - Alimentos"),
                    ("GASTOS_BEBIDAS", "Costo - Bebidas"),
                    ("GASTOS_NOMINA_EXT", "Costo - Staff externo de eventos"),
                    (
                        "COSTO_RENTA_EVENTOS",
                        "Costo - Renta de mobiliario y equipo para eventos",
                    ),
                    (
                        "COSTO_SERVICIOS_EVENTOS",
                        "Costo - Servicios subcontratados para eventos",
                    ),
                    ("GASTO_LUZ", "Gastos - Energía eléctrica"),
                    ("GASTO_AGUA", "Gastos - Agua"),
                    ("GASTO_GAS", "Gastos - Gas"),
                    ("GASTO_TELEFONO", "Gastos - Teléfono e internet"),
                    ("GASTO_JARDINERIA", "Gastos - Jardinería"),
                    ("GASTO_LIMPIEZA", "Gastos - Limpieza"),
                    ("GASTO_SEGURIDAD", "Gastos - Vigilancia y sistemas de seguridad"),
                    ("GASTO_HONORARIOS", "Gastos - Honorarios profesionales"),
                    ("GASTO_CONTABLES", "Gastos - Servicios contables"),
                    ("GASTO_SOFTWARE", "Gastos - Software y suscripciones"),
                    ("GASTO_FOTOGRAFIA", "Gastos - Fotografía y video"),
                    ("GASTO_OTROS", "Gastos - Otros gastos"),
                    ("ACTIVO_MOBILIARIO", "Activo fijo - Mobiliario y equipo"),
                    (
                        "ACTIVO_CONSTRUCCIONES",
                        "Activo fijo - Construcciones y mejoras al inmueble",
                    ),
                    ("NO_DEDUCIBLE_COSTO", "No deducible - Costo"),
                    ("NO_DEDUCIBLE_OPERACION", "No deducible - Gastos de operación"),
                    ("NO_DEDUCIBLE_VENTA", "No deducible - Gastos de venta"),
                    ("NO_DEDUCIBLE_FINANCIERO", "No deducible - Gastos financieros"),
                    ("NO_DEDUCIBLE_OTROS", "No deducible - Otros gastos"),
                    ("SUELDOS_SALARIOS", "Sueldos y salarios"),
                    ("IMSS_PATRONAL", "IMSS patronal"),
                    ("BANCO_PRINCIPAL", "Banco principal"),
                    ("BANCO_SECUNDARIO", "Banco secundario"),
                    ("CAJA", "Caja"),
                    (
                        "AJUSTE_APERTURA",
                        "Ajuste de apertura / resultados de ejercicios anteriores",
                    ),
                    (
                        "RETIROS_DUENO",
                        "Retiros del dueño (traspasos a cuentas propias)",
                    ),
                    (
                        "APORTACIONES_DUENO",
                        "Aportaciones del dueño (traspasos desde cuentas propias)",
                    ),
                    (
                        "INVERSIONES",
                        "Inversiones (dinero enviado a o recuperado de una inversión)",
                    ),
                    (
                        "GASTO_NO_DEDUCIBLE",
                        "No deducible - Gastos de administración (sin CFDI)",
                    ),
                    ("PARTIDAS_POR_IDENTIFICAR", "Partidas bancarias por identificar"),
                ],
                help_text="La cuenta sale de la Configuración contable. Usa esto o «Cuenta contrapartida».",
                max_length=50,
                verbose_name="Operación contable",
            ),
        ),
        migrations.RunPython(aplicar, migrations.RunPython.noop),
    ]
