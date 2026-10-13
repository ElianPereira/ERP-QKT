# Catálogo de categorías de gasto (Issue #390). Además del cambio de choices,
# reasigna las dos categorías retiradas:
# - SERVICIOS_ADMON / SERVICIO_EXTERNO de los bancos y pasarelas (proveedores
#   que cobran de la cuenta o descuentan de los depósitos) → COMISIONES_BANCARIAS.
# - El resto → SIN_CLASIFICAR (Compra/Gasto) o vacío (Proveedor): mezclaban
#   honorarios, software y comisiones, no se adivina cuál era.
# Solo cambia la categoría: reapuntar las pólizas ya asentadas es
# `manage.py reclasificar_compras`, que simula por defecto.

from django.db import migrations, models
from django.db.models import Q

RETIRADAS = ('SERVICIOS_ADMON', 'SERVICIO_EXTERNO')
DE_BANCO = Q(descuenta_de_depositos=True) | Q(cobra_de_la_cuenta=True)


def reasignar(apps, schema_editor):
    Proveedor = apps.get_model('comercial', 'Proveedor')
    Compra = apps.get_model('comercial', 'Compra')
    Gasto = apps.get_model('comercial', 'Gasto')

    bancos = Proveedor.objects.filter(DE_BANCO)
    bancos.filter(Q(categoria_gasto__in=RETIRADAS) | Q(categoria_gasto='')) \
        .update(categoria_gasto='COMISIONES_BANCARIAS')
    Proveedor.objects.filter(categoria_gasto__in=RETIRADAS).update(categoria_gasto='')

    de_bancos = Compra.objects.filter(proveedor__in=bancos)
    de_bancos.filter(categoria__in=RETIRADAS + ('SIN_CLASIFICAR',)).update(categoria='COMISIONES_BANCARIAS')
    Gasto.objects.filter(compra__in=de_bancos, categoria__in=RETIRADAS + ('SIN_CLASIFICAR',)) \
        .update(categoria='COMISIONES_BANCARIAS')
    Compra.objects.filter(categoria__in=RETIRADAS).update(categoria='SIN_CLASIFICAR')
    Gasto.objects.filter(categoria__in=RETIRADAS).update(categoria='SIN_CLASIFICAR')


