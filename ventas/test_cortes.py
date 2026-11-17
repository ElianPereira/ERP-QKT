"""Cortes, pago, póliza, conciliación y efectivo (Issue #505, PR 4)."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from itertools import count
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, Pago
from comunicacion.models import Pendiente, Responsable, Visita
from contabilidad.models import (
    ConfiguracionContable,
    CuentaBancaria,
    CuentaContable,
    EstadoCuentaBancario,
    MovimientoContable,
    MovimientoEstadoCuenta,
    Poliza,
    UnidadNegocio,
)
from contabilidad.services_estados_cuenta import _emparejar_automaticamente
from contabilidad.signals import poliza_de_corte
from nomina.models import Empleado
from ventas import services_cortes as cortes
from ventas import services_efectivo as efectivo
from ventas.models import CorteComision, EntregaEfectivo, MovimientoComision
from ventas.test_comisiones import CLAVE, ZONA, D, condiciones, pagar, vendedora, venta

STORAGE = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
_claves = count(1)


def mov(vendedor, monto, fecha, tipo='CONTRATACION', **extra):
    return MovimientoComision.objects.create(
        vendedor=vendedor, tipo=tipo, monto=D(monto), fecha=fecha, clave_idempotencia=f't:{next(_claves)}', **extra)


def dirección():
    return User.objects.create_superuser(f'dir{next(_claves)}', 'd@x.mx', CLAVE)


class PeriodosTest(TestCase):
    def test_visitas_semana_lunes_a_domingo(self):
        self.assertEqual(cortes.periodo_visitas(date(2026, 11, 22)), (date(2026, 11, 16), date(2026, 11, 22)))

    def test_comisiones_quincenas(self):
        self.assertEqual(cortes.periodo_comisiones(date(2026, 11, 16)), (date(2026, 11, 1), date(2026, 11, 15)))
        self.assertEqual(cortes.periodo_comisiones(date(2026, 12, 1)), (date(2026, 11, 16), date(2026, 11, 30)))
        self.assertEqual(cortes.periodo_comisiones(date(2026, 3, 1)), (date(2026, 2, 16), date(2026, 2, 28)))
        self.assertEqual(cortes.periodo_comisiones(date(2027, 1, 1)), (date(2026, 12, 16), date(2026, 12, 31)))

    def test_mensual_solo_corta_el_dia_uno(self):
        self.assertIsNone(cortes.periodo_comisiones(date(2026, 11, 16), 'MENSUAL'))
        self.assertEqual(cortes.periodo_comisiones(date(2026, 12, 1), 'MENSUAL'),
                         (date(2026, 11, 1), date(2026, 11, 30)))

    def test_domingo_de_pago(self):
        ana = vendedora()
        visitas = CorteComision(vendedor=ana, tipo='VISITAS', desde=date(2026, 11, 16), hasta=date(2026, 11, 22))
        quincena = CorteComision(vendedor=ana, tipo='COMISIONES', desde=date(2026, 11, 1), hasta=date(2026, 11, 15))
        self.assertEqual(cortes.fecha_pago_programada(visitas), date(2026, 11, 22))   # domingo del corte
        self.assertEqual(cortes.fecha_pago_programada(quincena), date(2026, 11, 22))  # domingo siguiente al 15
        fin = CorteComision(vendedor=ana, tipo='COMISIONES', desde=date(2026, 11, 16), hasta=date(2026, 11, 30))
        self.assertEqual(cortes.fecha_pago_programada(fin), date(2026, 12, 6))
        self.assertEqual(fin.hasta.weekday(), 0)


class CorteTest(TestCase):
    def setUp(self):
        self.ana = vendedora()
        self.desde, self.hasta = date(2026, 11, 1), date(2026, 11, 15)

    def test_corte_de_comisiones_suma_lo_devengado(self):
        mov(self.ana, '250.00', date(2026, 11, 3))
        mov(self.ana, '250.00', date(2026, 11, 9))
        corte = cortes.crear_corte(self.ana, 'COMISIONES', self.desde, self.hasta)
        self.assertEqual((corte.bruto, corte.compensado, corte.total, corte.estado),
                         (D('500.00'), D('0.00'), D('500.00'), 'BORRADOR'))
        self.assertEqual(MovimientoComision.objects.filter(corte=corte).count(), 2)

    def test_no_toma_dos_veces_un_movimiento_y_doble_corrida_es_idempotente(self):
        mov(self.ana, '300.00', date(2026, 11, 3))
        primero = cortes.crear_corte(self.ana, 'COMISIONES', self.desde, self.hasta)
        segundo = cortes.crear_corte(self.ana, 'COMISIONES', self.desde, self.hasta)
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(CorteComision.objects.count(), 1)
        mov(self.ana, '100.00', date(2026, 11, 20))
        siguiente = cortes.crear_corte(self.ana, 'COMISIONES', date(2026, 11, 16), date(2026, 11, 30))
        self.assertEqual(siguiente.total, D('100.00'))
        self.assertEqual(MovimientoComision.objects.filter(corte=primero).count(), 1)

    def test_sin_movimientos_no_hay_corte(self):
        self.assertIsNone(cortes.crear_corte(self.ana, 'COMISIONES', self.desde, self.hasta))

    def test_arrastre_y_tope_de_compensacion_del_30_por_ciento(self):
        mov(self.ana, '500.00', date(2026, 11, 3))
        mov(self.ana, '-400.00', date(2026, 11, 4))
        primero = cortes.crear_corte(self.ana, 'COMISIONES', self.desde, self.hasta)
        # Tope 30 % de $500.00 = $150.00; quedan $250.00 en contra.
        self.assertEqual((primero.compensado, primero.total, primero.saldo_arrastrado),
                         (D('150.00'), D('350.00'), D('250.00')))
        mov(self.ana, '500.00', date(2026, 11, 20))
        segundo = cortes.crear_corte(self.ana, 'COMISIONES', date(2026, 11, 16), date(2026, 11, 30))
        self.assertEqual((segundo.compensado, segundo.total, segundo.saldo_arrastrado),
                         (D('150.00'), D('350.00'), D('100.00')))
        mov(self.ana, '100.00', date(2026, 12, 3))
        tercero = cortes.crear_corte(self.ana, 'COMISIONES', date(2026, 12, 1), date(2026, 12, 15))
        self.assertEqual((tercero.compensado, tercero.total, tercero.saldo_arrastrado),
                         (D('30.00'), D('70.00'), D('70.00')))

    def test_el_tope_sale_de_las_condiciones(self):
        otra = vendedora('Bea', '9990000002')
        from nomina.models import CondicionesPago
        CondicionesPago.objects.filter(empleado=otra).update(tope_compensacion=D('0.5000'))
        mov(otra, '1000.00', date(2026, 11, 3))
        mov(otra, '-800.00', date(2026, 11, 4))
        corte = cortes.crear_corte(otra, 'COMISIONES', self.desde, self.hasta)
        self.assertEqual((corte.compensado, corte.total, corte.saldo_arrastrado),
                         (D('500.00'), D('500.00'), D('300.00')))

    def test_un_corte_nunca_queda_negativo_y_se_cierra_solo(self):
        mov(self.ana, '-200.00', date(2026, 11, 4))
        corte = cortes.crear_corte(self.ana, 'COMISIONES', self.desde, self.hasta)
        self.assertEqual((corte.total, corte.saldo_arrastrado, corte.estado), (D('0.00'), D('200.00'), 'PAGADO'))
        self.assertFalse(Pendiente.objects.filter(clave_idempotencia=f'corte:{corte.pk}').exists())
        mov(self.ana, '300.00', date(2026, 11, 20))
        siguiente = cortes.crear_corte(self.ana, 'COMISIONES', date(2026, 11, 16), date(2026, 11, 30))
        self.assertEqual((siguiente.compensado, siguiente.total, siguiente.saldo_arrastrado),
                         (D('90.00'), D('210.00'), D('110.00')))

    def test_baja_absorbe_el_saldo_en_contra(self):
        mov(self.ana, '500.00', date(2026, 11, 3))
        mov(self.ana, '-400.00', date(2026, 11, 4))
        Empleado.objects.filter(pk=self.ana.pk).update(fecha_baja=date(2026, 11, 10))
        self.ana.refresh_from_db()
        corte = cortes.crear_corte(self.ana, 'COMISIONES', self.desde, self.hasta)
        self.assertEqual((corte.compensado, corte.total, corte.saldo_arrastrado, corte.absorbido),
                         (D('150.00'), D('350.00'), D('0.00'), D('250.00')))

    def test_absorcion_por_la_quinta_cancela_saldo_sin_pagarse(self):
        mov(self.ana, '500.00', date(2026, 11, 3))
        mov(self.ana, '-400.00', date(2026, 11, 4))
        mov(self.ana, '400.00', date(2026, 11, 5), tipo='AJUSTE', absorcion=True)
        corte = cortes.crear_corte(self.ana, 'COMISIONES', self.desde, self.hasta)
        self.assertEqual((corte.bruto, corte.compensado, corte.total, corte.saldo_arrastrado),
                         (D('500.00'), D('0.00'), D('500.00'), D('0.00')))

    def test_corte_de_visitas_por_semana_y_las_manuales_se_listan_aparte(self):
        lunes = date(2026, 11, 16)
        inicio = datetime.combine(lunes, time(10), tzinfo=ZoneInfo('America/Merida'))
        manual = Visita.objects.create(nombre='Manual', telefono='9990001111', inicio=inicio, estado='CONFIRMADA',
                                       vendedor=self.ana, resultado='ATENDIDA', origen='MANUAL')
        kooxi = Visita.objects.create(nombre='Kooxi', telefono='9990002222', inicio=inicio + timedelta(hours=1),
                                      estado='CONFIRMADA', vendedor=self.ana, resultado='ATENDIDA')
        mov(self.ana, '100.00', lunes, tipo='VISITA', visita=manual)
        mov(self.ana, '100.00', lunes, tipo='VISITA', visita=kooxi)
        corte = cortes.crear_corte(self.ana, 'VISITAS', *cortes.periodo_visitas(date(2026, 11, 22)))
        self.assertEqual(corte.total, D('200.00'))
        filas = cortes.filas_del_corte(corte)
        self.assertEqual([f['manual'] for f in filas], [True, False])
        pendiente = Pendiente.objects.get(clave_idempotencia=f'corte:{corte.pk}')
        self.assertIn('Visitas capturadas a mano: 1', pendiente.detalle)

    def test_visita_del_domingo_posterior_al_corte_cae_en_el_siguiente(self):
        mov(self.ana, '100.00', date(2026, 11, 17), tipo='VISITA')
        primero = cortes.crear_corte(self.ana, 'VISITAS', date(2026, 11, 16), date(2026, 11, 22))
        mov(self.ana, '100.00', date(2026, 11, 22), tipo='VISITA')  # cerrada después de cortar
        segundo = cortes.crear_corte(self.ana, 'VISITAS', date(2026, 11, 23), date(2026, 11, 29))
        self.assertEqual((primero.total, segundo.total), (D('100.00'), D('100.00')))

    def test_generar_cortes_corta_solo_a_quien_tiene_movimientos(self):
        otra = vendedora('Bea', '9990000002')
        mov(otra, '250.00', date(2026, 11, 3))
        creados = cortes.generar_cortes('COMISIONES', hoy=date(2026, 11, 16))
        self.assertEqual([c.vendedor for c in creados], [otra])
        self.assertEqual(cortes.generar_cortes('COMISIONES', hoy=date(2026, 11, 16)), [])  # nada más que cortar
        self.assertEqual(CorteComision.objects.count(), 1)

    def test_comando_y_programador(self):
        from core_erp.programador import POR_NOMBRE

        mov(self.ana, '250.00', timezone.localdate() - timedelta(days=40))
        call_command('generar_cortes_ventas', '--comisiones', stdout=mock.MagicMock())
        self.assertEqual(CorteComision.objects.count(), 1)
        for nombre in ('cortes_ventas_visitas', 'cortes_ventas_comisiones_16', 'cortes_ventas_comisiones_01'):
            self.assertIn(nombre, POR_NOMBRE)


class AprobacionTest(TestCase):
    def setUp(self):
        self.ana = vendedora()
        mov(self.ana, '500.00', date(2026, 11, 3))
        self.corte = cortes.crear_corte(self.ana, 'COMISIONES', date(2026, 11, 1), date(2026, 11, 15))

    def test_solo_direccion_aprueba_y_aprobar_no_paga(self):
        normal = User.objects.create_user('normal', password=CLAVE, is_staff=True)
        with self.assertRaises(PermissionDenied):
            cortes.aprobar_corte(self.corte, usuario=normal)
        su = dirección()
        self.assertTrue(cortes.aprobar_corte(self.corte, usuario=su))
        self.assertFalse(cortes.aprobar_corte(self.corte, usuario=su))
        self.corte.refresh_from_db()
        self.assertEqual((self.corte.estado, self.corte.aprobado_por), ('APROBADO', su))
        self.assertIsNone(poliza_de_corte(self.corte))
        self.assertTrue(Pendiente.objects.get(clave_idempotencia=f'corte:{self.corte.pk}').estado != 'ABIERTO')

    def test_boton_de_whatsapp_solo_desde_direccion(self):
        from comunicacion.services_direccion import atender_boton

        Responsable.objects.create(rol='DIRECCION', nombre='Dir', telefono_wa='5219999000001')
        self.assertIsNone(atender_boton('5219999000999', f'cor:{self.corte.pk}:ok'))
        self.corte.refresh_from_db()
        self.assertEqual(self.corte.estado, 'BORRADOR')
        self.assertIn('quedó aprobado', atender_boton('5219999000001', f'cor:{self.corte.pk}:ok'))
        self.corte.refresh_from_db()
        self.assertEqual(self.corte.estado, 'APROBADO')
        self.assertIn('ya estaba', atender_boton('5219999000001', f'cor:{self.corte.pk}:ok'))

    def test_cancelar_corte_libera_los_movimientos(self):
        cortes.cancelar_corte(self.corte, dirección())
        self.assertEqual(MovimientoComision.objects.filter(corte__isnull=True).count(), 1)
        nuevo = cortes.crear_corte(self.ana, 'COMISIONES', date(2026, 11, 1), date(2026, 11, 15))
        self.assertNotEqual(nuevo.pk, self.corte.pk)
        self.assertEqual(nuevo.total, D('500.00'))


@override_settings(STORAGES=STORAGE)
class PagoYPolizaTest(TestCase):
    def setUp(self):
        self.ana = vendedora()
        self.su = dirección()
        mov(self.ana, '500.00', date(2026, 11, 3))
        self.corte = cortes.crear_corte(self.ana, 'COMISIONES', date(2026, 11, 1), date(2026, 11, 15))
        cortes.aprobar_corte(self.corte, usuario=self.su)
        self.unidad = UnidadNegocio.objects.get(clave='QUINTA')
        self.egresos = CuentaBancaria.objects.create(
            nombre='BBVA Egresos', banco='BBVA', clabe='012345678901234777', unidad_negocio=self.unidad,
            cuenta_contable=CuentaContable.objects.get(codigo_sat='102.02.01'))

    def test_la_migracion_deja_la_subcuenta_y_la_operacion(self):
        cuenta = CuentaContable.objects.get(codigo_sat='601.05.05')
        self.assertEqual((cuenta.tipo, cuenta.padre.codigo_sat), ('GASTO', '601.05'))
        self.assertEqual(ConfiguracionContable.objects.get(operacion='COMISIONES_VENTA').cuenta, cuenta)

    def test_pago_en_efectivo_asienta_gasto_no_deducible_contra_caja(self):
        cortes.marcar_pagado(self.corte, self.su, 'EFECTIVO', referencia='Recibo 1')
        poliza = poliza_de_corte(self.corte)
        self.assertEqual((poliza.tipo, poliza.estado, poliza.origen), ('E', 'APLICADA', 'COMISION_VENTAS'))
        lineas = {m.cuenta.codigo_sat: (m.debe, m.haber) for m in poliza.movimientos.all()}
        caja = ConfiguracionContable.objects.get(operacion='CAJA').cuenta.codigo_sat
        self.assertEqual(lineas['601.05.05'], (D('500.00'), D('0.00')))
        self.assertEqual(lineas[caja], (D('0.00'), D('500.00')))
        self.assertTrue(poliza.esta_cuadrada)
        self.corte.refresh_from_db()
        self.assertEqual((self.corte.estado, self.corte.pagado_por, self.corte.metodo), ('PAGADO', self.su, 'EFECTIVO'))
        self.assertTrue(self.corte.archivo_pdf)

    def test_pago_por_transferencia_sale_de_la_cuenta_elegida(self):
        cortes.marcar_pagado(self.corte, self.su, 'TRANSFERENCIA', self.egresos, 'SPEI 99')
        lineas = {m.cuenta.codigo_sat: (m.debe, m.haber) for m in poliza_de_corte(self.corte).movimientos.all()}
        self.assertEqual(lineas['102.02.01'], (D('0.00'), D('500.00')))

    def test_sin_cuenta_elegida_usa_la_pagadora(self):
        CuentaBancaria.objects.filter(pk=self.egresos.pk).update(rol='PAGO')
        cortes.marcar_pagado(self.corte, self.su, 'TRANSFERENCIA')
        self.assertIsNotNone(poliza_de_corte(self.corte))

    def test_solo_direccion_paga_y_solo_un_corte_aprobado(self):
        normal = User.objects.create_user('normal', password=CLAVE, is_staff=True)
        with self.assertRaises(PermissionDenied):
            cortes.marcar_pagado(self.corte, normal, 'EFECTIVO')
        with self.assertRaises(ValidationError):
            cortes.marcar_pagado(self.corte, self.su, 'CHEQUE')
        mov(self.ana, '100.00', date(2026, 11, 20))
        borrador = cortes.crear_corte(self.ana, 'COMISIONES', date(2026, 11, 16), date(2026, 11, 30))
        with self.assertRaises(ValidationError):
            cortes.marcar_pagado(borrador, self.su, 'EFECTIVO')

    def test_pagar_dos_veces_no_duplica_la_poliza(self):
        cortes.marcar_pagado(self.corte, self.su, 'EFECTIVO')
        with self.assertRaises(ValidationError):
            cortes.marcar_pagado(self.corte, self.su, 'EFECTIVO')
        self.corte.save()  # un guardado cualquiera tampoco la repite
        self.assertEqual(Poliza.objects.filter(origen='COMISION_VENTAS').count(), 1)

    def test_si_la_poliza_no_puede_asentarse_el_pago_no_se_registra(self):
        ConfiguracionContable.objects.filter(operacion='CAJA').update(activa=False)
        with self.assertRaises(ValidationError):
            cortes.marcar_pagado(self.corte, self.su, 'EFECTIVO')
        self.corte.refresh_from_db()
        self.assertEqual(self.corte.estado, 'APROBADO')
        self.assertEqual(Poliza.objects.filter(origen='COMISION_VENTAS').count(), 0)

    def test_cancelar_el_pago_cancela_la_poliza_sin_borrarla(self):
        cortes.marcar_pagado(self.corte, self.su, 'EFECTIVO')
        cortes.cancelar_pago(self.corte, self.su)
        self.assertIsNone(poliza_de_corte(self.corte))
        self.assertEqual(Poliza.objects.filter(origen='COMISION_VENTAS', estado='CANCELADA').count(), 1)
        self.corte.refresh_from_db()
        self.assertEqual(self.corte.estado, 'APROBADO')
        cortes.marcar_pagado(self.corte, self.su, 'EFECTIVO')  # se puede volver a pagar
        self.assertEqual(Poliza.objects.filter(origen='COMISION_VENTAS', estado='APLICADA').count(), 1)

    def test_total_cero_no_genera_poliza(self):
        mov(self.ana, '-100.00', date(2026, 11, 20))
        cero = cortes.crear_corte(self.ana, 'COMISIONES', date(2026, 11, 16), date(2026, 11, 30))
        self.assertEqual(cero.estado, 'PAGADO')
        self.assertEqual(Poliza.objects.filter(origen='COMISION_VENTAS').count(), 0)

    def test_aparece_en_el_estado_de_resultados_y_en_el_flujo_de_efectivo(self):
        from reportes.services.contabilidad import EstadoResultadosService
        from reportes.services.finanzas import FlujoEfectivoService

        cortes.marcar_pagado(self.corte, self.su, 'TRANSFERENCIA', self.egresos, 'SPEI')
        hoy = timezone.localdate()
        estado = EstadoResultadosService.generar(hoy, hoy)
        texto = repr(estado)
        self.assertIn('601.05.05', texto)
        self.assertIn('500', texto)
        flujo = FlujoEfectivoService.generar(hoy, hoy)
        self.assertIn('500', repr(flujo))

    def test_el_cargo_del_banco_empareja_con_la_linea_de_la_poliza(self):
        cortes.marcar_pagado(self.corte, self.su, 'TRANSFERENCIA', self.egresos, 'SPEI 99')
        poliza = poliza_de_corte(self.corte)
        linea_banco = poliza.movimientos.get(cuenta=self.egresos.cuenta_contable)
        estado = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.egresos, banco='BBVA', periodo_mes=poliza.fecha.month,
            periodo_anio=poliza.fecha.year, formato='PDF', estado='PROCESADO')
        mov_banco = MovimientoEstadoCuenta.objects.create(
            estado_cuenta=estado, fecha=poliza.fecha + timedelta(days=1), descripcion='SPEI ENVIADO ANA',
            cargo=D('500.00'))
        _emparejar_automaticamente(estado)
        mov_banco.refresh_from_db()
        self.assertEqual(mov_banco.movimiento_contable, linea_banco)
        self.assertIsInstance(linea_banco, MovimientoContable)

    def test_estado_de_comisiones_pdf(self):
        pdf = cortes.generar_pdf(self.corte)
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertEqual(cortes.nombre_pdf(self.corte), 'QKT_Comisiones_Ana_20261101-20261115.pdf')

    def test_aviso_de_pago_nunca_tumba_el_pago(self):
        with mock.patch('comunicacion.services_pendientes._enviar_aviso', side_effect=RuntimeError('x')):
            cortes.marcar_pagado(self.corte, self.su, 'EFECTIVO')
        self.corte.refresh_from_db()
        self.assertEqual(self.corte.estado, 'PAGADO')


class EfectivoTest(TestCase):
    def setUp(self):
        self.ana = vendedora()
        self.usuario = User.objects.create_user('ana', password=CLAVE, is_staff=True)
        Empleado.objects.filter(pk=self.ana.pk).update(usuario=self.usuario)
        self.ana.refresh_from_db()
        self.cot = venta(self.ana)
        self.su = dirección()

    def cobro(self, monto, usuario=None, metodo='EFECTIVO'):
        p = pagar(self.cot, monto, metodo=metodo)
        Pago.objects.filter(pk=p.pk).update(usuario=usuario or self.usuario)
        return Pago.objects.get(pk=p.pk)

    def test_lista_solo_el_efectivo_que_ella_registro(self):
        mio = self.cobro('1000.00')
        self.cobro('500.00', metodo='TRANSFERENCIA')
        self.cobro('300.00', usuario=self.su)
        self.assertEqual(list(efectivo.pagos_por_entregar(self.ana)), [mio])
        self.assertEqual(efectivo.saldo_por_entregar(self.ana), D('1000.00'))
        self.assertEqual(efectivo.saldos_pendientes(), [(self.ana, D('1000.00'))])

    def test_registrar_entrega_descuenta_el_saldo_y_no_se_repite(self):
        a, b = self.cobro('1000.00'), self.cobro('250.50')
        entrega = efectivo.registrar_entrega(self.ana, [a], self.su, date(2026, 11, 20), usuario=self.su)
        self.assertEqual(entrega.monto, D('1000.00'))
        self.assertEqual(efectivo.saldo_por_entregar(self.ana), D('250.50'))
        with self.assertRaises(ValidationError):
            efectivo.registrar_entrega(self.ana, [a], self.su, date(2026, 11, 20))
        efectivo.registrar_entrega(self.ana, [b], self.su, date(2026, 11, 21))
        self.assertEqual(efectivo.saldo_por_entregar(self.ana), D('0.00'))
        self.assertEqual(EntregaEfectivo.objects.count(), 2)

    def test_no_acepta_cobros_ajenos_ni_vacios(self):
        ajeno = self.cobro('100.00', usuario=self.su)
        with self.assertRaises(ValidationError):
            efectivo.registrar_entrega(self.ana, [ajeno], self.su, date(2026, 11, 20))
        with self.assertRaises(ValidationError):
            efectivo.registrar_entrega(self.ana, [], self.su, date(2026, 11, 20))

    def test_una_entrega_no_se_borra_ni_genera_poliza(self):
        a = self.cobro('1000.00')
        antes = Poliza.objects.count()
        entrega = efectivo.registrar_entrega(self.ana, [a], self.su, date(2026, 11, 20))
        self.assertEqual(Poliza.objects.count(), antes)
        with self.assertRaises(PermissionDenied):
            entrega.delete()

    def test_el_efectivo_pendiente_jamas_se_compensa_contra_comisiones(self):
        self.cobro('5000.00')
        mov(self.ana, '500.00', date(2026, 11, 3))
        corte = cortes.crear_corte(self.ana, 'COMISIONES', date(2026, 11, 1), date(2026, 11, 15))
        self.assertEqual((corte.compensado, corte.total), (D('0.00'), D('500.00')))
        for modulo in (cortes, efectivo):
            self.assertFalse([n for n in dir(modulo) if 'compens' in n.lower() and callable(getattr(modulo, n))
                              and getattr(getattr(modulo, n), '__module__', '') == modulo.__name__])


class AdminCortesTest(TestCase):
    def setUp(self):
        from core_erp.test_utils import login_superuser_con_totp

        self.ana = vendedora()
        self.su = dirección()
        login_superuser_con_totp(self.client, self.su)
        mov(self.ana, '500.00', date(2026, 11, 3))
        self.corte = cortes.crear_corte(self.ana, 'COMISIONES', date(2026, 11, 1), date(2026, 11, 15))

    def accion(self, nombre, **extra):
        from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
        from django.urls import reverse

        return self.client.post(reverse('admin:ventas_cortecomision_changelist'), {
            'action': nombre, ACTION_CHECKBOX_NAME: [self.corte.pk], **extra})

    def test_listas_cargan_y_aprobar_pide_confirmacion(self):
        from django.urls import reverse

        for nombre in ('cortecomision', 'movimientocomision', 'comisioncontratacion', 'eventoventa',
                       'entregaefectivo'):
            self.assertEqual(self.client.get(reverse(f'admin:ventas_{nombre}_changelist')).status_code, 200, nombre)
        self.accion('aprobar')
        self.corte.refresh_from_db()
        self.assertEqual(self.corte.estado, 'BORRADOR')
        self.accion('aprobar', confirmar='si')
        self.corte.refresh_from_db()
        self.assertEqual(self.corte.estado, 'APROBADO')

    @override_settings(STORAGES=STORAGE)
    def test_marcar_pagado_desde_el_admin(self):
        cortes.aprobar_corte(self.corte, usuario=self.su)
        pantalla = self.accion('marcar_pagado')
        self.assertContains(pantalla, 'Cómo se pagó')
        self.corte.refresh_from_db()
        self.assertEqual(self.corte.estado, 'APROBADO')
        self.accion('marcar_pagado', confirmar='si', metodo='EFECTIVO', referencia='R-1')
        self.corte.refresh_from_db()
        self.assertEqual(self.corte.estado, 'PAGADO')

    def test_un_staff_sin_permisos_no_ve_los_cortes(self):
        from django.urls import reverse

        staff = User.objects.create_user('staff', password=CLAVE, is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse('admin:ventas_cortecomision_changelist')).status_code, 403)

    def test_registrar_entrega_desde_el_admin(self):
        from django.urls import reverse

        usuario = User.objects.create_user('ana', password=CLAVE, is_staff=True)
        Empleado.objects.filter(pk=self.ana.pk).update(usuario=usuario)
        cot = venta(self.ana)
        p = pagar(cot, '700.00', metodo='EFECTIVO')
        Pago.objects.filter(pk=p.pk).update(usuario=usuario)
        res = self.client.post(reverse('admin:ventas_entregaefectivo_add'), {
            'vendedor': self.ana.pk, 'fecha': '2026-11-20', 'pagos': [p.pk], 'recibido_por': self.su.pk,
            'notas': ''})
        self.assertEqual(res.status_code, 302, getattr(res, 'context_data', None) and res.context_data['errors'])
        entrega = EntregaEfectivo.objects.get()
        self.assertEqual((entrega.monto, entrega.created_by), (D('700.00'), self.su))
        otra = self.client.post(reverse('admin:ventas_entregaefectivo_add'), {
            'vendedor': self.ana.pk, 'fecha': '2026-11-20', 'pagos': [p.pk], 'recibido_por': self.su.pk})
        self.assertEqual(otra.status_code, 200)
        self.assertEqual(EntregaEfectivo.objects.count(), 1)


class ConsistenciaConElMotorTest(TestCase):
    """Un corte y el saldo en contra del motor hablan de lo mismo."""

    def test_saldo_en_contra_considera_el_arrastre_del_ultimo_corte(self):
        from ventas.services_comisiones import saldo_en_contra

        ana = vendedora()
        mov(ana, '500.00', date(2026, 11, 3))
        mov(ana, '-400.00', date(2026, 11, 4))
        self.assertEqual(saldo_en_contra(ana), D('400.00'))
        cortes.crear_corte(ana, 'COMISIONES', date(2026, 11, 1), date(2026, 11, 15))
        self.assertEqual(saldo_en_contra(ana), D('250.00'))

    def test_condiciones_helper_importado(self):
        self.assertTrue(callable(condiciones))
        self.assertTrue(callable(venta))
        self.assertTrue(isinstance(ZONA, ZoneInfo))
        self.assertIsNotNone(Cotizacion)
        self.assertIsNotNone(Cliente)
