# Cuentas y configuración que necesitan las reglas de conciliación (Issue #329)
# para asentar sin intervención: el catálogo sembrado no traía cuentas para
# los movimientos del dueño, inversiones, gastos sin CFDI ni partidas por
# identificar. Códigos del agrupador SAT; el contador puede reasignar cualquier
# operación en Configuración contable sin tocar código.
#
# Idempotente y conservadora: crea la cuenta solo si su código no existe y la
# configuración solo si la operación no tiene ya una cuenta asignada (nunca
# sobrescribe lo capturado en el admin).

from django.db import migrations

# (codigo, nombre, tipo, naturaleza, nivel, codigo_padre, permite_movimientos)
CUENTAS = [
    ('103', 'Inversiones', 'ACTIVO', 'D', 2, '100', False),
    ('103.01', 'Inversiones temporales', 'ACTIVO', 'D', 3, '103', True),
    ('205.04', 'Partidas bancarias por identificar', 'PASIVO', 'A', 3, '205', True),
    ('301.03', 'Aportaciones del propietario', 'CAPITAL', 'A', 3, '301', True),
    ('301.04', 'Retiros del propietario', 'CAPITAL', 'D', 3, '301', True),
    ('601.05', 'Gastos no deducibles', 'GASTO', 'D', 2, '601', True),
]

# (operacion, codigo) — incluye las categorías de gasto de las palabras clave
# que tienen una cuenta inequívoca en el catálogo.
CONFIGURACION = [
    ('RETIROS_DUENO', '301.04'),
    ('APORTACIONES_DUENO', '301.03'),
    ('INVERSIONES', '103.01'),
    ('GASTO_NO_DEDUCIBLE', '601.05'),
    ('PARTIDAS_POR_IDENTIFICAR', '205.04'),
    ('GASTO_MANTENIMIENTO', '601.02.05'),
    ('GASTO_PUBLICIDAD', '601.04.01'),
    ('GASTO_VEHICULOS', '601.02.10'),
]

DESCRIPCION = 'Reglas de conciliación bancaria (Issue #329)'


def crear(apps, schema_editor):
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    ConfiguracionContable = apps.get_model('contabilidad', 'ConfiguracionContable')

    for codigo, nombre, tipo, naturaleza, nivel, codigo_padre, permite in CUENTAS:
        if CuentaContable.objects.filter(codigo_sat=codigo).exists():
            continue
        padre = CuentaContable.objects.filter(codigo_sat=codigo_padre).first()
        if padre is None:
            print(f"⚠️  Cuenta padre {codigo_padre} no encontrada: no se creó {codigo}")
            continue
        CuentaContable.objects.create(
            codigo_sat=codigo, nombre=nombre, tipo=tipo, naturaleza=naturaleza,
            nivel=nivel, padre=padre, permite_movimientos=permite,
        )

    for operacion, codigo in CONFIGURACION:
        if ConfiguracionContable.objects.filter(operacion=operacion).exists():
            continue
        cuenta = CuentaContable.objects.filter(codigo_sat=codigo).first()
        if cuenta is None:
            print(f"⚠️  Cuenta {codigo} no encontrada: {operacion} queda sin configurar")
            continue
        ConfiguracionContable.objects.create(
            operacion=operacion, cuenta=cuenta, descripcion=DESCRIPCION, activa=True,
        )


def revertir(apps, schema_editor):
    ConfiguracionContable = apps.get_model('contabilidad', 'ConfiguracionContable')
    ConfiguracionContable.objects.filter(descripcion=DESCRIPCION).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0026_sembrar_reglas_conciliacion'),
    ]

    operations = [
        migrations.RunPython(crear, revertir),
    ]
