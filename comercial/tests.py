"""
Tests del módulo Comercial
==========================
Ejecutar: python manage.py test comercial --verbosity=2
"""
import json
from decimal import Decimal
from datetime import date, timedelta
from django.test import TestCase
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User

from comercial.models import (
    Cliente, Cotizacion, ItemCotizacion, Pago, Insumo,
    ConstanteSistema, MovimientoInventario, PlanPago, ParcialidadPago,
    Espacio, AsignacionEspacio, AsignacionPersonal, Compra,
)
from nomina.models import Empleado
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
    
    def test_cotizacion_siempre_tiene_iva(self):
        """Todo ingreso aplica IVA 16% sin excepción."""
        cot = self._crear_cotizacion_limpia(requiere_factura=False)
        ItemCotizacion.objects.create(
            cotizacion=cot, descripcion='Renta',
            cantidad=1, precio_unitario=Decimal('10000.00')
        )
        cot.calcular_totales()
        self.assertEqual(cot.iva, Decimal('1600.00'))
        self.assertEqual(cot.precio_final, Decimal('11600.00'))
    
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

    def test_ingreso_extra_no_cuenta_para_saldo(self):
        """Un pago concepto=EXTRA no debe validarse contra el saldo de la venta."""
        Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('20000.00'),
            metodo='EFECTIVO', usuario=self.user,
        )
        # La venta ya está saldada; un ingreso EXTRA por encima no debe rechazarse.
        extra = Pago(
            cotizacion=self.cot, monto=Decimal('500.00'),
            metodo='EFECTIVO', concepto='EXTRA',
        )
        extra.clean()

    def test_ingreso_extra_no_se_suma_al_total_pagado(self):
        Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('15000.00'),
            metodo='EFECTIVO', usuario=self.user,
        )
        Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('500.00'),
            metodo='EFECTIVO', usuario=self.user, concepto='EXTRA',
        )
        self.assertEqual(self.cot.total_pagado(), Decimal('15000.00'))
        self.assertEqual(self.cot.saldo_pendiente(), Decimal('5000.00'))


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
    
    def test_ingreso_extra_no_bloquea_cierre(self):
        """Reproduce el caso real: la suma de pagos VENTA cuadra exacto con el
        total, y un ingreso EXTRA (propina) encima no debe impedir cerrar."""
        self.cot.refresh_from_db()  # el item creado en setUp recalcula precio_final (con IVA)
        Pago.objects.create(
            cotizacion=self.cot, monto=self.cot.precio_final,
            metodo='EFECTIVO', usuario=self.user,
        )
        Pago.objects.create(
            cotizacion=self.cot, monto=Decimal('14.00'),
            metodo='EFECTIVO', usuario=self.user, concepto='EXTRA',
        )
        self.cot.estado = 'EJECUTADA'
        self.cot.save(update_fields=['estado'])
        ok, msg = self.cot.cambiar_estado('CERRADA', self.user)
        self.assertTrue(ok, msg)

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

