"""
Reglas de sistema para asentar solos los movimientos bancarios sin documento
(Issue #329). Solo patrones genéricos de BBVA y del concepto «TRASPASO»; las
cuentas propias del dueño se aprenden desde «Clasificar» o se capturan en el
admin, nunca en el repo.

Las reglas apuntan a una operación de ConfiguracionContable: mientras esa
operación no tenga cuenta asignada, la regla calza pero no asienta nada (el
movimiento queda pendiente y la acción lo avisa).
"""
from django.db import migrations

REGLAS = [
    ('IVA de comisión bancaria', 'CARGO', 'IVA COM', 'IVA_ACREDITABLE', 10),
    ('Comisión bancaria', 'CARGO', 'SERV BANCA INTERNET|COM SERV BCA INTERNET|COMISION', 'GASTO_BANCARIOS', 20),
    ('Traspaso a cuenta propia (retiro del dueño)', 'CARGO', 'TRASPAS|RETIRO', 'RETIROS_DUENO', 90),
    ('Traspaso desde cuenta propia (aportación del dueño)', 'ABONO', 'TRASPAS', 'APORTACIONES_DUENO', 90),
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