class Migration(migrations.Migration):

    dependencies = [
        ("comercial", "0114_ayuda_comision_tpv"),
    ]

    operations = [
        migrations.AlterField(
            model_name="compra",
            name="categoria",
            field=models.CharField(
                choices=[
                    ("SIN_CLASIFICAR", "Sin Clasificar"),
                    ("ALIMENTOS", "Alimentos"),
                    ("BEBIDAS_SIN_ALCOHOL", "Bebidas Sin Alcohol"),
                    ("BEBIDAS_CON_ALCOHOL", "Bebidas Con Alcohol"),
                    ("INSUMOS", "Insumos para eventos"),
                    ("NOMINA_EXT", "Staff externo de eventos"),
                    ("RENTA_EVENTOS", "Renta de mobiliario y equipo para eventos"),
                    ("SERVICIOS_EVENTOS", "Servicios subcontratados para eventos"),
                    ("LUZ", "Energía eléctrica (incluye combustible de la planta)"),
                    ("AGUA", "Agua"),
                    ("GAS", "Gas"),
                    ("TELEFONO_INTERNET", "Teléfono e internet"),
                    ("MANTENIMIENTO", "Mantenimiento Y Reparaciones"),
                    ("JARDINERIA", "Jardinería"),
                    ("LIMPIEZA", "Limpieza Y Desechables"),
                    ("SEGURIDAD", "Vigilancia y sistemas de seguridad"),
                    ("SEGUROS", "Seguros y fianzas"),
                    (
                        "VEHICULOS",
                        "Vehículos (combustible, mantenimiento y refacciones)",
                    ),
                    ("PAPELERIA", "Papelería y oficina"),
                    ("HONORARIOS", "Honorarios profesionales"),
                    ("CONTABILIDAD", "Servicios contables"),
                    ("SOFTWARE", "Software y suscripciones"),
                    ("IMPUESTOS", "Impuestos locales y derechos (predial, licencias)"),
                    ("PUBLICIDAD", "Publicidad Y Marketing"),
                    ("FOTOGRAFIA", "Fotografía y video"),
                    ("COMISIONES_BANCARIAS", "Comisiones bancarias y de cobro"),
                    ("MOBILIARIO_EQ", "Mobiliario Y Equipo (activo)"),
                    ("CONSTRUCCIONES", "Construcciones y mejoras al inmueble (activo)"),
                    ("OTRO", "Otros Gastos"),
                ],
                db_index=True,
                default="SIN_CLASIFICAR",
                help_text="Define la cuenta contable del gasto. Al cambiarla se reclasifica la póliza y el proveedor la recuerda para sus próximas facturas.",
                max_length=20,
                verbose_name="Categoría de gasto",
            ),
        ),
        migrations.AlterField(
            model_name="gasto",
            name="categoria",
            field=models.CharField(
                choices=[
                    ("SIN_CLASIFICAR", "Sin Clasificar"),
                    ("ALIMENTOS", "Alimentos"),
                    ("BEBIDAS_SIN_ALCOHOL", "Bebidas Sin Alcohol"),
                    ("BEBIDAS_CON_ALCOHOL", "Bebidas Con Alcohol"),
                    ("INSUMOS", "Insumos para eventos"),
                    ("NOMINA_EXT", "Staff externo de eventos"),
                    ("RENTA_EVENTOS", "Renta de mobiliario y equipo para eventos"),
                    ("SERVICIOS_EVENTOS", "Servicios subcontratados para eventos"),
                    ("LUZ", "Energía eléctrica (incluye combustible de la planta)"),
                    ("AGUA", "Agua"),
                    ("GAS", "Gas"),
                    ("TELEFONO_INTERNET", "Teléfono e internet"),
                    ("MANTENIMIENTO", "Mantenimiento Y Reparaciones"),
                    ("JARDINERIA", "Jardinería"),
                    ("LIMPIEZA", "Limpieza Y Desechables"),
                    ("SEGURIDAD", "Vigilancia y sistemas de seguridad"),
                    ("SEGUROS", "Seguros y fianzas"),
                    (
                        "VEHICULOS",
                        "Vehículos (combustible, mantenimiento y refacciones)",
                    ),
                    ("PAPELERIA", "Papelería y oficina"),
                    ("HONORARIOS", "Honorarios profesionales"),
                    ("CONTABILIDAD", "Servicios contables"),
                    ("SOFTWARE", "Software y suscripciones"),
                    ("IMPUESTOS", "Impuestos locales y derechos (predial, licencias)"),
                    ("PUBLICIDAD", "Publicidad Y Marketing"),
                    ("FOTOGRAFIA", "Fotografía y video"),
                    ("COMISIONES_BANCARIAS", "Comisiones bancarias y de cobro"),
                    ("MOBILIARIO_EQ", "Mobiliario Y Equipo (activo)"),
                    ("CONSTRUCCIONES", "Construcciones y mejoras al inmueble (activo)"),
                    ("OTRO", "Otros Gastos"),
                ],
                default="SIN_CLASIFICAR",
                max_length=20,
                verbose_name="Categoría",
            ),
        ),
        migrations.AlterField(
            model_name="proveedor",
            name="categoria_gasto",
            field=models.CharField(
                blank=True,
                choices=[
                    ("SIN_CLASIFICAR", "Sin Clasificar"),
                    ("ALIMENTOS", "Alimentos"),
                    ("BEBIDAS_SIN_ALCOHOL", "Bebidas Sin Alcohol"),
                    ("BEBIDAS_CON_ALCOHOL", "Bebidas Con Alcohol"),
                    ("INSUMOS", "Insumos para eventos"),
                    ("NOMINA_EXT", "Staff externo de eventos"),
                    ("RENTA_EVENTOS", "Renta de mobiliario y equipo para eventos"),
                    ("SERVICIOS_EVENTOS", "Servicios subcontratados para eventos"),
                    ("LUZ", "Energía eléctrica (incluye combustible de la planta)"),
                    ("AGUA", "Agua"),
                    ("GAS", "Gas"),
                    ("TELEFONO_INTERNET", "Teléfono e internet"),
                    ("MANTENIMIENTO", "Mantenimiento Y Reparaciones"),
                    ("JARDINERIA", "Jardinería"),
                    ("LIMPIEZA", "Limpieza Y Desechables"),
                    ("SEGURIDAD", "Vigilancia y sistemas de seguridad"),
                    ("SEGUROS", "Seguros y fianzas"),
                    (
                        "VEHICULOS",
                        "Vehículos (combustible, mantenimiento y refacciones)",
                    ),
                    ("PAPELERIA", "Papelería y oficina"),
                    ("HONORARIOS", "Honorarios profesionales"),
                    ("CONTABILIDAD", "Servicios contables"),
                    ("SOFTWARE", "Software y suscripciones"),
                    ("IMPUESTOS", "Impuestos locales y derechos (predial, licencias)"),
                    ("PUBLICIDAD", "Publicidad Y Marketing"),
                    ("FOTOGRAFIA", "Fotografía y video"),
                    ("COMISIONES_BANCARIAS", "Comisiones bancarias y de cobro"),
                    ("MOBILIARIO_EQ", "Mobiliario Y Equipo (activo)"),
                    ("CONSTRUCCIONES", "Construcciones y mejoras al inmueble (activo)"),
                    ("OTRO", "Otros Gastos"),
                ],
                help_text="Las facturas nuevas de este proveedor entran con esta categoría. Se llena sola la primera vez que clasificas una de sus compras.",
                max_length=20,
                verbose_name="Categoría de gasto",
            ),
        ),
        migrations.RunPython(reasignar, migrations.RunPython.noop),
    ]
