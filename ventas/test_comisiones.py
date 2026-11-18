"""Motor de comisiones (Issue #505, PR 3). Cada regla del Issue tiene su prueba."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, ItemCotizacion, Pago, Producto
from comunicacion.models import Pendiente, Visita
from nomina.models import CondicionesPago, Empleado, EscalonComision
from nomina.services_expediente import aceptar_condiciones
from ventas import services_atribucion
from ventas import services_comisiones as svc
from ventas.models import ComisionContratacion, EventoVenta, MovimientoComision

D = Decimal
CLAVE = 'x' * 20
ZONA = ZoneInfo('America/Merida')


def condiciones(empleado, visita=D('100.00'), tope=5, pct=D('0.05'), escalones=((1, '350.00'), (13, '500.00')),
                desde=None):
    c = CondicionesPago.objects.create(
        empleado=empleado, vigente_desde=desde or timezone.localdate() - timedelta(days=400),
        pago_visita=visita, tope_visitas_semana=tope, porcentaje_extras=pct)
    for n, monto in escalones:
        EscalonComision.objects.create(condiciones=c, desde_contratacion=n, monto=D(monto))
    return aceptar_condiciones(c)


def vendedora(nombre='Ana', telefono='9991234567', **cond):
    e = Empleado.objects.create(nombre=nombre, puesto='VENTAS', telefono=telefono)
    condiciones(e, **cond)
    return e


def venta(vendedor, base=D('14000.00'), extras=D('3000.00'), estado='CONFIRMADA', cliente=None, **campos):
    """Cotización confirmada con vendedor: base del servicio + extras (sin IVA)."""
    cliente = cliente or Cliente.objects.create(nombre='Cliente', telefono='9997654321')
    cot = Cotizacion.objects.create(cliente=cliente, nombre_evento='Evento',
                                    fecha_evento=timezone.localdate() + timedelta(days=60),
                                    incluye_refrescos=False, **campos)
    renta = Producto.objects.create(nombre=f'Renta {cot.pk}', precio_venta_fijo=base,
                                    rol_cotizador='BASE_EVENTO')
    ItemCotizacion.objects.create(cotizacion=cot, producto=renta, cantidad=1, precio_unitario=base)
    if extras:
        ItemCotizacion.objects.create(cotizacion=cot, descripcion='Extra', cantidad=1, precio_unitario=extras)
    Cotizacion.objects.filter(pk=cot.pk).update(vendedor=vendedor, vendedor_origen='COTIZO', estado=estado)
    cot.refresh_from_db()
    return cot


def pagar(cot, monto, metodo='TRANSFERENCIA', tipo='INGRESO', concepto='VENTA', fecha=None):
    return Pago.objects.bulk_create([Pago(
        cotizacion=cot, monto=D(monto), metodo=metodo, tipo=tipo, concepto=concepto,
        fecha_pago=fecha or timezone.localdate())])[0]


def devengado(cot):
    return ComisionContratacion.objects.get(cotizacion=cot).devengado


def suma_movimientos(vendedor, **filtro):
    return sum((m.monto for m in MovimientoComision.objects.filter(vendedor=vendedor, **filtro)), D('0.00'))


class ContratacionTest(TestCase):
    def setUp(self):
        self.ana = vendedora()

    def preparar_numero(self, n):
        """Deja n-1 contrataciones previas del año para que la siguiente sea la n."""
        for _ in range(n - 1):
            svc.asegurar_comision(venta(self.ana, extras=D('0.00')))

    def test_caso_armado_a_mano(self):
        # Contratación n.º 5: $350 + 5 % de $3,000 de extras = $500.00. Total con IVA $19,720.00.
        self.preparar_numero(5)
        cot = venta(self.ana)
        self.assertEqual(cot.precio_final, D('19720.00'))
        svc.sincronizar(cot)
        c = ComisionContratacion.objects.get(cotizacion=cot)
        self.assertEqual((c.numero_en_periodo, c.monto_fijo, c.base_extras, c.monto_extras, c.total),
                         (5, D('350.00'), D('3000.00'), D('150.00'), D('500.00')))
        self.assertEqual(c.devengado, D('0.00'))
        pagar(cot, '9860.00')  # 50 %
        svc.sincronizar(cot)
        self.assertEqual(devengado(cot), D('250.00'))
        pagar(cot, '9860.00')
        svc.sincronizar(cot)
        self.assertEqual(devengado(cot), D('500.00'))

    def test_reejecutar_no_duplica(self):
        cot = venta(self.ana)
        pagar(cot, '9860.00')
        self.assertIsNotNone(svc.sincronizar(cot))
        antes = MovimientoComision.objects.count()
        for _ in range(3):
            self.assertIsNone(svc.sincronizar(cot))
        self.assertEqual(MovimientoComision.objects.count(), antes)
        self.assertEqual(ComisionContratacion.objects.filter(cotizacion=cot).count(), 1)

    def test_plan_de_cuatro_pagos_suma_exacto(self):
        cot = venta(self.ana)
        for esperado in ('125.00', '250.00', '375.00', '500.00'):
            pagar(cot, '4930.00')
            svc.sincronizar(cot)
            self.assertEqual(devengado(cot), D(esperado))
        self.assertEqual(MovimientoComision.objects.filter(comision__cotizacion=cot).count(), 4)
        self.assertEqual(suma_movimientos(self.ana), D('500.00'))

    def test_escalon_12_a_13(self):
        cots = [venta(self.ana, extras=D('0.00')) for _ in range(13)]
        montos = []
        for cot in cots:
            svc.asegurar_comision(cot)
            montos.append(ComisionContratacion.objects.get(cotizacion=cot).monto_fijo)
        self.assertEqual(montos[11], D('350.00'))
        self.assertEqual(montos[12], D('500.00'))

    def test_cambio_de_anio_reinicia_el_numero(self):
        anterior = venta(self.ana, extras=D('0.00'))
        anio = timezone.localdate().year
        ComisionContratacion.objects.create(
            cotizacion=anterior, vendedor=self.ana, condiciones=self.ana.condiciones.first(),
            numero_en_periodo=12, anio=anio - 1, monto_fijo=D('350.00'), porcentaje_extras=D('0'),
            total=D('350.00'), confirmada_en=timezone.now() - timedelta(days=400))
        nueva = venta(self.ana, extras=D('0.00'))
        svc.asegurar_comision(nueva)
        self.assertEqual(ComisionContratacion.objects.get(cotizacion=nueva).numero_en_periodo, 1)

    def test_montos_salen_de_las_condiciones_no_del_codigo(self):
        otra = vendedora('Bea', '9990000002', visita=D('80.00'), pct=D('0.10'), escalones=((1, '200.00'),))
        cot = venta(otra)
        svc.asegurar_comision(cot)
        c = ComisionContratacion.objects.get(cotizacion=cot)
        self.assertEqual((c.monto_fijo, c.porcentaje_extras, c.total), (D('200.00'), D('0.1000'), D('500.00')))

    def test_los_montos_quedan_sellados_aunque_cambien_las_condiciones(self):
        cot = venta(self.ana)
        svc.asegurar_comision(cot)
        nuevas = CondicionesPago.objects.create(
            empleado=self.ana, vigente_desde=timezone.localdate() + timedelta(days=1), pago_visita=D('999.00'),
            porcentaje_extras=D('0.50'))
        EscalonComision.objects.create(condiciones=nuevas, desde_contratacion=1, monto=D('9999.00'))
        aceptar_condiciones(nuevas)
        pagar(cot, '19720.00')
        svc.sincronizar(cot)
        self.assertEqual(devengado(cot), D('500.00'))

    def test_sin_condiciones_no_devenga_y_avisa(self):
        sin = Empleado.objects.create(nombre='Sin', puesto='VENTAS')
        cot = venta(sin)
        self.assertIsNone(svc.asegurar_comision(cot))
        self.assertFalse(ComisionContratacion.objects.filter(cotizacion=cot).exists())
        self.assertTrue(Pendiente.objects.filter(clave_idempotencia=f'comision_sin_condiciones:{cot.pk}').exists())

    def test_extra_agregado_tras_confirmar_sube_la_comision(self):
        cot = venta(self.ana)
        pagar(cot, cot.precio_final)
        svc.sincronizar(cot)
        self.assertEqual(devengado(cot), D('500.00'))
        ItemCotizacion.objects.create(cotizacion=cot, descripcion='Hora extra', cantidad=1,
                                      precio_unitario=D('1000.00'))
        cot.refresh_from_db()
        pagar(cot, cot.precio_final - cot.total_pagado_neto())
        svc.sincronizar(cot)
        c = ComisionContratacion.objects.get(cotizacion=cot)
        self.assertEqual((c.base_extras, c.total, c.devengado), (D('4000.00'), D('550.00'), D('550.00')))

    def test_base_de_extras_excluye_base_paquete_y_fijos_e_incluye_horas_y_personas(self):
        from comercial.models import ProductoComponente

        cot = venta(self.ana, extras=D('0.00'))
        paquete = Producto.objects.create(nombre='Paquete', precio_venta_fijo=D('1000.00'), es_paquete=True)
        renta = Producto.objects.create(nombre='Renta fija', precio_venta_fijo=D('500.00'))
        ProductoComponente.objects.create(producto_padre=paquete, producto_hijo=renta, cantidad=1,
                                          fijo_por_evento=True)
        hora = Producto.objects.create(nombre='Hora', precio_venta_fijo=D('100.00'), rol_cotizador='HORA_EXTRA')
        persona = Producto.objects.create(nombre='Persona', precio_venta_fijo=D('50.00'),
                                          rol_cotizador='PERSONA_EXTRA_PASADIA')
        for prod, precio in ((paquete, '1000.00'), (renta, '500.00'), (hora, '100.00'), (persona, '50.00')):
            ItemCotizacion.objects.create(cotizacion=cot, producto=prod, cantidad=1, precio_unitario=D(precio))
        cot.refresh_from_db()
        self.assertEqual(svc.base_extras(cot), D('150.00'))

    def test_base_de_extras_es_neta_de_descuento(self):
        cot = venta(self.ana, base=D('7000.00'), extras=D('3000.00'))
        Cotizacion.objects.filter(pk=cot.pk).update(descuento=D('1000.00'))
        cot.refresh_from_db()
        self.assertEqual(svc.base_extras(cot), D('2700.00'))

    def test_cortesia_historica_y_sin_vendedor_no_generan_nada(self):
        cortesia = venta(self.ana, estado='CONFIRMADA')
        Cotizacion.objects.filter(pk=cortesia.pk).update(precio_final=D('0.00'))
        historica = venta(self.ana)
        Cotizacion.objects.filter(pk=historica.pk).update(es_historica=True)
        sin = venta(self.ana)
        Cotizacion.objects.filter(pk=sin.pk).update(vendedor=None)
        borrador = venta(self.ana, estado='BORRADOR')
        for cot in (cortesia, historica, sin, borrador):
            cot.refresh_from_db()
            self.assertIsNone(svc.sincronizar(cot))
        self.assertEqual(ComisionContratacion.objects.count(), 0)

    def test_deposito_propina_y_condonacion_no_devengan(self):
        cot = venta(self.ana)
        pagar(cot, '5000.00', concepto='EXTRA')
        pagar(cot, '5000.00', metodo='CONDONACION')
        svc.sincronizar(cot)
        self.assertEqual(devengado(cot), D('0.00'))
        # Lo condonado nunca llega al 100 %: se comisiona lo cobrado.
        pagar(cot, '14720.00')
        svc.sincronizar(cot)
        self.assertEqual(devengado(cot), D('373.23'))


class CancelacionYReembolsoTest(TestCase):
    def setUp(self):
        self.ana = vendedora()

    def test_reembolso_parcial_genera_movimiento_negativo(self):
        cot = venta(self.ana)
        pagar(cot, '19720.00')
        svc.sincronizar(cot)
        pagar(cot, '4930.00', tipo='REEMBOLSO')
        mov = svc.sincronizar(cot)
        self.assertEqual(mov.monto, D('-125.00'))
        self.assertEqual(devengado(cot), D('375.00'))

    def test_cancelacion_con_retencion_deja_lo_proporcional_a_lo_conservado(self):
        cot = venta(self.ana)
        svc.sincronizar(cot)
        pagar(cot, '9860.00')
        pagar(cot, '4000.00', tipo='REEMBOLSO')
        Cotizacion.objects.filter(pk=cot.pk).update(estado='CANCELADA')
        svc.sincronizar(cot)
        # Quedan $5,860.00 de $19,720.00 → 29.7120…% de $500.00.
        self.assertEqual(devengado(cot), D('148.58'))

    def test_devolucion_total_deja_en_cero_y_no_cuenta_para_el_escalon(self):
        cots = [venta(self.ana, extras=D('0.00')) for _ in range(12)]
        for cot in cots:
            svc.asegurar_comision(cot)
        primera = cots[0]
        pagar(primera, '11600.00')
        svc.sincronizar(primera)
        pagar(primera, '11600.00', tipo='REEMBOLSO')
        Cotizacion.objects.filter(pk=primera.pk).update(estado='CANCELADA')
        svc.sincronizar(primera)
        self.assertEqual(devengado(primera), D('0.00'))
        decimotercera = venta(self.ana, extras=D('0.00'))
        svc.asegurar_comision(decimotercera)
        # Con una devolución total solo cuentan 11: esta es la 12 (todavía $350).
        c = ComisionContratacion.objects.get(cotizacion=decimotercera)
        self.assertEqual((c.numero_en_periodo, c.monto_fijo), (12, D('350.00')))

    def test_saldo_en_contra_y_absorcion_imputable_a_la_quinta(self):
        cot = venta(self.ana)
        pagar(cot, '19720.00')
        svc.sincronizar(cot)
        pagar(cot, '9860.00', tipo='REEMBOLSO')
        svc.sincronizar(cot)
        self.assertEqual(svc.saldo_en_contra(self.ana), D('250.00'))
        c = ComisionContratacion.objects.get(cotizacion=cot)
        normal = User.objects.create_user('normal', password=CLAVE, is_staff=True)
        with self.assertRaises(PermissionDenied):
            svc.absorber(c, 'Cancela la Quinta', normal)
        su = User.objects.create_superuser('dir', 'd@x.mx', CLAVE)
        with self.assertRaises(ValidationError):
            svc.absorber(c, '  ', su)
        mov = svc.absorber(c, 'Cancelación imputable a la Quinta', su)
        self.assertEqual((mov.monto, mov.absorcion), (D('250.00'), True))
        self.assertEqual(svc.saldo_en_contra(self.ana), D('0.00'))
        self.assertIsNone(svc.absorber(c, 'otra vez', su))
        self.assertTrue(EventoVenta.objects.filter(cotizacion=cot, tipo='ABSORCION').exists())


class BajaTest(TestCase):
    def setUp(self):
        self.ana = vendedora()

    def test_baja_paga_lo_devengado_y_nada_despues(self):
        cot = venta(self.ana)
        hoy = timezone.localdate()
        pagar(cot, '9860.00', fecha=hoy - timedelta(days=3))
        svc.sincronizar(cot)
        Empleado.objects.filter(pk=self.ana.pk).update(fecha_baja=hoy - timedelta(days=1))
        pagar(cot, '9860.00', fecha=hoy)
        extra = ItemCotizacion.objects.create(cotizacion=cot, descripcion='Tarde', cantidad=1,
                                              precio_unitario=D('5000.00'))
        self.assertTrue(extra.pk)
        svc.sincronizar(cot)
        c = ComisionContratacion.objects.get(cotizacion=cot)
        self.assertEqual(c.devengado, D('250.00'))
        self.assertEqual(c.total, D('500.00'))
        self.assertEqual(suma_movimientos(self.ana), D('250.00'))

    def test_pagos_anteriores_a_la_baja_se_devengan_aunque_el_cron_llegue_tarde(self):
        cot = venta(self.ana)
        svc.asegurar_comision(cot)
        hoy = timezone.localdate()
        pagar(cot, '19720.00', fecha=hoy - timedelta(days=2))
        Empleado.objects.filter(pk=self.ana.pk).update(fecha_baja=hoy - timedelta(days=1))
        svc.sincronizar(cot)
        self.assertEqual(devengado(cot), D('500.00'))
        self.assertEqual(MovimientoComision.objects.get().fecha, hoy - timedelta(days=1))

    def test_una_venta_confirmada_despues_de_la_baja_no_devenga(self):
        Empleado.objects.filter(pk=self.ana.pk).update(fecha_baja=timezone.localdate() - timedelta(days=1))
        cot = venta(self.ana)
        self.assertIsNone(svc.asegurar_comision(cot))


class VisitasTest(TestCase):
    def setUp(self):
        self.ana = vendedora()
        self.lunes = svc.lunes_de(timezone.localdate()) - timedelta(days=14)

    def visita(self, dia, hora=10, resultado='ATENDIDA', telefono='9990001111'):
        inicio = datetime.combine(self.lunes + timedelta(days=dia), time(hora), tzinfo=ZONA)
        return Visita.objects.create(nombre='V', telefono=telefono, inicio=inicio, estado='CONFIRMADA',
                                     vendedor=self.ana, resultado=resultado)

    def test_sexta_visita_vale_cero_y_aun_asi_atribuye(self):
        visitas = [self.visita(0, 10 + i) for i in range(6)]
        cliente = Cliente.objects.create(nombre='Prospecto', telefono='9990001111')
        cot = venta(self.ana, cliente=cliente, estado='COTIZADA')
        Cotizacion.objects.filter(pk=cot.pk).update(vendedor=None, vendedor_origen='')
        svc.devengar_visitas(self.ana, self.lunes)
        montos = [m.monto for m in MovimientoComision.objects.filter(tipo='VISITA').order_by('visita__inicio')]
        self.assertEqual(montos, [D('100.00')] * 5 + [D('0.00')])
        self.assertEqual(MovimientoComision.objects.get(visita=visitas[5]).detalle, 'Sobre el tope semanal')
        self.assertEqual(services_atribucion.atribuir_por_visita(visitas[5]), 1)
        cot.refresh_from_db()
        self.assertEqual(cot.vendedor, self.ana)

    def test_el_tope_es_por_semana_lunes_a_domingo(self):
        for i in range(5):
            self.visita(i)          # lunes a viernes
        self.visita(6, 10)          # domingo: sexta de la semana, $0
        self.visita(7, 10)          # lunes siguiente: semana nueva
        svc.devengar_visitas(self.ana, self.lunes)
        svc.devengar_visitas(self.ana, self.lunes + timedelta(days=7))
        self.assertEqual(suma_movimientos(self.ana), D('600.00'))

    def test_reejecutar_no_duplica(self):
        self.visita(0)
        self.assertEqual(svc.devengar_visitas(self.ana, self.lunes), 1)
        self.assertEqual(svc.devengar_visitas(self.ana, self.lunes), 0)
        self.assertEqual(MovimientoComision.objects.count(), 1)

    def test_visita_que_pasa_a_no_llego_se_revierte_y_libera_el_tope(self):
        visitas = [self.visita(0, 10 + i) for i in range(6)]
        svc.devengar_visitas(self.ana, self.lunes)
        Visita.objects.filter(pk=visitas[0].pk).update(resultado='NO_LLEGO')
        svc.devengar_visitas(self.ana, self.lunes)
        self.assertEqual(suma_movimientos(self.ana), D('500.00'))  # la sexta ya paga
        self.assertEqual(suma_movimientos(self.ana, visita=visitas[0]), D('0.00'))
        self.assertEqual(svc.devengar_visitas(self.ana, self.lunes), 0)

    def test_visita_sin_resultado_o_no_llego_no_paga(self):
        self.visita(0, resultado='')
        self.visita(1, resultado='NO_LLEGO')
        svc.devengar_visitas(self.ana, self.lunes)
        self.assertEqual(MovimientoComision.objects.count(), 0)

    def test_visita_posterior_a_la_baja_no_paga(self):
        self.visita(2)
        Empleado.objects.filter(pk=self.ana.pk).update(fecha_baja=self.lunes + timedelta(days=1))
        svc.devengar_visitas(self.ana, self.lunes)
        self.assertEqual(suma_movimientos(self.ana), D('0.00'))

    def test_tope_y_monto_salen_de_las_condiciones(self):
        otra = vendedora('Bea', '9990000002', visita=D('80.00'), tope=2)
        for i in range(3):
            inicio = datetime.combine(self.lunes, time(10 + i), tzinfo=ZONA)
            Visita.objects.create(nombre='V', telefono='9990002222', inicio=inicio, estado='CONFIRMADA',
                                  vendedor=otra, resultado='ATENDIDA')
        svc.devengar_visitas(otra, self.lunes)
        self.assertEqual(suma_movimientos(otra), D('160.00'))


class InmutabilidadYDisparadoresTest(TestCase):
    def setUp(self):
        self.ana = vendedora()

    def movimiento(self):
        cot = venta(self.ana)
        pagar(cot, '9860.00')
        return svc.sincronizar(cot)

    def test_movimientos_son_inmutables(self):
        mov = self.movimiento()
        mov.monto = D('1.00')
        with self.assertRaises(PermissionDenied):
            mov.save()
        with self.assertRaises(PermissionDenied):
            mov.delete()
        with self.assertRaises(PermissionDenied):
            MovimientoComision.objects.all().delete()
        with self.assertRaises(PermissionDenied):
            MovimientoComision.objects.all().update(monto=D('1.00'))

    def test_bitacora_es_inmutable(self):
        self.movimiento()
        ev = EventoVenta.objects.first()
        with self.assertRaises(PermissionDenied):
            ev.delete()

    def test_un_pago_dispara_el_devengo_en_on_commit(self):
        cot = venta(self.ana)
        with self.captureOnCommitCallbacks(execute=True):
            Pago.objects.create(cotizacion=cot, monto=D('9860.00'), metodo='TRANSFERENCIA',
                                referencia='T-1', usuario=None)
        self.assertEqual(devengado(cot), D('250.00'))

    def test_un_fallo_de_ventas_no_impide_guardar_el_pago(self):
        cot = venta(self.ana)
        with mock.patch('ventas.services_comisiones.sincronizar', side_effect=RuntimeError('boom')):
            with self.assertLogs('ventas.signals', level='ERROR'):
                with self.captureOnCommitCallbacks(execute=True):
                    pago = Pago.objects.create(cotizacion=cot, monto=D('9860.00'), metodo='TRANSFERENCIA',
                                               referencia='T-2')
        self.assertTrue(Pago.objects.filter(pk=pago.pk).exists())

    def test_quien_registro_el_pago_no_importa(self):
        cot = venta(self.ana)
        otro = User.objects.create_user('otro', password=CLAVE, is_staff=True)
        with self.captureOnCommitCallbacks(execute=True):
            Pago.objects.create(cotizacion=cot, monto=D('9860.00'), metodo='TRANSFERENCIA', referencia='T-3',
                                usuario=otro)
        self.assertEqual(devengado(cot), D('250.00'))

    def test_cerrar_visita_dispara_el_devengo(self):
        inicio = timezone.now() - timedelta(hours=3)
        with self.captureOnCommitCallbacks(execute=True):
            Visita.objects.create(nombre='V', telefono='9990003333', inicio=inicio, estado='CONFIRMADA',
                                  vendedor=self.ana, resultado='ATENDIDA')
        self.assertEqual(suma_movimientos(self.ana, tipo='VISITA'), D('100.00'))

    def test_barrido_recupera_lo_que_cambio_sin_senal(self):
        cot = venta(self.ana)
        pagar(cot, '19720.00')  # bulk_create: sin señal
        resumen = svc.barrido()
        self.assertEqual(resumen['errores'], 0)
        self.assertEqual(devengado(cot), D('500.00'))
        movimientos = MovimientoComision.objects.count()
        svc.barrido()
        self.assertEqual(MovimientoComision.objects.count(), movimientos)

    def test_comando_sincronizar_comisiones(self):
        from django.core.management import call_command

        cot = venta(self.ana)
        pagar(cot, '19720.00')
        call_command('sincronizar_comisiones', stdout=mock.MagicMock())
        self.assertEqual(devengado(cot), D('500.00'))

    def test_el_comando_esta_en_el_programador(self):
        from core_erp.programador import POR_NOMBRE

        self.assertIn('sincronizar_comisiones', POR_NOMBRE)


class PanelComisionTest(TestCase):
    def test_panel_en_la_cotizacion_solo_para_superusuario(self):
        from django.urls import reverse

        from core_erp.test_utils import login_superuser_con_totp

        ana = vendedora()
        cot = venta(ana)
        svc.asegurar_comision(cot)
        su = User.objects.create_superuser('dir', 'd@x.mx', CLAVE)
        login_superuser_con_totp(self.client, su)
        html = self.client.get(reverse('admin:comercial_cotizacion_change', args=[cot.pk])).content.decode()
        self.assertIn('Comisión de ventas', html)
        self.assertIn('Contratación n.º 1', html)
