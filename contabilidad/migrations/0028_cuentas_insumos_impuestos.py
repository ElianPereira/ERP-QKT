# Cuentas de las palabras clave INS e IMP (Issue #329), que 0027 dejó sin
# configurar por no haber una cuenta inequívoca en el catálogo. Decisión
# tomada con el propietario:
#
# - GASTO_INSUMOS → 501.04 Insumos para eventos (costo de ventas): bolis,
#   hielo, desechables, bebidas son costo directo del servicio, no gasto
#   general. También reciben esta cuenta las Compras con categoría INSUMOS,
#   BEBIDAS, LICOR o HIELO (`MAPEO_CATEGORIA_CUENTA`), que hasta hoy caían en
#   GASTOS_GENERALES.
# - GASTO_IMPUESTOS → 601.03.06 Impuestos y derechos: predial, derechos,
#   licencias, tenencia. Los pagos de ISR/IVA al SAT no son gasto y no deben
#   asentarse con esta palabra clave.
#
# Idempotente y conservadora, igual que 0027: no crea una cuenta que ya exista
# ni sobrescribe una operación ya configurada en el admin.

from django.db import migrations

CUENTAS = [
    ('501.04', 'Insumos para eventos', 'COSTO', 'D', 2, '501'),
    ('601.03.06', 'Impuestos y derechos', 'GASTO', 'D', 3, '601.03'),
]
CONFIGURACION = [
    ('GASTO_INSUMOS', '501.04'),
    ('GASTO_IMPUESTOS', '601.03.06'),
]
DESCRIPCION = 'Reglas de conciliación bancaria (Issue #329) — INS / IMP'


def crear(apps, schema_editor):
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    ConfiguracionContable = apps.get_model('contabilidad', 'ConfiguracionContable')

    for codigo, nombre, tipo, naturaleza, nivel, codigo_padre in CUENTAS:
        if CuentaContable.objects.filter(codigo_sat=codigo).exists():
            continue
        padre = CuentaContable.objects.filter(codigo_sat=codigo_padre).first()
        if padre is None:
            print(f"⚠️  Cuenta padre {codigo_padre} no encontrada: no se creó {codigo}")
            continue
        CuentaContable.objects.create(
            codigo_sat=codigo, nombre=nombre, tipo=tipo, naturaleza=naturaleza,
            nivel=nivel, padre=padre, permite_movimientos=True,
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
        ('contabilidad', '0027_cuentas_reglas_conciliacion'),
    ]

    operations = [
        migrations.RunPython(crear, revertir),
    ]
