# Configura GASTO_BANCARIOS → 601.03.04 (Comisiones bancarias), usada por la
# póliza automática de la comisión de Openpay. Idempotente: no toca nada si
# ya está configurada.

from django.db import migrations


def configurar_gasto_bancarios(apps, schema_editor):
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    ConfiguracionContable = apps.get_model('contabilidad', 'ConfiguracionContable')

    if ConfiguracionContable.objects.filter(operacion='GASTO_BANCARIOS').exists():
        return
    try:
        cuenta = CuentaContable.objects.get(codigo_sat='601.03.04')
    except CuentaContable.DoesNotExist:
        print("⚠️  Cuenta 601.03.04 no encontrada para GASTO_BANCARIOS")
        return
    ConfiguracionContable.objects.create(
        operacion='GASTO_BANCARIOS',
        cuenta=cuenta,
        descripcion='Comisiones bancarias y de pasarelas de pago (Openpay, terminal)',
        activa=True,
    )


def revertir(apps, schema_editor):
    ConfiguracionContable = apps.get_model('contabilidad', 'ConfiguracionContable')
    ConfiguracionContable.objects.filter(
        operacion='GASTO_BANCARIOS',
        descripcion='Comisiones bancarias y de pasarelas de pago (Openpay, terminal)',
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0013_rename_contabilida_codigo__a1b2c3_idx_contabilida_codigo__b58027_idx_and_more'),
    ]

    operations = [
        migrations.RunPython(configurar_gasto_bancarios, revertir),
    ]
