# Retiro de Revolut / cuenta pagadora (decisión del propietario, 2026-09-29):
# se vuelve a una sola cuenta, BBVA Maestra PYME, para cobros y para gastos
# con y sin factura. Revierte 0031/0032 sin borrar nada que pueda tener
# historia: la cuenta 102.02.03 y cualquier cuenta bancaria pagadora se
# DESACTIVAN (soft-deactivation), y si ya tienen movimientos se avisa en el
# log del deploy para revisarlos a mano. Se quita la configuración de
# INGRESOS_FINANCIEROS que sembró 0032 y se desactiva la regla de intereses de
# Revolut; las reglas de Railway/Anthropic/Cloudflare/Meta se quedan porque
# también clasifican esos cargos en el estado de cuenta de BBVA.

from django.db import migrations, models


DESCRIPCION_0032 = 'Revolut como cuenta pagadora (Issue #341)'


def retirar(apps, schema_editor):
    CuentaBancaria = apps.get_model('contabilidad', 'CuentaBancaria')
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    ConfiguracionContable = apps.get_model('contabilidad', 'ConfiguracionContable')
    ReglaConciliacion = apps.get_model('contabilidad', 'ReglaConciliacion')
    MovimientoContable = apps.get_model('contabilidad', 'MovimientoContable')
    Compra = apps.get_model('comercial', 'Compra')

    for banco in CuentaBancaria.objects.filter(rol='PAGO', activa=True):
        compras = Compra.objects.filter(cuenta_pago=banco).count()
        if compras:
            print(f"⚠️  {compras} compra(s) quedaron con cuenta de pago «{banco.nombre}»: cámbialas a BBVA")
        banco.activa = False
        banco.save(update_fields=['activa'])

    cuenta = CuentaContable.objects.filter(codigo_sat='102.02.03').first()
    if cuenta:
        lineas = MovimientoContable.objects.filter(cuenta=cuenta, poliza__estado='APLICADA').count()
        if lineas:
            print(f"⚠️  102.02.03 tiene {lineas} movimiento(s) en pólizas aplicadas: revísalos")
        cuenta.activa = False
        cuenta.save(update_fields=['activa'])

    ConfiguracionContable.objects.filter(operacion='INGRESOS_FINANCIEROS', descripcion=DESCRIPCION_0032).delete()
    ReglaConciliacion.objects.filter(origen='SISTEMA', nombre='Intereses del ahorro Revolut').update(activa=False)


class Migration(migrations.Migration):

    dependencies = [
        ("contabilidad", "0032_revolut_cuenta_pagadora"),
        ("comercial", "0104_clasificacion_compras_por_proveedor"),
    ]

    operations = [
        migrations.RunPython(retirar, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="cuentabancaria",
            name="rol",
        ),
        migrations.RemoveField(
            model_name="cuentabancaria",
            name="textos_traspaso",
        ),
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
                    ("GASTOS_GENERALES", "Gastos generales"),
                    ("GASTO_INSUMOS", "Gastos - Insumos y bebidas"),
                    ("GASTO_SERVICIOS", "Gastos - Servicios (agua, luz, internet)"),
                    ("GASTO_MANTENIMIENTO", "Gastos - Mantenimiento y limpieza"),
                    ("GASTO_PUBLICIDAD", "Gastos - Publicidad y marketing"),
                    ("GASTO_EQUIPO", "Gastos - Equipo y mobiliario"),
                    ("GASTO_VEHICULOS", "Gastos - Vehículos y combustible"),
                    ("GASTO_OFICINA", "Gastos - Oficina y papelería"),
                    ("GASTO_IMPUESTOS", "Gastos - Impuestos y derechos"),
                    ("GASTO_SEGUROS", "Gastos - Seguros y fianzas"),
                    ("GASTO_BANCARIOS", "Gastos - Comisiones bancarias"),
                    ("GASTOS_BEBIDAS", "Gastos bebidas"),
                    ("GASTOS_NOMINA_EXT", "Gastos nómina externa"),
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
                    ("GASTO_NO_DEDUCIBLE", "Gastos no deducibles (sin CFDI)"),
                    ("PARTIDAS_POR_IDENTIFICAR", "Partidas bancarias por identificar"),
                ],
                max_length=50,
                unique=True,
                verbose_name="Tipo de operación",
            ),
        ),
        migrations.AlterField(
            model_name="estadocuentabancario",
            name="formato",
            field=models.CharField(
                choices=[("PDF", "PDF"), ("XML", "XML")],
                max_length=5,
                verbose_name="Formato",
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
                    ("GASTOS_GENERALES", "Gastos generales"),
                    ("GASTO_INSUMOS", "Gastos - Insumos y bebidas"),
                    ("GASTO_SERVICIOS", "Gastos - Servicios (agua, luz, internet)"),
                    ("GASTO_MANTENIMIENTO", "Gastos - Mantenimiento y limpieza"),
                    ("GASTO_PUBLICIDAD", "Gastos - Publicidad y marketing"),
                    ("GASTO_EQUIPO", "Gastos - Equipo y mobiliario"),
                    ("GASTO_VEHICULOS", "Gastos - Vehículos y combustible"),
                    ("GASTO_OFICINA", "Gastos - Oficina y papelería"),
                    ("GASTO_IMPUESTOS", "Gastos - Impuestos y derechos"),
                    ("GASTO_SEGUROS", "Gastos - Seguros y fianzas"),
                    ("GASTO_BANCARIOS", "Gastos - Comisiones bancarias"),
                    ("GASTOS_BEBIDAS", "Gastos bebidas"),
                    ("GASTOS_NOMINA_EXT", "Gastos nómina externa"),
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
                    ("GASTO_NO_DEDUCIBLE", "Gastos no deducibles (sin CFDI)"),
                    ("PARTIDAS_POR_IDENTIFICAR", "Partidas bancarias por identificar"),
                ],
                help_text="La cuenta sale de la Configuración contable. Usa esto o «Cuenta contrapartida».",
                max_length=50,
                verbose_name="Operación contable",
            ),
        ),
    ]
