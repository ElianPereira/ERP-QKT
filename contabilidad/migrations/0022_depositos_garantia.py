# Depósito en garantía (Issue #318, fase 3): cuenta de pasivo 205.03 y su
# configuración DEPOSITOS_GARANTIA. El depósito es dinero del cliente en
# custodia, no ingreso ni anticipo. Idempotente.

from django.db import migrations

CODIGO = '205.03'
NOMBRE = 'Depósitos en garantía de clientes'


def crear_cuenta(apps, schema_editor):
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    ConfiguracionContable = apps.get_model('contabilidad', 'ConfiguracionContable')

    padre = CuentaContable.objects.filter(codigo_sat='205').first()
    if padre is None:
        print("⚠️  Cuenta 205 no encontrada: no se creó 205.03 para DEPOSITOS_GARANTIA")
        return
    cuenta, _ = CuentaContable.objects.get_or_create(
        codigo_sat=CODIGO,
        defaults={
            'nombre': NOMBRE, 'tipo': 'PASIVO', 'naturaleza': 'A',
            'nivel': 3, 'padre': padre, 'permite_movimientos': True,
        },
    )
    ConfiguracionContable.objects.get_or_create(
        operacion='DEPOSITOS_GARANTIA',
        defaults={'cuenta': cuenta, 'descripcion': NOMBRE, 'activa': True},
    )


def revertir(apps, schema_editor):
    ConfiguracionContable = apps.get_model('contabilidad', 'ConfiguracionContable')
    ConfiguracionContable.objects.filter(operacion='DEPOSITOS_GARANTIA').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0021_retiro_airbnb_permisos'),
    ]

    operations = [
        migrations.RunPython(crear_cuenta, revertir),
    ]
