"""
Reconocimiento del ingreso al ejecutarse el evento.

Origen: la simulación del flujo completo mostró que todo lo cobrado antes del
evento se quedaba en "Anticipo de clientes" para siempre: el cron pasa la
cotización a EJECUTADA/CERRADA con un `update()` y nada movía ese saldo a
ingresos.

Ejecutar: python manage.py test contabilidad.test_reconocimiento_ingreso --verbosity=2
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, ItemCotizacion, Pago
from contabilidad.models import MovimientoContable, Poliza
from contabilidad.signals import anticipo_por_reconocer, get_cuenta
from contabilidad.tests import setup_contabilidad_minima


def _cotizacion_pagada(dias=30, monto=Decimal('11600.00')):
    cliente = Cliente.objects.create(nombre='Cliente Ingreso', tipo_persona='FISICA')
    cot = Cotizacion.objects.create(
        cliente=cliente, nombre_evento='Boda',
        fecha_evento=timezone.localdate() + timedelta(days=dias), incluye_refrescos=False,
    )
    ItemCotizacion.objects.create(
        cotizacion=cot, descripcion='Servicio', cantidad=1, precio_unitario=Decimal('10000.00'))
    Pago.objects.create(cotizacion=cot, monto=monto, metodo='TRANSFERENCIA')
    return Cotizacion.objects.get(pk=cot.pk)


def _saldo(operacion):
    agg = MovimientoContable.objects.filter(
        cuenta=get_cuenta(operacion), poliza__estado='APLICADA',
    ).aggregate(d=Sum('debe'), h=Sum('haber'))
    return (agg['h'] or Decimal('0')) - (agg['d'] or Decimal('0'))


def _pasar_fecha(cot):
    Cotizacion.objects.filter(pk=cot.pk).update(fecha_evento=timezone.localdate() - timedelta(days=1))


class ReconocimientoIngresoTest(TestCase):

    def setUp(self):
        setup_contabilidad_minima()

    def test_el_cron_pasa_el_anticipo_a_ingreso_al_ejecutar(self):
        cot = _cotizacion_pagada()
        self.assertEqual(_saldo('ANTICIPO_CLIENTES'), Decimal('10000.00'))
        _pasar_fecha(cot)
        call_command('cerrar_cotizaciones', stdout=StringIO())
        cot.refresh_from_db()
        self.assertEqual(cot.estado, 'CERRADA')
        self.assertEqual(_saldo('ANTICIPO_CLIENTES'), Decimal('0.00'))
        self.assertEqual(_saldo('INGRESO_EVENTOS'), Decimal('10000.00'))
        poliza = Poliza.objects.get(origen='AJUSTE', object_id=cot.pk, tipo='D')
        self.assertEqual(poliza.fecha, cot.fecha_evento)
        movs = poliza.movimientos.aggregate(d=Sum('debe'), h=Sum('haber'))
        self.assertEqual(movs['d'], movs['h'])

    def test_correr_el_cron_dos_veces_no_duplica(self):
        cot = _cotizacion_pagada()
        _pasar_fecha(cot)
        call_command('cerrar_cotizaciones', stdout=StringIO())
        call_command('cerrar_cotizaciones', stdout=StringIO())
        self.assertEqual(Poliza.objects.filter(origen='AJUSTE', object_id=cot.pk).count(), 1)

    def test_el_cambio_manual_a_ejecutada_tambien_reconoce(self):
        cot = _cotizacion_pagada()
        self.assertEqual(cot.estado, 'CONFIRMADA')
        ok, msg = cot.cambiar_estado('EJECUTADA')
        self.assertTrue(ok, msg)
        self.assertEqual(anticipo_por_reconocer(cot), Decimal('0.00'))
        self.assertEqual(_saldo('INGRESO_EVENTOS'), Decimal('10000.00'))

    def test_un_pago_despues_del_evento_va_directo_a_ingreso_sin_duplicar(self):
        cot = _cotizacion_pagada(monto=Decimal('5800.00'))
        _pasar_fecha(cot)
        call_command('cerrar_cotizaciones', stdout=StringIO())
        cot.refresh_from_db()
        self.assertEqual(cot.estado, 'EJECUTADA')
        Pago.objects.create(cotizacion=cot, monto=Decimal('5800.00'), metodo='TRANSFERENCIA')
        call_command('cerrar_cotizaciones', stdout=StringIO())
        self.assertEqual(_saldo('ANTICIPO_CLIENTES'), Decimal('0.00'))
        self.assertEqual(_saldo('INGRESO_EVENTOS'), Decimal('10000.00'))


class ComandoReconocerIngresosTest(TestCase):

    def setUp(self):
        setup_contabilidad_minima()
        self.cot = _cotizacion_pagada()
        # Estado heredado: ejecutada antes de que existiera el reconocimiento.
        Cotizacion.objects.filter(pk=self.cot.pk).update(
            estado='CERRADA', fecha_evento=timezone.localdate() - timedelta(days=10))

    def test_simula_por_defecto(self):
        out = StringIO()
        call_command('reconocer_ingresos_eventos', stdout=out)
        self.assertIn('1 cotizaciones', out.getvalue())
        self.assertEqual(_saldo('INGRESO_EVENTOS'), Decimal('0.00'))

    def test_aplicar_reconoce_y_es_idempotente(self):
        call_command('reconocer_ingresos_eventos', '--aplicar', stdout=StringIO())
        call_command('reconocer_ingresos_eventos', '--aplicar', stdout=StringIO())
        self.assertEqual(_saldo('INGRESO_EVENTOS'), Decimal('10000.00'))
        self.assertEqual(_saldo('ANTICIPO_CLIENTES'), Decimal('0.00'))

    def test_desde_excluye_eventos_anteriores(self):
        desde = (timezone.localdate() - timedelta(days=5)).isoformat()
        call_command('reconocer_ingresos_eventos', '--aplicar', '--desde', desde, stdout=StringIO())
        self.assertEqual(_saldo('INGRESO_EVENTOS'), Decimal('0.00'))
