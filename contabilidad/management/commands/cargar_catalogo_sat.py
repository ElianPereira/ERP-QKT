"""
Comando para cargar el Catálogo de Cuentas SAT 2024
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from contabilidad.models import CuentaContable, UnidadNegocio, ConfiguracionContable


class Command(BaseCommand):
    help = 'Carga el catálogo de cuentas SAT 2024 y configuración inicial'

    def handle(self, *args, **options):
        self.stdout.write("Cargando catálogo de cuentas SAT 2024...")
        
        with transaction.atomic():
            self._cargar_catalogo()
            self._cargar_unidades_negocio()
        
        self.stdout.write(self.style.SUCCESS("✓ Catálogo cargado exitosamente"))

    def _cargar_catalogo(self):
        cuentas = [
            # ACTIVO
            ('100', 'Activo', 'ACTIVO', 'D', 1, None),
            ('102', 'Efectivo y equivalentes de efectivo', 'ACTIVO', 'D', 2, '100'),
            ('102.01', 'Caja', 'ACTIVO', 'D', 3, '102'),
            ('102.02', 'Bancos nacionales', 'ACTIVO', 'D', 3, '102'),
            ('102.02.01', 'BBVA Principal', 'ACTIVO', 'D', 4, '102.02'),
            ('102.02.02', 'Banco secundario', 'ACTIVO', 'D', 4, '102.02'),
            ('105', 'Clientes', 'ACTIVO', 'D', 2, '100'),
            ('105.01', 'Clientes nacionales', 'ACTIVO', 'D', 3, '105'),
            ('108', 'IVA por acreditar', 'ACTIVO', 'D', 2, '100'),
            ('108.01', 'IVA acreditable del período', 'ACTIVO', 'D', 3, '108'),
            ('109', 'Pagos anticipados', 'ACTIVO', 'D', 2, '100'),
            ('109.03', 'ISR retenido por Airbnb', 'ACTIVO', 'D', 3, '109'),
            ('109.04', 'IVA retenido por Airbnb', 'ACTIVO', 'D', 3, '109'),
            
            # PASIVO
            ('200', 'Pasivo', 'PASIVO', 'A', 1, None),
            ('202', 'Proveedores', 'PASIVO', 'A', 2, '200'),
            ('202.01', 'Proveedores nacionales', 'PASIVO', 'A', 3, '202'),
            ('205', 'Acreedores diversos', 'PASIVO', 'A', 2, '200'),
            ('205.01', 'Anticipos de clientes', 'PASIVO', 'A', 3, '205'),
            ('208', 'Impuestos por pagar', 'PASIVO', 'A', 2, '200'),
            ('208.01', 'IVA trasladado', 'PASIVO', 'A', 3, '208'),
            ('208.04', 'Impuesto al hospedaje por pagar', 'PASIVO', 'A', 3, '208'),
            
            # CAPITAL
            ('300', 'Capital contable', 'CAPITAL', 'A', 1, None),
            ('301', 'Capital contribuido', 'CAPITAL', 'A', 2, '300'),
            ('301.01', 'Capital social', 'CAPITAL', 'A', 3, '301'),
            ('302', 'Capital ganado', 'CAPITAL', 'A', 2, '300'),
            ('302.01', 'Utilidades acumuladas', 'CAPITAL', 'A', 3, '302'),
            ('302.03', 'Utilidad del ejercicio', 'CAPITAL', 'A', 3, '302'),
            
            # INGRESOS
            ('400', 'Ingresos', 'INGRESO', 'A', 1, None),
            ('401', 'Ingresos por actividades primarias', 'INGRESO', 'A', 2, '400'),
            ('401.01', 'Ingresos por servicios de eventos', 'INGRESO', 'A', 3, '401'),
            ('401.02', 'Ingresos por hospedaje Airbnb', 'INGRESO', 'A', 3, '401'),
            
            # COSTOS
            ('500', 'Costos', 'COSTO', 'D', 1, None),
            ('501', 'Costo de ventas', 'COSTO', 'D', 2, '500'),
            ('501.01', 'Costo de alimentos', 'COSTO', 'D', 3, '501'),
            ('501.02', 'Costo de bebidas', 'COSTO', 'D', 3, '501'),
            
            # GASTOS
            ('600', 'Gastos', 'GASTO', 'D', 1, None),
            ('601', 'Gastos generales', 'GASTO', 'D', 2, '600'),
            ('601.01', 'Gastos de personal', 'GASTO', 'D', 3, '601'),
            ('601.01.01', 'Sueldos y salarios', 'GASTO', 'D', 4, '601.01'),
            ('601.01.08', 'Personal externo', 'GASTO', 'D', 4, '601.01'),
            ('601.02', 'Gastos de operación', 'GASTO', 'D', 3, '601'),
            ('601.04', 'Gastos de venta', 'GASTO', 'D', 3, '601'),
            ('601.04.02', 'Comisiones Airbnb', 'GASTO', 'D', 4, '601.04'),
        ]
        
        cuentas_creadas = {}
        
        for codigo, nombre, tipo, naturaleza, nivel, padre_codigo in cuentas:
            padre = cuentas_creadas.get(padre_codigo) if padre_codigo else None
            permite_mov = nivel >= 3
            
            cuenta, created = CuentaContable.objects.update_or_create(
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
            
            if created:
                self.stdout.write(f"  + {codigo} - {nombre}")
        
        self.stdout.write(f"\n  Total: {len(cuentas_creadas)} cuentas")

    def _cargar_unidades_negocio(self):
        unidades = [
            ('QUINTA', 'Quinta Ko\'ox Tanil - Eventos', 'EMPRESARIAL'),
            ('AIRBNB', 'Hospedaje Airbnb', 'PLATAFORMAS'),
            ('OTROS', 'Otros', 'MIXTO'),
        ]
        
        for clave, nombre, regimen in unidades:
            UnidadNegocio.objects.update_or_create(
                clave=clave,
                defaults={'nombre': nombre, 'regimen_fiscal': regimen, 'activa': True}
            )
            self.stdout.write(f"  + Unidad: {clave}")
