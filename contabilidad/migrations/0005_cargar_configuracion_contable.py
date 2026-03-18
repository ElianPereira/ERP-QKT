# Migration to preload accounting configuration

from django.db import migrations


def cargar_configuracion_contable(apps, schema_editor):
    """Precarga la configuración de cuentas por tipo de operación."""
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    ConfiguracionContable = apps.get_model('contabilidad', 'ConfiguracionContable')
    
    # Mapeo: operación -> código SAT de la cuenta
    configuraciones = [
        # Bancos y Caja
        ('BANCO_PRINCIPAL', '102.02.01', 'Cuenta BBVA Principal'),
        ('BANCO_SECUNDARIO', '102.02.02', 'Cuenta bancaria secundaria'),
        ('CAJA', '102.01', 'Efectivo en caja'),
        
        # Ingresos Eventos (Quinta)
        ('PAGO_CLIENTE_EFECTIVO', '102.01', 'Pagos en efectivo van a Caja'),
        ('PAGO_CLIENTE_TRANSFERENCIA', '102.02.01', 'Transferencias van a Banco'),
        ('PAGO_CLIENTE_TARJETA', '102.02.01', 'Tarjetas van a Banco'),
        ('ANTICIPO_CLIENTES', '205.01', 'Anticipos recibidos'),
        ('INGRESO_EVENTOS', '401.01.01', 'Ingresos por venue y jardín'),
        ('IVA_TRASLADADO', '208.01', 'IVA cobrado a clientes'),
        
        # Ingresos Airbnb
        ('INGRESO_AIRBNB', '401.02.01', 'Ingresos hospedaje Airbnb'),
        ('RETENCION_ISR_AIRBNB', '109.03', 'ISR retenido por plataforma (4%)'),
        ('RETENCION_IVA_AIRBNB', '109.04', 'IVA retenido por plataforma (8%)'),
        ('COMISION_AIRBNB', '601.04.02', 'Comisión cobrada por Airbnb'),
        ('IMPUESTO_HOSPEDAJE', '208.04', 'Impuesto estatal al hospedaje'),
        
        # Compras y Gastos
        ('PROVEEDORES', '202.01', 'Cuentas por pagar a proveedores'),
        ('IVA_ACREDITABLE', '108.01', 'IVA de compras acreditable'),
        ('GASTOS_GENERALES', '601.02.05', 'Gastos de mantenimiento y operación'),
        ('GASTOS_BEBIDAS', '501.02', 'Costo de bebidas'),
        ('GASTOS_NOMINA_EXT', '601.01.08', 'Personal externo (meseros, cocina)'),
        
        # Nómina
        ('SUELDOS_SALARIOS', '601.01.01', 'Sueldos y salarios'),
        ('IMSS_PATRONAL', '601.01.05', 'Cuotas IMSS patrón'),
    ]
    
    for operacion, codigo_sat, descripcion in configuraciones:
        try:
            cuenta = CuentaContable.objects.get(codigo_sat=codigo_sat)
            ConfiguracionContable.objects.get_or_create(
                operacion=operacion,
                defaults={
                    'cuenta': cuenta,
                    'descripcion': descripcion,
                    'activa': True,
                }
            )
        except CuentaContable.DoesNotExist:
            # Si la cuenta no existe, saltamos esta configuración
            print(f"⚠️  Cuenta {codigo_sat} no encontrada para {operacion}")
            continue


def revertir_configuracion(apps, schema_editor):
    """Elimina la configuración precargada."""
    ConfiguracionContable = apps.get_model('contabilidad', 'ConfiguracionContable')
    ConfiguracionContable.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0004_convertir_regimenes_existentes'),
    ]

    operations = [
        migrations.RunPython(cargar_configuracion_contable, revertir_configuracion),
    ]