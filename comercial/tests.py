"""
Tests del módulo Comercial
==========================
Ejecutar: python manage.py test comercial --verbosity=2
"""
from decimal import Decimal
from datetime import date, timedelta
from django.test import TestCase
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User

from comercial.models import (
    Cliente, Cotizacion, ItemCotizacion, Pago, Insumo,
    ConstanteSistema, MovimientoInventario, PlanPago, ParcialidadPago
)
from comercial.services import PlanPagosService


class CotizacionTotalesTest(TestCase):
    """Verifica que los totales se calculen correctamente."""
    
    def setUp(self):
        self.user = User.objects.create_user('test', password='test')
        self.cliente = Cliente.objects.create(nombre='Cliente Test', tipo_persona='FISICA')
    
    def _crear_cotizacion_limpia(self, **kwargs):
        """Crea cotización sin disparar auto-cálculo de barra."""
        defaults = {
            'cliente': self.cliente,
            'nombre_evento': 'Test',
            'fecha_evento': date.today() + timedelta(days=90),
            'num_personas': 100,
            'incluye_refrescos': False,
            'incluye_cerveza': False,
            'incluye_licor_nacional': False,
            'incluye_licor_premium': False,
            'incluye_cocteleria_basica': False,
            'incluye_cocteleria_premium': False,
        }
        defaults.update(kwargs)
        return Cotizacion.objects.create(**defaults)
    
    def test_cotizacion_sin_items_precio_cero(self):
        cot = self._crear_cotizacion_limpia()
        cot.calcular_totales()
        self.assertEqual(cot.precio_final, Decimal('0.00'))
    
    def test_cotizacion_con_items_calcula_subtotal(self):
        cot = self._crear_cotizacion_limpia()
        ItemCotizacion.objects.create(
            cotizacion=cot, descripcion='Renta de salón',
            cantidad=1, precio_unitario=Decimal('15000.00')
        )
        ItemCotizacion.objects.create(
            cotizacion=cot, descripcion='Barra',
            cantidad=1, precio_unitario=Decimal('8000.00')
        )
        cot.calcular_totales()
        self.assertEqual(cot.subtotal, Decimal('23000.00'))
    
    def test_cotizacion_sin_factura_iva_cero(self):
        cot = self._crear_cotizacion_limpia(requiere_factura=False)
        ItemCotizacion.objects.create(
            cotizacion=cot, descripcion='Renta',
            cantidad=1, precio_unitario=Decimal('10000.00')
        )
        cot.calcular_totales()
        self.assertEqual(cot.iva, Decimal('0.00'))
        self.assertEqual(cot.retencion_isr, Decimal('0.00'))
        self.assertEqual(cot.precio_final, Decimal('10000.00'))
    
    def test_cotizacion_con_factura_persona_fisica(self):
        self.cliente.tipo_persona = 'FISICA'
        self.cliente.save()
        cot = self._crear_cotizacion_limpia(requiere_factura=True)
        ItemCotizacion.objects.create(
            cotizacion=cot, descripcion='Servicio',
            cantidad=1, precio_unitario=Decimal('10000.00')
        )
        cot.calcular_totales()
        self.assertEqual(cot.iva, Decimal('1600.00'))
        self.assertEqual(cot.retencion_isr, Decimal('0.00'))
        self.assertEqual(cot.precio_final, Decimal('11600.00'))
    
    def test_cotizacion_con_factura_persona_moral(self):
        self.cliente.tipo_persona = 'MORAL'
        self.cliente.save()
        cot = self._crear_cotizacion_limpia(requiere_factura=True)
        ItemCotizacion.objects.create(
            cotizacion=cot, descripcion='Servicio',
            cantidad=1, precio_unitario=Decimal('10000.00')
        )
        cot.calcular_totales()
        self.assertEqual(cot.iva, Decimal('1600.00'))
        self.assertEqual(cot.retencion_isr, Decimal('125.00'))
        self.assertEqual(cot.precio_final, Decimal('11475.00'))
    
    def test_cotizacion_con_descuento(self):
        cot = self._crear_cotizacion_limpia(requiere_factura=True, descuento=Decimal('2000.00'))
        ItemCotizacion.objects.create(
            cotizacion=cot, descripcion='Servicio',
            cantidad=1, precio_unitario=Decimal('10000.00')
        )
        cot.calcular_totales()
        # Base = 10000 - 2000 = 8000, IVA = 1280
        self.assertEqual(cot.iva, Decimal('1280.00'))
        self.assertEqual(cot.precio_final, Decimal('9280.00'))


