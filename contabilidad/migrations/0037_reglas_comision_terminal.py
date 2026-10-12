"""
La tasa de descuento de la terminal BBVA no se descuenta del depósito: BBVA la
cobra como dos cargos propios en el estado de cuenta («APLI TASA DE DES DEBITO
… TERMINALES PUNTO» y su «IVA TASA DE DESC …»), que ninguna regla reconocía y
se asentaban a mano. El IVA va antes (prioridad menor) por si un patrón más
amplio llegara a calzar con los dos.
"""
from django.db import migrations

REGLAS = [
    ('IVA de comisión de terminal', 'CARGO', 'IVA TASA DE DES*', 'IVA_ACREDITABLE', 11),
    ('Comisión de terminal (tasa de descuento)', 'CARGO', 'APLI TASA DE DES*', 'GASTO_BANCARIOS', 21),
]


def sembrar(apps, schema_editor):
    ReglaConciliacion = apps.get_model('contabilidad', 'ReglaConciliacion')
    for nombre, tipo, patrones, operacion, prioridad in REGLAS:
        ReglaConciliacion.objects.get_or_create(
            nombre=nombre, origen='SISTEMA',
            defaults={
                'tipo_movimiento': tipo, 'patrones': patrones, 'operacion': operacion,
                'prioridad': prioridad, 'aplicar_automaticamente': True,
            },
        )


def quitar(apps, schema_editor):
    ReglaConciliacion = apps.get_model('contabilidad', 'ReglaConciliacion')
    ReglaConciliacion.objects.filter(origen='SISTEMA', nombre__in=[r[0] for r in REGLAS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0036_comisiones_por_comprobar'),
    ]

    operations = [
        migrations.RunPython(sembrar, quitar),
    ]
