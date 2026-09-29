# Revolut como cuenta pagadora (Issue #341): BBVA solo recibe cobros y
# Revolut paga todo. Esta migración deja lo que el ERP necesita para
# conciliarla; la CuentaBancaria de Revolut (con su CLABE) se da de alta en el
# admin, porque los números de cuenta del propietario no van en el repo.
#
# - 102.02.03 Revolut, para ligarla a esa CuentaBancaria.
# - INGRESOS_FINANCIEROS → 402.01 Ingresos financieros (intereses del ahorro).
# - Reglas de sistema para las suscripciones sin CFDI que se pagan desde
#   Revolut (a Gastos no deducibles, decisión del propietario mientras el
#   contador no indique otra cuenta), Meta a Publicidad y los intereses.
# - Textos de traspaso de la cuenta BBVA existente: BBVA imprime los SPEI
#   de/para Revolut como «STP» (Revolut liquida por STP), pegado al tipo
#   («SPEI RECIBIDOSTP»).
#
# Idempotente y conservadora, igual que 0026-0029: no crea lo que ya existe
# ni sobrescribe lo capturado en el admin.

from django.db import migrations

CUENTAS = [
    ('102.02.03', 'Revolut', 'ACTIVO', 'D', 4, '102.02'),
]
CONFIGURACION = [
    ('INGRESOS_FINANCIEROS', '402.01'),
]
REGLAS = [
    ('Suscripción Railway (sin CFDI)', 'CARGO', 'RAILWAY', 'GASTO_NO_DEDUCIBLE', 70),
    ('Suscripción Anthropic / Claude (sin CFDI)', 'CARGO', 'ANTHROPIC*|CLAUDE', 'GASTO_NO_DEDUCIBLE', 70),
    ('Suscripción Cloudflare (sin CFDI)', 'CARGO', 'CLOUDFLARE*', 'GASTO_NO_DEDUCIBLE', 70),
    ('Publicidad Meta / Facebook', 'CARGO', 'FACEBOOK|FACEBK|META', 'GASTO_PUBLICIDAD', 70),
    ('Intereses del ahorro Revolut', 'ABONO', 'REVOLUT INTERESES*', 'INGRESOS_FINANCIEROS', 20),
]
TEXTOS_TRASPASO_BBVA = '*STP|REVOLUT'
DESCRIPCION = 'Revolut como cuenta pagadora (Issue #341)'


def crear(apps, schema_editor):
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    ConfiguracionContable = apps.get_model('contabilidad', 'ConfiguracionContable')
    ReglaConciliacion = apps.get_model('contabilidad', 'ReglaConciliacion')
    CuentaBancaria = apps.get_model('contabilidad', 'CuentaBancaria')

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
        cuenta = CuentaContable.objects.filter(codigo_sat=codigo, permite_movimientos=True).first()
        if cuenta is None:
            print(f"⚠️  Cuenta {codigo} no encontrada: {operacion} queda sin configurar")
            continue
        ConfiguracionContable.objects.create(
            operacion=operacion, cuenta=cuenta, descripcion=DESCRIPCION, activa=True,
        )

    for nombre, tipo, patrones, operacion, prioridad in REGLAS:
        ReglaConciliacion.objects.get_or_create(
            nombre=nombre, origen='SISTEMA',
            defaults={
                'tipo_movimiento': tipo, 'patrones': patrones, 'operacion': operacion,
                'prioridad': prioridad, 'aplicar_automaticamente': True,
            },
        )

    CuentaBancaria.objects.filter(banco__icontains='BBVA', textos_traspaso='').update(
        textos_traspaso=TEXTOS_TRASPASO_BBVA,
    )


def revertir(apps, schema_editor):
    apps.get_model('contabilidad', 'ConfiguracionContable').objects.filter(descripcion=DESCRIPCION).delete()
    apps.get_model('contabilidad', 'ReglaConciliacion').objects.filter(
        origen='SISTEMA', nombre__in=[r[0] for r in REGLAS],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0031_cuenta_pagadora_y_traspasos'),
    ]

    operations = [
        migrations.RunPython(crear, revertir),
    ]
