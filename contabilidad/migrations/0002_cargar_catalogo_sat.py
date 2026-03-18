# Generated manually - Data migration for SAT catalog

from django.db import migrations


def cargar_catalogo_sat(apps, schema_editor):
    """Carga el catálogo de cuentas SAT 2024 y unidades de negocio."""
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    UnidadNegocio = apps.get_model('contabilidad', 'UnidadNegocio')
    
    # ==========================================
    # UNIDADES DE NEGOCIO
    # ==========================================
    unidades = [
        ('QUINTA', 'Quinta Ko\'ox Tanil - Eventos', 'EMPRESARIAL'),
        ('AIRBNB', 'Hospedaje Airbnb', 'PLATAFORMAS'),
        ('OTROS', 'Otros', 'MIXTO'),
    ]
    
    for clave, nombre, regimen in unidades:
        UnidadNegocio.objects.get_or_create(
            clave=clave,
            defaults={'nombre': nombre, 'regimen_fiscal': regimen, 'activa': True}
        )
    
    # ==========================================
    # CATÁLOGO DE CUENTAS SAT 2024
    # ==========================================
    cuentas = [
        # ACTIVO
        ('100', 'Activo', 'ACTIVO', 'D', 1, None, False),
        ('102', 'Efectivo y equivalentes de efectivo', 'ACTIVO', 'D', 2, '100', False),
        ('102.01', 'Caja', 'ACTIVO', 'D', 3, '102', True),
        ('102.02', 'Bancos nacionales', 'ACTIVO', 'D', 3, '102', False),
        ('102.02.01', 'BBVA Principal', 'ACTIVO', 'D', 4, '102.02', True),
        ('102.02.02', 'Banco secundario', 'ACTIVO', 'D', 4, '102.02', True),
        ('105', 'Clientes', 'ACTIVO', 'D', 2, '100', False),
        ('105.01', 'Clientes nacionales', 'ACTIVO', 'D', 3, '105', True),
        ('106', 'Deudores diversos', 'ACTIVO', 'D', 2, '100', False),
        ('106.01', 'Funcionarios y empleados', 'ACTIVO', 'D', 3, '106', True),
        ('108', 'IVA por acreditar', 'ACTIVO', 'D', 2, '100', False),
        ('108.01', 'IVA acreditable del período', 'ACTIVO', 'D', 3, '108', True),
        ('109', 'Pagos anticipados', 'ACTIVO', 'D', 2, '100', False),
        ('109.01', 'Seguros pagados por anticipado', 'ACTIVO', 'D', 3, '109', True),
        ('109.03', 'ISR retenido por Airbnb', 'ACTIVO', 'D', 3, '109', True),
        ('109.04', 'IVA retenido por Airbnb', 'ACTIVO', 'D', 3, '109', True),
        ('115', 'Activo fijo', 'ACTIVO', 'D', 2, '100', False),
        ('115.01', 'Edificios', 'ACTIVO', 'D', 3, '115', True),
        ('115.02', 'Maquinaria y equipo', 'ACTIVO', 'D', 3, '115', True),
        ('115.03', 'Mobiliario y equipo de oficina', 'ACTIVO', 'D', 3, '115', True),
        ('115.04', 'Equipo de transporte', 'ACTIVO', 'D', 3, '115', True),
        ('115.05', 'Equipo de cómputo', 'ACTIVO', 'D', 3, '115', True),
        ('116', 'Depreciación acumulada de activo fijo', 'ACTIVO', 'A', 2, '100', False),
        ('116.01', 'Depreciación acumulada de edificios', 'ACTIVO', 'A', 3, '116', True),
        ('116.02', 'Depreciación acumulada de maquinaria', 'ACTIVO', 'A', 3, '116', True),
        ('116.03', 'Depreciación acumulada de mobiliario', 'ACTIVO', 'A', 3, '116', True),
        ('116.04', 'Depreciación acumulada de equipo de transporte', 'ACTIVO', 'A', 3, '116', True),
        ('116.05', 'Depreciación acumulada de equipo de cómputo', 'ACTIVO', 'A', 3, '116', True),
        
        # PASIVO
        ('200', 'Pasivo', 'PASIVO', 'A', 1, None, False),
        ('201', 'Pasivo a corto plazo', 'PASIVO', 'A', 2, '200', False),
        ('202', 'Proveedores', 'PASIVO', 'A', 2, '200', False),
        ('202.01', 'Proveedores nacionales', 'PASIVO', 'A', 3, '202', True),
        ('205', 'Acreedores diversos', 'PASIVO', 'A', 2, '200', False),
        ('205.01', 'Anticipos de clientes', 'PASIVO', 'A', 3, '205', True),
        ('205.02', 'Otros acreedores diversos', 'PASIVO', 'A', 3, '205', True),
        ('208', 'Impuestos por pagar', 'PASIVO', 'A', 2, '200', False),
        ('208.01', 'IVA trasladado', 'PASIVO', 'A', 3, '208', True),
        ('208.02', 'ISR retenido', 'PASIVO', 'A', 3, '208', True),
        ('208.03', 'IVA retenido', 'PASIVO', 'A', 3, '208', True),
        ('208.04', 'Impuesto al hospedaje por pagar', 'PASIVO', 'A', 3, '208', True),
        ('208.05', 'ISR del ejercicio por pagar', 'PASIVO', 'A', 3, '208', True),
        ('210', 'Contribuciones por pagar', 'PASIVO', 'A', 2, '200', False),
        ('210.01', 'IMSS por pagar', 'PASIVO', 'A', 3, '210', True),
        ('210.02', 'SAR e INFONAVIT por pagar', 'PASIVO', 'A', 3, '210', True),
        ('210.03', 'Impuesto estatal sobre nómina', 'PASIVO', 'A', 3, '210', True),
        
        # CAPITAL
        ('300', 'Capital contable', 'CAPITAL', 'A', 1, None, False),
        ('301', 'Capital contribuido', 'CAPITAL', 'A', 2, '300', False),
        ('301.01', 'Capital social', 'CAPITAL', 'A', 3, '301', True),
        ('301.02', 'Aportaciones para futuros aumentos de capital', 'CAPITAL', 'A', 3, '301', True),
        ('302', 'Capital ganado', 'CAPITAL', 'A', 2, '300', False),
        ('302.01', 'Utilidades acumuladas', 'CAPITAL', 'A', 3, '302', True),
        ('302.02', 'Pérdidas acumuladas', 'CAPITAL', 'D', 3, '302', True),
        ('302.03', 'Utilidad del ejercicio', 'CAPITAL', 'A', 3, '302', True),
        ('302.04', 'Pérdida del ejercicio', 'CAPITAL', 'D', 3, '302', True),
        
        # INGRESOS
        ('400', 'Ingresos', 'INGRESO', 'A', 1, None, False),
        ('401', 'Ingresos por actividades primarias', 'INGRESO', 'A', 2, '400', False),
        ('401.01', 'Ingresos por servicios de eventos', 'INGRESO', 'A', 3, '401', False),
        ('401.01.01', 'Servicios de venue y jardín', 'INGRESO', 'A', 4, '401.01', True),
        ('401.01.02', 'Servicios de banquete', 'INGRESO', 'A', 4, '401.01', True),
        ('401.01.03', 'Servicios de barra', 'INGRESO', 'A', 4, '401.01', True),
        ('401.01.04', 'Servicios adicionales', 'INGRESO', 'A', 4, '401.01', True),
        ('401.02', 'Ingresos por hospedaje Airbnb', 'INGRESO', 'A', 3, '401', False),
        ('401.02.01', 'Hospedaje Habitación 1', 'INGRESO', 'A', 4, '401.02', True),
        ('401.02.02', 'Hospedaje Habitación 2', 'INGRESO', 'A', 4, '401.02', True),
        ('401.02.03', 'Hospedaje Casa Completa', 'INGRESO', 'A', 4, '401.02', True),
        ('402', 'Otros ingresos', 'INGRESO', 'A', 2, '400', False),
        ('402.01', 'Ingresos financieros', 'INGRESO', 'A', 3, '402', True),
        ('402.02', 'Otros ingresos', 'INGRESO', 'A', 3, '402', True),
        
        # COSTOS
        ('500', 'Costos', 'COSTO', 'D', 1, None, False),
        ('501', 'Costo de ventas', 'COSTO', 'D', 2, '500', False),
        ('501.01', 'Costo de alimentos', 'COSTO', 'D', 3, '501', True),
        ('501.02', 'Costo de bebidas', 'COSTO', 'D', 3, '501', True),
        ('501.03', 'Costo de servicios de eventos', 'COSTO', 'D', 3, '501', True),
        
        # GASTOS
        ('600', 'Gastos', 'GASTO', 'D', 1, None, False),
        ('601', 'Gastos generales', 'GASTO', 'D', 2, '600', False),
        ('601.01', 'Gastos de personal', 'GASTO', 'D', 3, '601', False),
        ('601.01.01', 'Sueldos y salarios', 'GASTO', 'D', 4, '601.01', True),
        ('601.01.02', 'Gratificaciones', 'GASTO', 'D', 4, '601.01', True),
        ('601.01.03', 'Prima vacacional', 'GASTO', 'D', 4, '601.01', True),
        ('601.01.04', 'Aguinaldo', 'GASTO', 'D', 4, '601.01', True),
        ('601.01.05', 'IMSS patronal', 'GASTO', 'D', 4, '601.01', True),
        ('601.01.06', 'SAR e INFONAVIT', 'GASTO', 'D', 4, '601.01', True),
        ('601.01.07', 'Impuesto estatal sobre nómina', 'GASTO', 'D', 4, '601.01', True),
        ('601.01.08', 'Personal externo (meseros, cocina)', 'GASTO', 'D', 4, '601.01', True),
        ('601.02', 'Gastos de operación', 'GASTO', 'D', 3, '601', False),
        ('601.02.01', 'Energía eléctrica', 'GASTO', 'D', 4, '601.02', True),
        ('601.02.02', 'Agua', 'GASTO', 'D', 4, '601.02', True),
        ('601.02.03', 'Gas', 'GASTO', 'D', 4, '601.02', True),
        ('601.02.04', 'Teléfono e internet', 'GASTO', 'D', 4, '601.02', True),
        ('601.02.05', 'Mantenimiento y reparaciones', 'GASTO', 'D', 4, '601.02', True),
        ('601.02.06', 'Jardinería', 'GASTO', 'D', 4, '601.02', True),
        ('601.02.07', 'Limpieza', 'GASTO', 'D', 4, '601.02', True),
        ('601.02.08', 'Seguridad', 'GASTO', 'D', 4, '601.02', True),
        ('601.02.09', 'Seguros y fianzas', 'GASTO', 'D', 4, '601.02', True),
        ('601.02.10', 'Gastos de vehículos', 'GASTO', 'D', 4, '601.02', True),
        ('601.03', 'Gastos de administración', 'GASTO', 'D', 3, '601', False),
        ('601.03.01', 'Papelería y útiles de oficina', 'GASTO', 'D', 4, '601.03', True),
        ('601.03.02', 'Honorarios profesionales', 'GASTO', 'D', 4, '601.03', True),
        ('601.03.03', 'Servicios contables', 'GASTO', 'D', 4, '601.03', True),
        ('601.03.04', 'Comisiones bancarias', 'GASTO', 'D', 4, '601.03', True),
        ('601.03.05', 'Software y suscripciones', 'GASTO', 'D', 4, '601.03', True),
        ('601.04', 'Gastos de venta', 'GASTO', 'D', 3, '601', False),
        ('601.04.01', 'Publicidad y promoción', 'GASTO', 'D', 4, '601.04', True),
        ('601.04.02', 'Comisiones Airbnb', 'GASTO', 'D', 4, '601.04', True),
        ('601.04.03', 'Fotografía y video', 'GASTO', 'D', 4, '601.04', True),
        ('602', 'Gastos financieros', 'GASTO', 'D', 2, '600', False),
        ('602.01', 'Intereses pagados', 'GASTO', 'D', 3, '602', True),
        ('602.02', 'Pérdida cambiaria', 'GASTO', 'D', 3, '602', True),
        ('603', 'Depreciación del ejercicio', 'GASTO', 'D', 2, '600', False),
        ('603.01', 'Depreciación de edificios', 'GASTO', 'D', 3, '603', True),
        ('603.02', 'Depreciación de maquinaria y equipo', 'GASTO', 'D', 3, '603', True),
        ('603.03', 'Depreciación de mobiliario', 'GASTO', 'D', 3, '603', True),
        ('603.04', 'Depreciación de equipo de transporte', 'GASTO', 'D', 3, '603', True),
        ('603.05', 'Depreciación de equipo de cómputo', 'GASTO', 'D', 3, '603', True),
    ]
    
    # Crear cuentas en orden (para que los padres existan primero)
    cuentas_creadas = {}
    
    for codigo, nombre, tipo, naturaleza, nivel, padre_codigo, permite_mov in cuentas:
        padre = cuentas_creadas.get(padre_codigo) if padre_codigo else None
        
        cuenta, _ = CuentaContable.objects.get_or_create(
            codigo_sat=codigo,
            defaults={
                'nombre': nombre,
                'tipo': tipo,
                'naturaleza': naturaleza,
                'nivel': nivel,
                'padre': padre,
                'permite_movimientos': permite_mov,
                'activa': True,
            }
        )
        cuentas_creadas[codigo] = cuenta


def reverse_catalogo(apps, schema_editor):
    """Revierte la carga del catálogo (vacía las tablas)."""
    CuentaContable = apps.get_model('contabilidad', 'CuentaContable')
    UnidadNegocio = apps.get_model('contabilidad', 'UnidadNegocio')
    
    CuentaContable.objects.all().delete()
    UnidadNegocio.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(cargar_catalogo_sat, reverse_catalogo),
    ]