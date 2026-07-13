# Generated manually - agrega la cuenta de Capital para AJUSTE_APERTURA

from django.db import migrations


def agregar_cuenta_ajuste_apertura(apps, schema_editor):
    """
    Agrega 304 (Resultado de ejercicios anteriores) y su subcuenta 304.01
    bajo el rubro 300 (Capital contable) que ya existe en el catálogo.
    304 es el código agrupador SAT real (Anexo 24) para este concepto.
    """
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    capital = CuentaContable.objects.get(codigo_sat='300')

    resultado, _ = CuentaContable.objects.get_or_create(
        codigo_sat='304',
        defaults={
            'nombre': 'Resultado de ejercicios anteriores',
            'tipo': 'CAPITAL',
            'naturaleza': 'A',
            'nivel': 2,
            'padre': capital,
            'permite_movimientos': False,
            'activa': True,
        }
    )
    CuentaContable.objects.get_or_create(
        codigo_sat='304.01',
        defaults={
            'nombre': 'Resultado de ejercicios anteriores',
            'tipo': 'CAPITAL',
            'naturaleza': 'A',
            'nivel': 3,
            'padre': resultado,
            'permite_movimientos': True,
            'activa': True,
        }
    )


def reverse_cuenta_ajuste_apertura(apps, schema_editor):
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    CuentaContable.objects.filter(codigo_sat__in=['304', '304.01']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0007_alter_configuracioncontable_options_and_more'),
    ]

    operations = [
        migrations.RunPython(agregar_cuenta_ajuste_apertura, reverse_cuenta_ajuste_apertura),
    ]
