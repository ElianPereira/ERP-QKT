"""
Palabras clave para las categorías de gasto del catálogo (Issue #390) que no
tenían regla de conciliación: el concepto de la transferencia las asienta solas
en la cuenta de su categoría. Solo cargos, de palabra completa (`*` acepta
cualquier terminación). «Gas LP» va con prioridad 50 para ganarle a la regla
«GAS» de vehículos (60). Quedan sin regla a propósito «Otros gastos» y «Gastos
por clasificar»: se deciden a mano. Aprobado por el propietario tal cual
(2026-10-30).
"""
from django.db import migrations

REGLAS = [
    ('Palabra clave: Alimentos', 'ALIMENTO*|COMIDA|CARNE*|ABARROTE*', 'COSTO_ALIMENTOS', 60),
    ('Palabra clave: Bebidas', 'BEBIDA*|CERVEZA*|LICOR*|HIELO', 'GASTOS_BEBIDAS', 60),
    ('Palabra clave: Staff externo de eventos', 'STAFF|MESERO*|BARTENDER*', 'GASTOS_NOMINA_EXT', 60),
    ('Palabra clave: Renta de mobiliario para eventos', 'RENTA EVENTO*|RENTA MOB*', 'COSTO_RENTA_EVENTOS', 60),
    ('Palabra clave: Servicios subcontratados para eventos', 'SUBCONTRATO*|DJ|MARIACHI|SHOW', 'COSTO_SERVICIOS_EVENTOS', 60),
    ('Palabra clave: Energía eléctrica', 'LUZ|CFE', 'GASTO_LUZ', 60),
    ('Palabra clave: Agua', 'AGUA|PIPA*|JAPAY', 'GASTO_AGUA', 60),
    ('Palabra clave: Gas LP', 'GAS LP|GASLP', 'GASTO_GAS', 50),
    ('Palabra clave: Teléfono e internet', 'TELEFONO*|TELMEX|IZZI|INTERNET', 'GASTO_TELEFONO', 60),
    ('Palabra clave: Jardinería', 'JARDIN*|JARD|PODA', 'GASTO_JARDINERIA', 60),
    ('Palabra clave: Limpieza y desechables', 'LIMPIEZA*|LIMP|DESECHABLE*', 'GASTO_LIMPIEZA', 60),
    ('Palabra clave: Vigilancia y seguridad', 'VIGILANCIA|GUARDIA|ALARMA|CAMARAS', 'GASTO_SEGURIDAD', 60),
    ('Palabra clave: Seguros y fianzas', 'SEGURO*|FIANZA*', 'GASTO_SEGUROS', 60),
    ('Palabra clave: Papelería y oficina', 'PAPELERIA*|OFICINA', 'GASTO_OFICINA', 60),
    ('Palabra clave: Honorarios profesionales', 'HONORARIO*|NOTARIO|ABOGADO', 'GASTO_HONORARIOS', 60),
    ('Palabra clave: Servicios contables', 'CONTADOR|CONTABLE*|CONTA', 'GASTO_CONTABLES', 60),
    ('Palabra clave: Software y suscripciones', 'SOFTWARE|SUSCRIPCION*|DOMINIO|HOSTING|WORKSPACE', 'GASTO_SOFTWARE', 60),
    ('Palabra clave: Fotografía y video', 'FOTO*|VIDEO', 'GASTO_FOTOGRAFIA', 60),
    ('Palabra clave: Mobiliario y equipo (activo)', 'EQUIPO*|ACTIVO', 'ACTIVO_MOBILIARIO', 60),
    ('Palabra clave: Construcciones y mejoras (activo)', 'CONSTRUCCION*|REMODELACION*|OBRA', 'ACTIVO_CONSTRUCCIONES', 60),
]


def sembrar(apps, schema_editor):
    ReglaConciliacion = apps.get_model('contabilidad', 'ReglaConciliacion')
    for nombre, patrones, operacion, prioridad in REGLAS:
        ReglaConciliacion.objects.get_or_create(
            nombre=nombre, origen='SISTEMA',
            defaults={
                'tipo_movimiento': 'CARGO', 'patrones': patrones, 'operacion': operacion,
                'prioridad': prioridad, 'aplicar_automaticamente': True,
            },
        )


def quitar(apps, schema_editor):
    ReglaConciliacion = apps.get_model('contabilidad', 'ReglaConciliacion')
    ReglaConciliacion.objects.filter(origen='SISTEMA', nombre__in=[r[0] for r in REGLAS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0040_regla_deposito_efectivo'),
    ]

    operations = [
        migrations.RunPython(sembrar, quitar),
    ]
