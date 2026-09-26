# La categoría de compra «Servicios Staff Externo» apunta a GASTOS_NOMINA_EXT,
# que no tenía cuenta: sus compras caían en Gastos generales. Se configura
# contra 601.01.08 Personal externo (meseros, cocina). Idempotente y sin
# sobrescribir lo capturado en el admin, igual que 0027/0028.

from django.db import migrations

OPERACION, CODIGO = 'GASTOS_NOMINA_EXT', '601.01.08'
DESCRIPCION = 'Clasificación de compras por proveedor — staff externo'


def crear(apps, schema_editor):
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    ConfiguracionContable = apps.get_model('contabilidad', 'ConfiguracionContable')
    if ConfiguracionContable.objects.filter(operacion=OPERACION).exists():
        return
    cuenta = CuentaContable.objects.filter(codigo_sat=CODIGO).first()
    if cuenta is None:
        print(f"⚠️  Cuenta {CODIGO} no encontrada: {OPERACION} queda sin configurar")
        return
    ConfiguracionContable.objects.create(
        operacion=OPERACION, cuenta=cuenta, descripcion=DESCRIPCION, activa=True,
    )


def revertir(apps, schema_editor):
    apps.get_model('contabilidad', 'ConfiguracionContable').objects.filter(descripcion=DESCRIPCION).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0028_cuentas_insumos_impuestos'),
    ]

    operations = [
        migrations.RunPython(crear, revertir),
    ]
