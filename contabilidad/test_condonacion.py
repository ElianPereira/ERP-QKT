"""Una condonación no es un cobro: sin póliza ni solicitud de factura, y el
comando corrige las pólizas que se generaron antes del arreglo."""
from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase

from comercial.models import Cliente, Cotizacion, ItemCotizacion, Pago
from contabilidad.models import CuentaContable, MovimientoContable, Poliza
from contabilidad.signals import anticipo_por_reconocer, get_cuenta, get_unidad_negocio, get_usuario_sistema
from facturacion.models import SolicitudFactura


def _cotizacion(estado='CONFIRMADA'):
    cot = Cotizacion.objects.create(
        cliente=Cliente.objects.create(nombre='Ana Ruiz', telefono='5555550101'),
        nombre_evento='Boda Ruiz', tipo_servicio='EVENTO', fecha_evento=date(2026, 3, 7),
        num_personas=80, incluye_refrescos=False,
    )
    ItemCotizacion.objects.create(cotizacion=cot, descripcion='Servicio', cantidad=1,
                                  precio_unitario=Decimal('10000.00'))
    Cotizacion.objects.filter(pk=cot.pk).update(estado=estado)
    cot.refresh_from_db()
    return cot


def _polizas(pago):
    return Poliza.objects.filter(content_type=ContentType.objects.get_for_model(Pago), object_id=pago.pk)


class CondonacionSinContabilidadTest(TestCase):

    def test_no_genera_poliza_ni_solicitud_de_factura(self):
        cot = _cotizacion()
        pago = Pago.objects.create(cotizacion=cot, monto=Decimal('1160.00'), metodo='CONDONACION',
                                   fecha_pago=date(2026, 3, 2))
        self.assertFalse(_polizas(pago).exists())
        self.assertFalse(SolicitudFactura.objects.filter(pago=pago).exists())
        banco = CuentaContable.objects.get(codigo_sat='102.02.01')
        self.assertFalse(MovimientoContable.objects.filter(cuenta=banco).exists())

    def test_un_cobro_real_sigue_generando_ambas(self):
        pago = Pago.objects.create(cotizacion=_cotizacion(), monto=Decimal('1160.00'), metodo='TRANSFERENCIA',
                                   fecha_pago=date(2026, 3, 2))
        self.assertEqual(_polizas(pago).get().estado, 'APLICADA')
        self.assertTrue(SolicitudFactura.objects.filter(pago=pago).exists())


class CorregirPolizasCondonacionTest(TestCase):
    """Póliza armada como la dejaba el signal viejo: banco contra anticipo + IVA."""

    def _poliza_vieja(self, pago):
        p = Poliza.objects.create(
            tipo='I', folio=Poliza.siguiente_folio('I', pago.fecha_pago), fecha=pago.fecha_pago,
            concepto='Pago cliente', unidad_negocio=get_unidad_negocio('QUINTA'), estado='APLICADA',
            origen='PAGO_CLIENTE', content_type=ContentType.objects.get_for_model(Pago), object_id=pago.pk,
            created_by=get_usuario_sistema(),
        )
        for cuenta, debe, haber in (('BANCO_PRINCIPAL', '1160.00', '0'), ('ANTICIPO_CLIENTES', '0', '1000.00'),
                                    ('IVA_TRASLADADO', '0', '160.00')):
            MovimientoContable.objects.create(poliza=p, cuenta=get_cuenta(cuenta), concepto='x',
                                              debe=Decimal(debe), haber=Decimal(haber))
        return p

    def _correr(self, *args):
        salida = StringIO()
        call_command('corregir_polizas_condonacion', *args, stdout=salida)
        return salida.getvalue()

    def test_simula_por_defecto_y_cancela_con_aplicar(self):
        pago = Pago.objects.create(cotizacion=_cotizacion(), monto=Decimal('1160.00'), metodo='CONDONACION',
                                   fecha_pago=date(2026, 3, 2))
        poliza = self._poliza_vieja(pago)

        self.assertIn('1 pólizas de condonación por cancelar', self._correr())
        poliza.refresh_from_db()
        self.assertEqual(poliza.estado, 'APLICADA')

        self._correr('--aplicar')
        poliza.refresh_from_db()
        self.assertEqual(poliza.estado, 'CANCELADA')
        self.assertFalse(Poliza.objects.filter(estado='APLICADA', origen='AJUSTE').exists(),
                         'Sin evento ejecutado no hay reconocimiento que ajustar')

    def test_evento_ya_reconocido_queda_con_anticipo_en_cero(self):
        from contabilidad.signals import crear_poliza_reconocimiento_ingreso

        cot = _cotizacion(estado='EJECUTADA')
        pago = Pago.objects.create(cotizacion=cot, monto=Decimal('1160.00'), metodo='CONDONACION',
                                   fecha_pago=date(2026, 3, 2))
        self._poliza_vieja(pago)
        crear_poliza_reconocimiento_ingreso(cot)  # pasó los $1,000 condonados a ingreso
        self.assertEqual(anticipo_por_reconocer(cot), Decimal('0.00'))

        self._correr('--aplicar')
        self.assertEqual(anticipo_por_reconocer(cot), Decimal('0.00'))
        ingreso = get_cuenta('INGRESO_EVENTOS')
        neto = sum((m.haber - m.debe for m in MovimientoContable.objects.filter(
            cuenta=ingreso, poliza__estado='APLICADA')), Decimal('0'))
        self.assertEqual(neto, Decimal('0.00'), 'El ingreso por lo condonado se revierte')

    def test_desde_respeta_periodos_cerrados(self):
        pago = Pago.objects.create(cotizacion=_cotizacion(), monto=Decimal('1160.00'), metodo='CONDONACION',
                                   fecha_pago=date(2026, 3, 2))
        poliza = self._poliza_vieja(pago)
        self._correr('--desde', '2026-04-01', '--aplicar')
        poliza.refresh_from_db()
        self.assertEqual(poliza.estado, 'APLICADA')
