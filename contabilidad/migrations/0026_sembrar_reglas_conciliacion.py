"""
Reglas de sistema para asentar solos los movimientos bancarios sin documento
(Issue #329): patrones genéricos de BBVA y las palabras clave que el dueño
escribe en el concepto de sus transferencias. Las cuentas propias del dueño se
aprenden desde «Clasificar» o se capturan en el admin, nunca en el repo.

Las palabras clave de gasto son asientos provisionales: cuando llega el CFDI
del mismo cargo, la Compra lo sustituye (ver
`sustituir_asientos_provisionales_por_cfdi`). Ninguna usa «PAGO»: BBVA lo
imprime en toda transferencia («PAGO CUENTA DE TERCERO»). Las abreviaciones
(INS, MANT, PUB…) se buscan como palabra completa; «*» final = prefijo.

Las reglas apuntan a una operación de ConfiguracionContable: mientras esa
operación no tenga cuenta asignada, la regla calza pero no asienta nada (el
movimiento queda pendiente y la acción lo avisa).
"""
from django.db import migrations

REGLAS = [
    ('IVA de comisión bancaria', 'CARGO', 'IVA COM*', 'IVA_ACREDITABLE', 10),
    ('Comisión bancaria', 'CARGO', 'SERV BANCA INTERNET|COM SERV BCA INTERNET|COMISION*', 'GASTO_BANCARIOS', 20),
    ('Traspaso a cuenta propia (retiro del dueño)', 'CARGO', 'TRASPAS*|RETIRO*', 'RETIROS_DUENO', 90),
    ('Traspaso desde cuenta propia (aportación del dueño)', 'ABONO', 'TRASPAS*', 'APORTACIONES_DUENO', 90),
    ('Palabra clave: INV / INVERSION', 'AMBOS', 'INVERSION*|INV', 'INVERSIONES', 60),
    ('Palabra clave: INS / INSUMOS', 'CARGO', 'INSUMO*|INS', 'GASTO_INSUMOS', 60),
    ('Palabra clave: MANT / MTTO / MANTENIMIENTO', 'CARGO', 'MANTENIMIENTO*|MANT|MTTO', 'GASTO_MANTENIMIENTO', 60),
    ('Palabra clave: PUB / PUBLICIDAD', 'CARGO', 'PUBLICIDAD|PUB', 'GASTO_PUBLICIDAD', 60),
    ('Palabra clave: GAS / GASOLINA', 'CARGO', 'GASOLINA|COMBUSTIBLE|GAS', 'GASTO_VEHICULOS', 60),
    ('Palabra clave: IMP / IMPUESTOS', 'CARGO', 'IMPUESTO*|IMP', 'GASTO_IMPUESTOS', 60),
    ('Palabra clave: NOM / NOMINA', 'CARGO', 'NOMINA*|NOM', 'SUELDOS_SALARIOS', 60),
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
        ('contabilidad', '0025_reglas_conciliacion'),
    ]

    operations = [
        migrations.RunPython(sembrar, quitar),
    ]
