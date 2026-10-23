"""
Regla «Depósito de efectivo a banco»: el efectivo cobrado va a Caja y, al
depositarlo (BBVA: «DEPOSITO EFECTIVO PRACTIC»), nada registraba el traspaso
Caja → Bancos. Solo aplica a abonos sin asiento ligado (el emparejamiento con
Pagos corre antes) y, en `aplicar_reglas`, solo si Caja tiene saldo suficiente.
"""
from django.db import migrations


def sembrar(apps, schema_editor):
    ReglaConciliacion = apps.get_model('contabilidad', 'ReglaConciliacion')
    ReglaConciliacion.objects.get_or_create(
        nombre='Depósito de efectivo a banco', origen='SISTEMA',
        defaults={
            'tipo_movimiento': 'ABONO', 'patrones': 'DEPOSITO EFECTIVO*', 'operacion': 'CAJA',
            'prioridad': 30, 'aplicar_automaticamente': True,
        },
    )


def quitar(apps, schema_editor):
    apps.get_model('contabilidad', 'ReglaConciliacion').objects.filter(
        origen='SISTEMA', nombre='Depósito de efectivo a banco').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0039_descarga_masiva_sat'),
    ]

    operations = [
        migrations.RunPython(sembrar, quitar),
    ]
