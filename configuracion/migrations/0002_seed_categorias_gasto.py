"""
Seed de las 12 categorías de gasto que antes estaban hardcodeadas
en comercial.models.Gasto.CATEGORIAS.
"""
from django.db import migrations


CATEGORIAS_SEED = [
    ("SIN_CLASIFICAR", "Sin Clasificar", 0),
    ("SERVICIO_EXTERNO", "Servicio Externo", 10),
    ("BEBIDAS_SIN_ALCOHOL", "Bebidas Sin Alcohol", 20),
    ("BEBIDAS_CON_ALCOHOL", "Bebidas Con Alcohol", 30),
    ("LIMPIEZA", "Limpieza Y Desechables", 40),
    ("MOBILIARIO_EQ", "Mobiliario Y Equipo", 50),
    ("MANTENIMIENTO", "Mantenimiento Y Reparaciones", 60),
    ("NOMINA_EXT", "Servicios Staff Externo", 70),
    ("IMPUESTOS", "Pago De Impuestos", 80),
    ("PUBLICIDAD", "Publicidad Y Marketing", 90),
    ("SERVICIOS_ADMON", "Servicios Administrativos Y Bancarios", 100),
    ("OTRO", "Otros Gastos", 110),
]


def seed(apps, schema_editor):
    CategoriaGasto = apps.get_model("configuracion", "CategoriaGasto")
    for clave, nombre, orden in CATEGORIAS_SEED:
        CategoriaGasto.objects.get_or_create(
            clave=clave,
            defaults={"nombre": nombre, "orden": orden, "activa": True},
        )


def unseed(apps, schema_editor):
    CategoriaGasto = apps.get_model("configuracion", "CategoriaGasto")
    claves = [c[0] for c in CATEGORIAS_SEED]
    CategoriaGasto.objects.filter(clave__in=claves).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("configuracion", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