class AsignacionEspacioTest(TestCase):
    """Verifica detección de conflictos de asignación de espacios."""

    def setUp(self):
        self.user = User.objects.create_user('u', password='x')
        self.cliente = Cliente.objects.create(nombre='C')
        self.espacio = Espacio.objects.create(nombre='Jardín Principal', tipo='JARDIN', capacidad_max=100)
        self.cot1 = Cotizacion.objects.create(
            cliente=self.cliente, nombre_evento='E1',
            fecha_evento=date.today() + timedelta(days=10),
            incluye_refrescos=False, incluye_cerveza=False,
            incluye_licor_nacional=False, incluye_licor_premium=False,
            incluye_cocteleria_basica=False, incluye_cocteleria_premium=False,
        )
        self.cot2 = Cotizacion.objects.create(
            cliente=self.cliente, nombre_evento='E2',
            fecha_evento=date.today() + timedelta(days=10),
            incluye_refrescos=False, incluye_cerveza=False,
            incluye_licor_nacional=False, incluye_licor_premium=False,
            incluye_cocteleria_basica=False, incluye_cocteleria_premium=False,
        )

    def test_asignacion_simple_ok(self):
        from datetime import time
        AsignacionEspacio.objects.create(
            cotizacion=self.cot1, espacio=self.espacio,
            fecha=self.cot1.fecha_evento,
            hora_inicio=time(14, 0), hora_fin=time(22, 0),
        )
        self.assertEqual(self.espacio.asignaciones.count(), 1)

    def test_conflicto_solapamiento_rechazado(self):
        from datetime import time
        AsignacionEspacio.objects.create(
            cotizacion=self.cot1, espacio=self.espacio,
            fecha=self.cot1.fecha_evento,
            hora_inicio=time(14, 0), hora_fin=time(22, 0),
        )
        with self.assertRaises(ValidationError):
            AsignacionEspacio.objects.create(
                cotizacion=self.cot2, espacio=self.espacio,
                fecha=self.cot2.fecha_evento,
                hora_inicio=time(20, 0), hora_fin=time(23, 0),
            )

    def test_overnight_no_solapado_ok(self):
        from datetime import time
        AsignacionEspacio.objects.create(
            cotizacion=self.cot1, espacio=self.espacio,
            fecha=self.cot1.fecha_evento,
            hora_inicio=time(20, 0), hora_fin=time(2, 0),  # cruza medianoche
        )
        # Otra al día siguiente desde 8am no conflicta
        AsignacionEspacio.objects.create(
            cotizacion=self.cot2, espacio=self.espacio,
            fecha=self.cot2.fecha_evento + timedelta(days=1),
            hora_inicio=time(8, 0), hora_fin=time(14, 0),
        )

    def test_overnight_si_solapado_rechazado(self):
        from datetime import time
        AsignacionEspacio.objects.create(
            cotizacion=self.cot1, espacio=self.espacio,
            fecha=self.cot1.fecha_evento,
            hora_inicio=time(20, 0), hora_fin=time(5, 0),  # cruza medianoche
        )
        with self.assertRaises(ValidationError):
            AsignacionEspacio.objects.create(
                cotizacion=self.cot2, espacio=self.espacio,
                fecha=self.cot2.fecha_evento + timedelta(days=1),
                hora_inicio=time(3, 0), hora_fin=time(9, 0),
            )


class AsignacionPersonalTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('u', password='x')
        self.cliente = Cliente.objects.create(nombre='C')
        self.empleado = Empleado.objects.create(nombre='Juan', puesto='MESERO')
        self.cot1 = Cotizacion.objects.create(
            cliente=self.cliente, nombre_evento='E1',
            fecha_evento=date.today() + timedelta(days=10),
            incluye_refrescos=False, incluye_cerveza=False,
            incluye_licor_nacional=False, incluye_licor_premium=False,
            incluye_cocteleria_basica=False, incluye_cocteleria_premium=False,
        )
        self.cot2 = Cotizacion.objects.create(
            cliente=self.cliente, nombre_evento='E2',
            fecha_evento=date.today() + timedelta(days=10),
            incluye_refrescos=False, incluye_cerveza=False,
            incluye_licor_nacional=False, incluye_licor_premium=False,
            incluye_cocteleria_basica=False, incluye_cocteleria_premium=False,
        )

    def test_doble_asignacion_rechazada(self):
        from datetime import time
        AsignacionPersonal.objects.create(
            cotizacion=self.cot1, empleado=self.empleado, rol='MESERO',
            fecha=self.cot1.fecha_evento,
            hora_inicio=time(15, 0), hora_fin=time(23, 0),
        )
        with self.assertRaises(ValidationError):
            AsignacionPersonal.objects.create(
                cotizacion=self.cot2, empleado=self.empleado, rol='MESERO',
                fecha=self.cot2.fecha_evento,
                hora_inicio=time(18, 0), hora_fin=time(22, 0),
            )