class PagoValidacionTest(TestCase):
    """Verifica la validación de sobrepago."""
    
    def setUp(self):
        self.user = User.objects.create_user('test', password='test')
        self.cliente = Cliente.objects.create(nombre='Cliente Test')
        # Usar update() para fijar precio_final sin que save() lo recalcule
        self.cot = Cotizacion.objects.create(
            cliente=self.cliente,
            nombre_evento='Evento Test',
            fecha_evento=date.today() + timedelta(days=60),
            incluye_refrescos=False, incluye_cerveza=False,
            incluye_licor_nacional=False, incluye_licor_premium=False,
            incluye_cocteleria_basica=False, incluye_cocteleria_premium=False,
        )
        # Forzar precio_final directo en DB
        Cotizacion.objects.filter(pk=self.cot.pk).update(precio_final=Decimal('20000.00'))
        self.cot.refresh_from_db()
    
    def test_pago_dentro_del_saldo(self):
        pago = Pago(cotizacion=self.cot, monto=Decimal('5000.00'), metodo='EFECTIVO')
        pago.clean()
    
    def test_pago_exacto_al_saldo(self):
        pago = Pago(cotizacion=self.cot, monto=Decimal('20000.00'), metodo='EFECTIVO')
        pago.clean()
    
    def test_sobrepago_rechazado(self):
        Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('15000.00'),
            metodo='EFECTIVO', usuario=self.user
        )
        pago2 = Pago(cotizacion=self.cot, monto=Decimal('6000.00'), metodo='EFECTIVO')
        with self.assertRaises(ValidationError):
            pago2.clean()
    
    def test_tolerancia_50_centavos(self):
        Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('19999.80'),
            metodo='EFECTIVO', usuario=self.user
        )
        pago2 = Pago(cotizacion=self.cot, monto=Decimal('0.50'), metodo='EFECTIVO')
        pago2.clean()  # Diferencia = 0.30, dentro de tolerancia


class TransicionEstadosTest(TestCase):
    """Verifica la máquina de estados de cotización."""
    
    def setUp(self):
        self.user = User.objects.create_user('test', password='test')
        self.cliente = Cliente.objects.create(nombre='Cliente Test')
        self.cot = Cotizacion.objects.create(
            cliente=self.cliente,
            nombre_evento='Evento',
            fecha_evento=date.today() + timedelta(days=90),
            estado='BORRADOR',
            incluye_refrescos=False, incluye_cerveza=False,
            incluye_licor_nacional=False, incluye_licor_premium=False,
            incluye_cocteleria_basica=False, incluye_cocteleria_premium=False,
        )
        Cotizacion.objects.filter(pk=self.cot.pk).update(precio_final=Decimal('10000.00'))
        self.cot.refresh_from_db()
        ItemCotizacion.objects.create(
            cotizacion=self.cot, descripcion='Item',
            cantidad=1, precio_unitario=Decimal('10000.00')
        )
    
    def test_borrador_a_cotizada(self):
        ok, msg = self.cot.cambiar_estado('COTIZADA', self.user)
        self.assertTrue(ok)
        self.assertEqual(self.cot.estado, 'COTIZADA')
    
    def test_borrador_a_confirmada_directo_bloqueado(self):
        ok, msg = self.cot.cambiar_estado('CONFIRMADA', self.user)
        self.assertFalse(ok)
        self.assertEqual(self.cot.estado, 'BORRADOR')
    
    def test_cancelacion_requiere_motivo(self):
        ok, msg = self.cot.cambiar_estado('CANCELADA', self.user, motivo='')
        self.assertFalse(ok)
    
    def test_cancelacion_con_motivo(self):
        ok, msg = self.cot.cambiar_estado('CANCELADA', self.user, motivo='Cliente desistió')
        self.assertTrue(ok)
        self.assertEqual(self.cot.motivo_cancelacion, 'Cliente desistió')
    
    def test_cerrada_requiere_pago_completo(self):
        self.cot.estado = 'EJECUTADA'
        self.cot.save(update_fields=['estado'])
        ok, msg = self.cot.cambiar_estado('CERRADA', self.user)
        self.assertFalse(ok)
        self.assertIn('saldo pendiente', msg.lower())
    
    def test_anticipo_minimo_para_confirmar(self):
        ConstanteSistema.objects.create(
            clave='PORCENTAJE_ANTICIPO_MINIMO', valor=30, descripcion='Test'
        )
        self.cot.estado = 'COTIZADA'
        self.cot.save(update_fields=['estado'])
        ok, msg = self.cot.cambiar_estado('CONFIRMADA', self.user)
        self.assertFalse(ok)
        self.assertIn('anticipo', msg.lower())


