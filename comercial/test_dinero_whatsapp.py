"""Acciones de dinero de Dirección por WhatsApp (Issue #433 · DINERO).

Cubre el catálogo cerrado (devolución del depósito, descuento manual, reembolso
hecho fuera del sistema) y los candados: teléfono, botón, tope y revalidación.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings

from comercial import services_dinero_wa as dinero
from comercial import services_ordenes_wa as ordenes
from comercial.models import (
    AccionWhatsApp,
    Descuento,
    DescuentoAplicado,
    ItemCotizacion,
    MovimientoDeposito,
    Pago,
)
from comercial.services_deposito import asegurar_deposito, registrar_recepcion
from comercial.services_descuentos import DescuentoService
from comercial.test_ordenes_whatsapp import TEL_DIR, TEL_ENC, cotizacion, responsable, token_de
from contabilidad.models import Poliza

TOPE = override_settings(ERP_KOOXI_DINERO_MAX=Decimal('5000.00'))


class Base(TestCase):
    def setUp(self):
        responsable('DIRECCION', TEL_DIR)
        responsable('ENCARGADO', TEL_ENC)
        self.cot = cotizacion()   # 10,000 + IVA = 11,600.00

    def confirmar(self, resultado, decision='ok'):
        return ordenes.responder_boton(TEL_DIR, token_de(resultado, decision))

    def accion(self):
        return AccionWhatsApp.objects.get()


@TOPE
class DevolucionDepositoTest(Base):
    def setUp(self):
        super().setUp()
        self.deposito = asegurar_deposito(self.cot, Decimal('1160.00'))
        registrar_recepcion(self.deposito, monto=Decimal('1160.00'), metodo='TRANSFERENCIA')

    def _proponer(self, medio='TRANSFERENCIA', telefono=TEL_DIR):
        return dinero.proponer_devolucion_deposito(telefono, self.cot.pk, medio)

    def test_propuesta_no_mueve_nada(self):
        r = self._proponer()
        self.assertIn('$1,160.00', r.texto)
        self.assertIn('sin retenciones', r.texto)
        self.assertIn(f'COT-{self.cot.pk:03d}', r.texto)
        self.assertEqual(MovimientoDeposito.objects.filter(tipo='DEVOLUCION').count(), 0)
        a = self.accion()
        self.assertEqual((a.estado, a.tipo, a.monto, a.cotizacion_id), ('PROPUESTA', 'DEVOLUCION_DEPOSITO', Decimal('1160.00'), self.cot.pk))

    def test_confirmar_registra_la_devolucion_con_su_poliza_y_sin_openpay(self):
        r = self._proponer()
        with patch('comercial.services_openpay.reembolsar_monto_openpay') as openpay, \
                self.captureOnCommitCallbacks(execute=True):
            ok = self.confirmar(r)
        openpay.assert_not_called()
        self.assertTrue(ok.ejecutada)
        mov = MovimientoDeposito.objects.get(tipo='DEVOLUCION')
        self.assertEqual((mov.monto, mov.metodo), (Decimal('1160.00'), 'TRANSFERENCIA'))
        a = self.accion()
        self.assertEqual((a.estado, a.movimiento_deposito_id), ('CONFIRMADA', mov.pk))
        self.assertEqual(mov.referencia, f'WA-ORDEN-{a.pk}')
        self.assertTrue(Poliza.objects.filter(content_type=ContentType.objects.get_for_model(mov), object_id=mov.pk).exists())
        self.deposito.refresh_from_db()
        self.assertTrue(self.deposito.liquidado)

    def test_doble_confirmacion_no_devuelve_dos_veces(self):
        r = self._proponer()
        self.confirmar(r)
        self.assertFalse(self.confirmar(r).ejecutada)
        self.assertEqual(MovimientoDeposito.objects.filter(tipo='DEVOLUCION').count(), 1)

    def test_sin_confirmar_o_cancelando_no_hay_movimiento(self):
        r = self._proponer()
        self.confirmar(r, 'no')
        self.confirmar(r, 'ok')
        self.assertEqual(MovimientoDeposito.objects.filter(tipo='DEVOLUCION').count(), 0)

    def test_solo_direccion_puede_proponer(self):
        self.assertIsNone(self._proponer(telefono=TEL_ENC))
        self.assertEqual(AccionWhatsApp.objects.count(), 0)

    def test_medio_que_no_sea_transferencia_o_efectivo_se_rechaza(self):
        for medio in ('PLATAFORMA', 'TARJETA_DEBITO', ''):
            self.assertIsNone(self._proponer(medio).botones)
        self.assertEqual(AccionWhatsApp.objects.filter(estado='RECHAZADA').count(), 3)

    def test_arriba_del_tope_responde_que_se_haga_en_el_admin(self):
        with override_settings(ERP_KOOXI_DINERO_MAX=Decimal('1000.00')):
            r = self._proponer()
        self.assertIsNone(r.botones)
        self.assertIn('admin', r.texto)
        self.assertEqual(self.accion().estado, 'RECHAZADA')

    def test_el_tope_se_revisa_otra_vez_al_confirmar(self):
        r = self._proponer()
        with override_settings(ERP_KOOXI_DINERO_MAX=Decimal('1000.00')):
            res = self.confirmar(r)
        self.assertFalse(res.ejecutada)
        self.assertEqual((self.accion().estado, MovimientoDeposito.objects.filter(tipo='DEVOLUCION').count()), ('FALLIDA', 0))

    def test_si_la_custodia_cambia_entre_propuesta_y_confirmacion_no_ejecuta(self):
        r = self._proponer()
        self.deposito.monto = Decimal('2000.00')
        self.deposito.save()
        registrar_recepcion(self.deposito, monto=Decimal('340.00'), metodo='EFECTIVO')
        res = self.confirmar(r)
        self.assertIn('custodia cambió', res.texto)
        self.assertEqual(MovimientoDeposito.objects.filter(tipo='DEVOLUCION').count(), 0)

    def test_sin_deposito_sin_custodia_o_ya_liquidado(self):
        self.assertIsNone(dinero.proponer_devolucion_deposito(TEL_DIR, 99999, 'EFECTIVO').botones)
        otra = cotizacion()
        self.assertIn('no tiene depósito', dinero.proponer_devolucion_deposito(TEL_DIR, otra.pk, 'EFECTIVO').texto)
        asegurar_deposito(otra, Decimal('500.00'))
        self.assertIn('no tiene dinero en custodia', dinero.proponer_devolucion_deposito(TEL_DIR, otra.pk, 'EFECTIVO').texto)
        self.confirmar(self._proponer())
        self.assertIn('ya se liquidó', self._proponer().texto)


def descuento(nombre='Amigo 10%', valor='10.00', tipo='PORCENTAJE', **kw):
    return Descuento.objects.create(nombre=nombre, tipo_valor=tipo, valor=Decimal(valor), modo='MANUAL',
                                    activo=True, **kw)


@TOPE
class DescuentoManualTest(Base):
    def _proponer(self, nombre='Amigo 10%', telefono=TEL_DIR):
        return dinero.proponer_descuento(telefono, self.cot.pk, nombre)

    def test_propuesta_muestra_el_ahorro_con_iva_y_no_aplica_nada(self):
        descuento()
        r = self._proponer()
        self.assertIn('$1,160.00', r.texto)   # 10% de 10,000 + IVA
        self.assertEqual(DescuentoAplicado.objects.count(), 0)
        self.cot.refresh_from_db()
        self.assertEqual(self.cot.precio_final, Decimal('11600.00'))

    def test_confirmar_aplica_por_el_servicio_existente(self):
        d = descuento()
        r = self._proponer('amigo 10%')   # sin importar mayúsculas
        ok = self.confirmar(r)
        self.assertTrue(ok.ejecutada)
        self.cot.refresh_from_db()
        self.assertEqual((self.cot.descuento, self.cot.precio_final), (Decimal('1000.00'), Decimal('10440.00')))
        aplicado = DescuentoAplicado.objects.get()
        self.assertEqual((aplicado.descuento_id, aplicado.modo_aplicacion), (d.pk, 'MANUAL'))
        self.assertEqual(self.accion().descuento_aplicado_id, aplicado.pk)
        self.assertFalse(self.confirmar(r).ejecutada)
        self.assertEqual(DescuentoAplicado.objects.count(), 1)

    def test_solo_manuales_activos_y_no_cortesias(self):
        descuento('Cortesía total', '100.00', es_cortesia=True)
        Descuento.objects.create(nombre='Auto', tipo_valor='PORCENTAJE', valor=Decimal('5'), modo='AUTOMATICO', activo=True)
        Descuento.objects.create(nombre='Inactivo', tipo_valor='PORCENTAJE', valor=Decimal('5'), modo='MANUAL', activo=False)
        for nombre in ('Cortesía total', 'Auto', 'Inactivo', 'No existe'):
            self.assertIsNone(self._proponer(nombre).botones, nombre)
        self.assertEqual(AccionWhatsApp.objects.filter(estado='RECHAZADA').count(), 4)

    def test_no_se_aplica_dos_veces_ni_a_cotizaciones_cerradas(self):
        descuento()
        self.confirmar(self._proponer())
        self.assertIn('ya está aplicado', self._proponer().texto)
        otra = cotizacion(estado='CANCELADA')
        self.assertIn('ya no admite', dinero.proponer_descuento(TEL_DIR, otra.pk, 'Amigo 10%').texto)

    def test_descuento_que_deja_en_cero_se_manda_al_admin(self):
        descuento('Todo', '100.00')
        r = self._proponer('Todo')
        self.assertIsNone(r.botones)
        self.assertIn('cortesía', r.texto)

    def test_arriba_del_tope(self):
        descuento('Mitad', '50.00')   # ahorra 5,800 con IVA
        r = self._proponer('Mitad')
        self.assertIsNone(r.botones)
        self.assertIn('admin', r.texto)
        self.assertEqual(DescuentoAplicado.objects.count(), 0)

    def test_si_cambia_el_importe_antes_de_confirmar_no_ejecuta(self):
        descuento()
        r = self._proponer()
        ItemCotizacion.objects.create(cotizacion=self.cot, descripcion='Extra', cantidad=1,
                                      precio_unitario=Decimal('5000.00'))   # el 10% ya no es $1,000
        res = self.confirmar(r)
        self.assertFalse(res.ejecutada)
        self.assertIn('importe cambió', res.texto)
        self.assertEqual(DescuentoAplicado.objects.count(), 0)

    def test_un_descuento_previo_que_agota_la_base_se_manda_al_admin_al_confirmar(self):
        descuento()
        r = self._proponer()
        DescuentoService.aplicar(self.cot, descuento('Casi todo', '9500.00', 'MONTO_FIJO'), modo='MANUAL')
        res = self.confirmar(r)
        self.assertFalse(res.ejecutada)
        self.assertEqual(DescuentoAplicado.objects.count(), 1)

    def test_solo_direccion(self):
        descuento()
        self.assertIsNone(self._proponer(telefono=TEL_ENC))
        self.assertIsNone(ordenes.responder_boton(TEL_ENC, 'ord:' + 'A' * 32 + ':ok'))


@TOPE
class ReembolsoExternoTest(Base):
    def setUp(self):
        super().setUp()
        Pago.objects.create(cotizacion=self.cot, tipo='INGRESO', metodo='TRANSFERENCIA', monto=Decimal('3000.00'),
                            fecha_pago=self.cot.fecha_evento)

    def _proponer(self, monto='1500.00', medio='TRANSFERENCIA', telefono=TEL_DIR):
        return dinero.proponer_reembolso_externo(telefono, self.cot.pk, monto, medio)

    def test_propuesta_no_crea_pago(self):
        r = self._proponer()
        self.assertIn('$1,500.00', r.texto)
        self.assertIn('ya entregaste', r.texto)
        self.assertEqual(Pago.objects.filter(tipo='REEMBOLSO').count(), 0)

    def test_confirmar_crea_un_reembolso_con_sus_signals_y_sin_openpay(self):
        r = self._proponer()
        with patch('comercial.services_openpay.reembolsar_cargo_openpay') as openpay, \
                self.captureOnCommitCallbacks(execute=True):
            ok = self.confirmar(r)
        openpay.assert_not_called()
        self.assertTrue(ok.ejecutada)
        pago = Pago.objects.get(tipo='REEMBOLSO')
        self.assertEqual((pago.monto, pago.metodo, pago.cotizacion_id), (Decimal('1500.00'), 'TRANSFERENCIA', self.cot.pk))
        a = self.accion()
        self.assertEqual((a.estado, a.pago_id, pago.referencia), ('CONFIRMADA', pago.pk, f'REEMB-WA-{a.pk}'))
        self.assertIn('fuera del sistema', pago.notas)
        self.assertTrue(Poliza.objects.filter(content_type=ContentType.objects.get_for_model(pago), object_id=pago.pk).exists())
        self.assertFalse(self.confirmar(r).ejecutada)
        self.assertEqual(Pago.objects.filter(tipo='REEMBOLSO').count(), 1)

    def test_monto_con_redondeo_half_up(self):
        r = self._proponer('1,500.005')
        self.assertEqual(self.accion().monto, Decimal('1500.01'))
        self.assertIn('$1,500.01', r.texto)

    def test_no_puede_exceder_lo_cobrado(self):
        r = self._proponer('3100.00')
        self.assertIsNone(r.botones)
        self.assertIn('excede', r.texto)
        self.assertEqual(self.accion().estado, 'RECHAZADA')

    def test_el_exceso_tambien_se_revalida_al_confirmar(self):
        r = self._proponer('2000.00')
        Pago.objects.create(cotizacion=self.cot, tipo='REEMBOLSO', metodo='EFECTIVO', monto=Decimal('2000.00'),
                            fecha_pago=self.cot.fecha_evento)
        res = self.confirmar(r)
        self.assertFalse(res.ejecutada)
        self.assertEqual(Pago.objects.filter(tipo='REEMBOLSO').count(), 1)

    def test_montos_y_medios_invalidos(self):
        for monto, medio in (('abc', 'EFECTIVO'), ('0', 'EFECTIVO'), ('-5', 'EFECTIVO'), ('100', 'PLATAFORMA'), ('100', 'CONDONACION')):
            self.assertIsNone(self._proponer(monto, medio).botones, (monto, medio))

    def test_arriba_del_tope(self):
        with override_settings(ERP_KOOXI_DINERO_MAX=Decimal('1000.00')):
            r = self._proponer('1500.00')
        self.assertIsNone(r.botones)
        self.assertIn('admin', r.texto)

    def test_solo_direccion(self):
        self.assertIsNone(self._proponer(telefono=TEL_ENC))
        self.assertEqual(AccionWhatsApp.objects.count(), 0)