class VentasMesIncluyeEventosCerradosTest(TestCase):
    """Regresión: un evento CERRADA y 100% pagado debe contar en Ventas Mes."""

    def test_evento_cerrada_cuenta_en_ventas_mes(self):
        from django.db.models import Sum
        from comercial.views import ESTADOS_VENTA_REAL

        cliente = Cliente.objects.create(nombre='Cliente Test')
        cot = Cotizacion.objects.create(
            cliente=cliente, nombre_evento='Evento Cerrado Test',
            fecha_evento=date.today().replace(day=15),
            estado='CERRADA',
            incluye_refrescos=False, incluye_cerveza=False,
            incluye_licor_nacional=False, incluye_licor_premium=False,
            incluye_cocteleria_basica=False, incluye_cocteleria_premium=False,
        )
        Cotizacion.objects.filter(pk=cot.pk).update(precio_final=Decimal('4000.00'))

        total = Cotizacion.objects.filter(
            estado__in=ESTADOS_VENTA_REAL,
            fecha_evento__year=date.today().year,
            fecha_evento__month=date.today().month,
        ).aggregate(total=Sum('precio_final'))['total']

        self.assertEqual(total, Decimal('4000.00'))

    def test_cancelada_borrador_cotizada_no_cuentan(self):
        from django.db.models import Sum
        from comercial.views import ESTADOS_VENTA_REAL

        cliente = Cliente.objects.create(nombre='Cliente Test 2')
        for estado in ('BORRADOR', 'COTIZADA', 'CANCELADA'):
            cot = Cotizacion.objects.create(
                cliente=cliente, nombre_evento=f'Evento {estado}',
                fecha_evento=date.today().replace(day=15),
                estado='BORRADOR',
                incluye_refrescos=False, incluye_cerveza=False,
                incluye_licor_nacional=False, incluye_licor_premium=False,
                incluye_cocteleria_basica=False, incluye_cocteleria_premium=False,
            )
            Cotizacion.objects.filter(pk=cot.pk).update(precio_final=Decimal('9999.00'), estado=estado)

        total = Cotizacion.objects.filter(
            estado__in=ESTADOS_VENTA_REAL,
            fecha_evento__year=date.today().year,
            fecha_evento__month=date.today().month,
        ).aggregate(total=Sum('precio_final'))['total']

        self.assertIsNone(total)


class DashboardGraficaFinanzasSoloAnioActualTest(TestCase):
    """Regresión: la gráfica 'Finanzas (ventas vs gastos)' debe mostrar solo
    los meses del año en curso, no el historial completo de años anteriores."""

    def setUp(self):
        self.staff = User.objects.create_user('staff_dash', password='test', is_staff=True)
        self.client.force_login(self.staff)
        self.cliente = Cliente.objects.create(nombre='Cliente Test')

    def _crear_cotizacion(self, fecha_evento, estado='CONFIRMADA'):
        cot = Cotizacion.objects.create(
            cliente=self.cliente, nombre_evento=f'Evento {fecha_evento}',
            fecha_evento=fecha_evento,
            estado='BORRADOR',
            incluye_refrescos=False, incluye_cerveza=False,
            incluye_licor_nacional=False, incluye_licor_premium=False,
            incluye_cocteleria_basica=False, incluye_cocteleria_premium=False,
        )
        Cotizacion.objects.filter(pk=cot.pk).update(precio_final=Decimal('1000.00'), estado=estado)
        return cot

    def test_grafica_excluye_meses_de_anios_anteriores(self):
        hoy = date.today()
        anio_pasado = hoy.replace(year=hoy.year - 1, day=1)
        self._crear_cotizacion(anio_pasado)
        self._crear_cotizacion(hoy.replace(day=1))

        response = self.client.get('/admin/')

        self.assertEqual(response.status_code, 200)
        anio_pasado_str = anio_pasado.strftime('%B %Y')
        anio_actual_str = hoy.replace(day=1).strftime('%B %Y')
        self.assertNotIn(anio_pasado_str, response.context['chart_labels'])
        self.assertIn(anio_actual_str, response.context['chart_labels'])

    def test_grafica_respeta_orden_cronologico_con_meses_solo_de_gastos(self):
        """Regresión: un mes que solo tuvo gastos (sin ventas) debe aparecer
        en su lugar cronológico, no al final de la lista."""
        from comercial.models import Compra
        from contabilidad.models import UnidadNegocio

        unidad_quinta, _ = UnidadNegocio.objects.get_or_create(
            clave='QUINTA', defaults={'nombre': "Quinta Ko'ox Tanil - Eventos"}
        )

        hoy = date.today()
        anio = hoy.year
        # Ventas en enero y abril; gastos (sin ventas) en julio y noviembre.
        self._crear_cotizacion(date(anio, 1, 5))
        self._crear_cotizacion(date(anio, 4, 5))
        Compra.objects.create(fecha_emision=date(anio, 7, 5), total=Decimal('100.00'), unidad_negocio=unidad_quinta)
        Compra.objects.create(fecha_emision=date(anio, 11, 5), total=Decimal('200.00'), unidad_negocio=unidad_quinta)

        response = self.client.get('/admin/')

        self.assertEqual(response.status_code, 200)
        labels = json.loads(response.context['chart_labels'])
        esperado = [
            date(anio, 1, 1).strftime('%B %Y'),
            date(anio, 4, 1).strftime('%B %Y'),
            date(anio, 7, 1).strftime('%B %Y'),
            date(anio, 11, 1).strftime('%B %Y'),
        ]
        self.assertEqual(labels, esperado)