class PlanPagosTest(TestCase):
    """Verifica la generación de planes de pago."""
    
    def setUp(self):
        self.user = User.objects.create_user('test', password='test')
        self.cliente = Cliente.objects.create(nombre='Cliente Test')
    
    def _crear_cotizacion(self, dias_anticipacion, precio=Decimal('20000.00')):
        cot = Cotizacion.objects.create(
            cliente=self.cliente,
            nombre_evento='Evento',
            fecha_evento=date.today() + timedelta(days=dias_anticipacion),
            incluye_refrescos=False, incluye_cerveza=False,
            incluye_licor_nacional=False, incluye_licor_premium=False,
            incluye_cocteleria_basica=False, incluye_cocteleria_premium=False,
        )
        Cotizacion.objects.filter(pk=cot.pk).update(precio_final=precio)
        cot.refresh_from_db()
        return cot
    
    def test_plan_largo_4_parcialidades(self):
        cot = self._crear_cotizacion(150)
        plan = PlanPagosService(cot).generar(usuario=self.user)
        self.assertEqual(plan.parcialidades.count(), 4)
    
    def test_plan_medio_3_parcialidades(self):
        cot = self._crear_cotizacion(90)
        plan = PlanPagosService(cot).generar(usuario=self.user)
        self.assertEqual(plan.parcialidades.count(), 3)
    
    def test_plan_corto_2_parcialidades(self):
        cot = self._crear_cotizacion(45)
        plan = PlanPagosService(cot).generar(usuario=self.user)
        self.assertEqual(plan.parcialidades.count(), 2)
    
    def test_plan_urgente_2_parcialidades(self):
        cot = self._crear_cotizacion(20)
        plan = PlanPagosService(cot).generar(usuario=self.user)
        self.assertEqual(plan.parcialidades.count(), 2)
    
    def test_suma_parcialidades_igual_precio_final(self):
        cot = self._crear_cotizacion(150, Decimal('25208.63'))
        plan = PlanPagosService(cot).generar(usuario=self.user)
        total = sum(p.monto for p in plan.parcialidades.all())
        self.assertEqual(total, cot.precio_final)
    
    def test_parcialidades_personalizadas(self):
        cot = self._crear_cotizacion(150)
        plan = PlanPagosService(cot).generar(usuario=self.user, num_parcialidades=6)
        self.assertEqual(plan.parcialidades.count(), 6)
    
    def test_suma_personalizada_exacta(self):
        cot = self._crear_cotizacion(150, Decimal('19042.25'))
        plan = PlanPagosService(cot).generar(usuario=self.user, num_parcialidades=5)
        total = sum(p.monto for p in plan.parcialidades.all())
        self.assertEqual(total, cot.precio_final)
    
    def test_regenerar_desactiva_anterior(self):
        """Generar un nuevo plan elimina el anterior (OneToOne)."""
        cot = self._crear_cotizacion(90)
        servicio = PlanPagosService(cot)
        plan1 = servicio.generar(usuario=self.user)
        plan1_id = plan1.id
        plan2 = servicio.generar(usuario=self.user, num_parcialidades=4)
        
        # plan1 ya no debe existir (fue eliminado por el OneToOne)
        self.assertFalse(PlanPago.objects.filter(id=plan1_id, activo=True).exists())
        self.assertTrue(plan2.activo)
    
    def test_ultimo_pago_antes_del_evento(self):
        cot = self._crear_cotizacion(90)
        plan = PlanPagosService(cot).generar(usuario=self.user)
        ultima = plan.parcialidades.last()
        dias_antes = (cot.fecha_evento - ultima.fecha_limite).days
        self.assertGreaterEqual(dias_antes, 15)


class MovimientoInventarioTest(TestCase):
    """Verifica movimientos de inventario."""
    
    def setUp(self):
        self.user = User.objects.create_user('test', password='test')
        self.insumo = Insumo.objects.create(
            nombre='Hielo 20kg', unidad_medida='Bolsa',
            costo_unitario=Decimal('90.00'), cantidad_stock=Decimal('10.00')
        )
    
    def test_entrada_suma_stock(self):
        mov = MovimientoInventario(
            insumo=self.insumo, tipo='ENTRADA', cantidad=Decimal('5.00'),
            created_by=self.user
        )
        mov.save()
        self.insumo.refresh_from_db()
        self.assertEqual(self.insumo.cantidad_stock, Decimal('15.00'))
    
    def test_salida_resta_stock(self):
        mov = MovimientoInventario(
            insumo=self.insumo, tipo='SALIDA', cantidad=Decimal('3.00'),
            created_by=self.user
        )
        mov.save()
        self.insumo.refresh_from_db()
        self.assertEqual(self.insumo.cantidad_stock, Decimal('7.00'))
    
    def test_salida_excesiva_rechazada(self):
        mov = MovimientoInventario(
            insumo=self.insumo, tipo='SALIDA', cantidad=Decimal('50.00'),
            created_by=self.user
        )
        with self.assertRaises(ValidationError):
            mov.clean()
    
    def test_auditoria_stock_anterior_posterior(self):
        mov = MovimientoInventario(
            insumo=self.insumo, tipo='ENTRADA', cantidad=Decimal('5.00'),
            created_by=self.user
        )
        mov.save()
        self.assertEqual(mov.stock_anterior, Decimal('10.00'))
        self.assertEqual(mov.stock_posterior, Decimal('15.00'))