class DashboardSeparacionElianRubyTest(TestCase):
    """Regresión: el dashboard debe separar ingresos/gastos/utilidad de
    Elián (Quinta, eventos) y Ruby (Airbnb, hospedaje) sin mezclarlos."""

    def setUp(self):
        from contabilidad.models import UnidadNegocio
        self.staff = User.objects.create_user('staff_split', password='test', is_staff=True)
        self.client.force_login(self.staff)
        self.cliente = Cliente.objects.create(nombre='Cliente Test')
        self.unidad_quinta, _ = UnidadNegocio.objects.get_or_create(
            clave='QUINTA', defaults={'nombre': "Quinta Ko'ox Tanil - Eventos"}
        )
        self.unidad_airbnb, _ = UnidadNegocio.objects.get_or_create(
            clave='AIRBNB', defaults={'nombre': 'Hospedaje Airbnb'}
        )

    def test_venta_quinta_no_afecta_kpis_de_ruby(self):
        from comercial.models import Compra
        from airbnb.models import PagoAirbnb

        hoy = date.today()
        cot = Cotizacion.objects.create(
            cliente=self.cliente, nombre_evento='Evento Quinta',
            fecha_evento=hoy.replace(day=15),
            estado='BORRADOR',
            incluye_refrescos=False, incluye_cerveza=False,
            incluye_licor_nacional=False, incluye_licor_premium=False,
            incluye_cocteleria_basica=False, incluye_cocteleria_premium=False,
        )
        Cotizacion.objects.filter(pk=cot.pk).update(precio_final=Decimal('4000.00'), estado='CONFIRMADA')
        Compra.objects.create(
            fecha_emision=hoy.replace(day=10), total=Decimal('500.00'),
            unidad_negocio=self.unidad_quinta,
        )

        pago = PagoAirbnb.objects.create(
            huesped='Huésped Test', fecha_checkin=hoy.replace(day=1),
            fecha_checkout=hoy.replace(day=3), monto_bruto=Decimal('2000.00'),
            monto_neto=Decimal('1800.00'), fecha_pago=hoy.replace(day=5),
            estado='PAGADO',
        )
        Compra.objects.create(
            fecha_emision=hoy.replace(day=12), total=Decimal('300.00'),
            unidad_negocio=self.unidad_airbnb,
        )

        response = self.client.get('/admin/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['ventas_mes_quinta'], Decimal('4000.00'))
        self.assertEqual(response.context['gastos_mes_quinta'], Decimal('500.00'))
        # monto_neto real: save() recalcula retenciones (ISR 4% / IVA 8% sobre
        # monto_bruto) ya que no se pasaron explícitas, así que el neto real
        # difiere del valor pasado a create().
        pago.refresh_from_db()
        self.assertEqual(response.context['ingresos_mes_ruby'], pago.monto_neto)
        self.assertEqual(response.context['gastos_mes_ruby'], Decimal('300.00'))

    def test_pago_airbnb_pendiente_no_cuenta_como_ingreso(self):
        from airbnb.models import PagoAirbnb

        hoy = date.today()
        PagoAirbnb.objects.create(
            huesped='Huésped Pendiente', fecha_checkin=hoy.replace(day=1),
            fecha_checkout=hoy.replace(day=3), monto_bruto=Decimal('2000.00'),
            monto_neto=Decimal('1800.00'), fecha_pago=hoy.replace(day=5),
            estado='PENDIENTE',
        )

        response = self.client.get('/admin/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['ingresos_mes_ruby'], 0)

    def test_grafica_comparte_eje_de_meses_entre_quinta_y_ruby(self):
        """Regresión: la gráfica combinada debe alinear las 4 series (ventas y
        gastos de Elián, ingresos y gastos de Ruby) sobre el mismo eje de
        meses. Un mes donde solo hubo actividad de una línea de negocio debe
        seguir apareciendo en su lugar cronológico, con 0 en las series de la
        otra línea (no debe faltarle el mes ni desalinearse con las demás)."""
        from comercial.models import Compra
        from airbnb.models import PagoAirbnb

        hoy = date.today()
        anio = hoy.year
        # Enero: solo Quinta (ventas + gastos). Marzo: solo Ruby (ingresos + gastos).
        cot = Cotizacion.objects.create(
            cliente=self.cliente, nombre_evento='Evento Enero',
            fecha_evento=date(anio, 1, 10), estado='BORRADOR',
            incluye_refrescos=False, incluye_cerveza=False,
            incluye_licor_nacional=False, incluye_licor_premium=False,
            incluye_cocteleria_basica=False, incluye_cocteleria_premium=False,
        )
        Cotizacion.objects.filter(pk=cot.pk).update(precio_final=Decimal('1000.00'), estado='CONFIRMADA')
        Compra.objects.create(fecha_emision=date(anio, 1, 12), total=Decimal('200.00'), unidad_negocio=self.unidad_quinta)

        # monto_neto real tras retenciones (ISR 4% / IVA 8% sobre monto_bruto,
        # aplicadas en PagoAirbnb.save()): 500 - 20 - 40 = 440.
        PagoAirbnb.objects.create(
            huesped='Huésped Marzo', fecha_checkin=date(anio, 3, 1),
            fecha_checkout=date(anio, 3, 3), monto_bruto=Decimal('500.00'),
            monto_neto=Decimal('500.00'), fecha_pago=date(anio, 3, 5), estado='PAGADO',
        )
        Compra.objects.create(fecha_emision=date(anio, 3, 8), total=Decimal('100.00'), unidad_negocio=self.unidad_airbnb)

        response = self.client.get('/admin/')

        self.assertEqual(response.status_code, 200)
        labels = json.loads(response.context['chart_labels'])
        esperado = [date(anio, 1, 1).strftime('%B %Y'), date(anio, 3, 1).strftime('%B %Y')]
        self.assertEqual(labels, esperado)

        ventas_quinta = json.loads(response.context['chart_ventas_quinta'])
        gastos_quinta = json.loads(response.context['chart_gastos_quinta'])
        ingresos_ruby = json.loads(response.context['chart_ingresos_ruby'])
        gastos_ruby = json.loads(response.context['chart_gastos_ruby'])

        self.assertEqual(ventas_quinta, [1000.0, 0])
        self.assertEqual(gastos_quinta, [200.0, 0])
        self.assertEqual(ingresos_ruby, [0, 440.0])
        self.assertEqual(gastos_ruby, [0, 100.0])


def _construir_cfdi(tipo='I', rfc_receptor='PECE010202IA0', uso_cfdi='G03', uuid='',
                     rfc_emisor='PRV010101ABC', nombre_emisor='Proveedor Test'):
    """CFDI 4.0 mínimo (solo lo que analizar_xml_compra/Compra.save() necesitan leer)."""
    complemento = ''
    if uuid:
        complemento = (
            '<cfdi:Complemento>'
            '<tfd:TimbreFiscalDigital xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" '
            f'UUID="{uuid}"/>'
            '</cfdi:Complemento>'
        )
    return (
        '<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" '
        f'Version="4.0" TipoDeComprobante="{tipo}" Total="1160.00" SubTotal="1000.00">'
        f'<cfdi:Emisor Rfc="{rfc_emisor}" Nombre="{nombre_emisor}"/>'
        f'<cfdi:Receptor Rfc="{rfc_receptor}" UsoCFDI="{uso_cfdi}"/>'
        f'{complemento}'
        '</cfdi:Comprobante>'
    ).encode('utf-8')


class AnalizarXmlCompraTest(TestCase):
    """La carga masiva de XML debe distinguir qué CFDIs son compras válidas
    del negocio, y por qué se excluye o se omite cada uno que no lo es."""

    def test_cfdi_valido_del_negocio(self):
        from comercial.services import analizar_xml_compra
        valido, motivo, unidad_clave, rfc_r, tipo, uso, es_duplicado = analizar_xml_compra(
            _construir_cfdi(rfc_receptor='PECE010202IA0', uso_cfdi='G03')
        )
        self.assertTrue(valido)
        self.assertIsNone(motivo)
        self.assertEqual(unidad_clave, 'QUINTA')
        self.assertFalse(es_duplicado)

    def test_cfdi_de_venta_propia_se_excluye_por_rfc_receptor(self):
        """Si el RFC receptor no es el del negocio (ej. es una factura que
        ELLOS emitieron a un cliente), no debe colarse como compra."""
        from comercial.services import analizar_xml_compra
        valido, motivo, unidad_clave, rfc_r, tipo, uso, es_duplicado = analizar_xml_compra(
            _construir_cfdi(rfc_receptor='XAXX010101000')
        )
        self.assertFalse(valido)
        self.assertFalse(es_duplicado)
        self.assertIn('no pertenece al negocio', motivo)

    def test_nota_de_credito_se_excluye_por_tipo(self):
        from comercial.services import analizar_xml_compra
        valido, motivo, unidad_clave, rfc_r, tipo, uso, es_duplicado = analizar_xml_compra(
            _construir_cfdi(tipo='E')  # Egreso = nota de crédito
        )
        self.assertFalse(valido)
        self.assertIn('no es de Ingreso', motivo)

    def test_uso_cfdi_personal_se_excluye(self):
        from comercial.services import analizar_xml_compra
        valido, motivo, unidad_clave, rfc_r, tipo, uso, es_duplicado = analizar_xml_compra(
            _construir_cfdi(uso_cfdi='D01')  # honorarios médicos, deducción personal
        )
        self.assertFalse(valido)
        self.assertIn('deducción personal', motivo)

    def test_factura_duplicada_se_detecta_por_uuid(self):
        from comercial.services import analizar_xml_compra
        from contabilidad.models import UnidadNegocio

        unidad, _ = UnidadNegocio.objects.get_or_create(clave='QUINTA', defaults={'nombre': 'Quinta Test'})
        Compra.objects.create(unidad_negocio=unidad, uuid='AAAA-1111-BBBB-2222')

        valido, motivo, unidad_clave, rfc_r, tipo, uso, es_duplicado = analizar_xml_compra(
            _construir_cfdi(uuid='AAAA-1111-BBBB-2222')
        )
        self.assertFalse(valido)
        self.assertTrue(es_duplicado)
        self.assertIn('duplicada', motivo)

    def test_factura_nueva_con_uuid_no_es_duplicada(self):
        from comercial.services import analizar_xml_compra
        valido, motivo, unidad_clave, rfc_r, tipo, uso, es_duplicado = analizar_xml_compra(
            _construir_cfdi(uuid='CCCC-3333-DDDD-4444')
        )
        self.assertTrue(valido)
        self.assertFalse(es_duplicado)


class CompraDeteccionAutomaticaTest(TestCase):
    """Regresión: subir un XML directo en 'Compras > Añadir' (sin pasar por
    la carga masiva) también debe detectar la unidad de negocio por el RFC
    receptor, y vincular/crear el Proveedor del catálogo — no solo la
    herramienta de carga masiva."""

    def setUp(self):
        from unittest.mock import patch
        # Compra.archivo_xml usa RawMediaCloudinaryStorage, que intentaría
        # subir de verdad a Cloudinary (requiere credenciales reales) —
        # se simula el guardado para poder probar el parseo del XML.
        parcheador = patch(
            'cloudinary_storage.storage.RawMediaCloudinaryStorage._save',
            side_effect=lambda name, content: name,
        )
        parcheador.start()
        self.addCleanup(parcheador.stop)

    def _crear_compra_con_xml(self, **kwargs_cfdi):
        from django.core.files.uploadedfile import SimpleUploadedFile
        xml = SimpleUploadedFile('factura.xml', _construir_cfdi(**kwargs_cfdi), content_type='application/xml')
        return Compra.objects.create(archivo_xml=xml)

    def test_detecta_unidad_negocio_por_rfc_receptor_sin_carga_masiva(self):
        from contabilidad.models import UnidadNegocio
        UnidadNegocio.objects.get_or_create(clave='QUINTA', defaults={'nombre': "Quinta Test"})

        compra = self._crear_compra_con_xml(rfc_receptor='PECE010202IA0')

        self.assertIsNotNone(compra.unidad_negocio)
        self.assertEqual(compra.unidad_negocio.clave, 'QUINTA')

    def test_no_sobreescribe_unidad_negocio_ya_asignada(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from contabilidad.models import UnidadNegocio

        UnidadNegocio.objects.get_or_create(clave='QUINTA', defaults={'nombre': "Quinta Test"})
        airbnb, _ = UnidadNegocio.objects.get_or_create(clave='AIRBNB', defaults={'nombre': 'Airbnb Test'})

        xml = SimpleUploadedFile(
            'factura.xml', _construir_cfdi(rfc_receptor='PECE010202IA0'), content_type='application/xml'
        )
        compra = Compra.objects.create(archivo_xml=xml, unidad_negocio=airbnb)

        self.assertEqual(compra.unidad_negocio.clave, 'AIRBNB')  # se respeta lo forzado manualmente

    def test_crea_proveedor_en_catalogo_si_no_existe(self):
        from comercial.models import Proveedor

        compra = self._crear_compra_con_xml(rfc_emisor='NUE010101XYZ', nombre_emisor='Proveedor Nuevo SA')

        self.assertIsNotNone(compra.proveedor)
        self.assertEqual(compra.proveedor.nombre, 'Proveedor Nuevo SA')
        self.assertEqual(compra.proveedor.rfc, 'NUE010101XYZ')
        self.assertTrue(Proveedor.objects.filter(nombre='Proveedor Nuevo SA').exists())

    def test_vincula_a_proveedor_existente_por_rfc(self):
        from comercial.models import Proveedor

        existente = Proveedor.objects.create(nombre='Nombre Distinto En Catálogo', rfc='EXI010101AAA')
        compra = self._crear_compra_con_xml(rfc_emisor='EXI010101AAA', nombre_emisor='Nombre Como Viene En El XML')

        self.assertEqual(compra.proveedor_id, existente.pk)
        # no debe crear un segundo Proveedor solo porque el nombre no coincide
        self.assertEqual(Proveedor.objects.filter(rfc='EXI010101AAA').count(), 1)

    def test_vincula_a_proveedor_existente_por_nombre_si_no_hay_rfc_match(self):
        from comercial.models import Proveedor

        existente = Proveedor.objects.create(nombre='Proveedor Sin Rfc Antes')
        compra = self._crear_compra_con_xml(rfc_emisor='NVO020202BBB', nombre_emisor='Proveedor Sin Rfc Antes')

        self.assertEqual(compra.proveedor_id, existente.pk)
        existente.refresh_from_db()
        self.assertEqual(existente.rfc, 'NVO020202BBB')  # se completa el RFC que faltaba

    def test_compra_manual_sin_xml_tambien_vincula_proveedor_por_nombre_capturado(self):
        """Aunque no haya XML, si se captura proveedor_nombre a mano, debe
        buscar/crear igual en el catálogo — no solo cuando viene de un CFDI."""
        compra = Compra.objects.create(proveedor_nombre='Ferretería Local')

        self.assertIsNotNone(compra.proveedor)
        self.assertEqual(compra.proveedor.nombre, 'Ferretería Local')
