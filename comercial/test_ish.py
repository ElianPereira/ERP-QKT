"""
Tests del Impuesto Sobre Hospedaje (ISH), el impuesto estatal del hospedaje
vendido directo.

Lo que fija este archivo, en orden de importancia:

1. Con `TASA_ISH = 0` (el default) el ERP se comporta EXACTAMENTE como antes
   de existir el ISH: no lo calcula, no lo exhibe y no lo contabiliza. Es la
   garantía de que desplegar esto no mueve un peso hasta que el contador
   confirme la tasa.
2. Con tasa configurada, solo el hospedaje directo lo causa — nunca evento,
   pasadía ni arrendamiento.
3. Airbnb queda fuera por construcción: su ISH lo retiene y entera la
   plataforma, así que no es un pasivo de la Quinta ni entra en su póliza.

Ejecutar: python manage.py test comercial.test_ish --verbosity=2
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, ItemCotizacion, Producto
from comercial.services import calcular_desglose_proporcional
from core_erp import impuestos

TASA_5 = Decimal('0.05')


def _cotizacion(tipo_servicio='HOSPEDAJE', *, precio_base='1000.00',
                tipo_persona='FISICA'):
    cliente = Cliente.objects.create(
        nombre='Huésped Prueba', email='huesped@example.com',
        tipo_persona=tipo_persona,
    )
    cot = Cotizacion.objects.create(
        cliente=cliente,
        tipo_servicio=tipo_servicio,
        nombre_evento='Prueba ISH',
        fecha_evento=timezone.localdate() + timedelta(days=30),
        num_personas=2,
    )
    producto = Producto.objects.create(
        nombre=f'Habitación prueba {tipo_servicio}',
        precio_venta_fijo=Decimal(precio_base),
    )
    ItemCotizacion.objects.create(
        cotizacion=cot, producto=producto, cantidad=1,
        precio_unitario=Decimal(precio_base),
    )
    cot.save()
    cot.refresh_from_db()
    return cot


class TasaISHTest(TestCase):
    """La tasa vive en settings, no en el código, y se valida al leerla."""

    @override_settings(TASA_ISH=Decimal('0'))
    def test_sin_tasa_configurada_el_ish_no_aplica(self):
        self.assertFalse(impuestos.ish_aplica())
        self.assertEqual(impuestos.ish_de(Decimal('1000.00')), Decimal('0.00'))

    @override_settings(TASA_ISH=TASA_5)
    def test_con_tasa_configurada_calcula_sobre_la_base_sin_iva(self):
        self.assertTrue(impuestos.ish_aplica())
        # 5% sobre la contraprestación, NO sobre el IVA.
        self.assertEqual(impuestos.ish_de(Decimal('1000.00')), Decimal('50.00'))

    @override_settings(TASA_ISH=TASA_5)
    def test_redondea_con_half_up_como_todo_importe_monetario(self):
        # 333.33 * 0.05 = 16.6665 -> 16.67 con ROUND_HALF_UP.
        self.assertEqual(impuestos.ish_de(Decimal('333.33')), Decimal('16.67'))

    @override_settings(TASA_ISH=Decimal('5'))
    def test_una_tasa_en_porcentaje_en_vez_de_proporcion_se_rechaza(self):
        # 5 en vez de 0.05 cobraría 500% de impuesto: mejor reventar que
        # cobrarlo.
        with self.assertRaises(ValueError):
            impuestos.ish_de(Decimal('1000.00'))

    @override_settings(TASA_ISH=TASA_5)
    def test_rechaza_float_igual_que_el_resto_del_modulo(self):
        with self.assertRaises(impuestos.ImporteInvalido):
            impuestos.ish_de(1000.00)


class CotizacionISHTest(TestCase):
    """`calcular_totales()` con y sin ISH."""

    @override_settings(TASA_ISH=Decimal('0'))
    def test_sin_tasa_el_total_es_identico_al_de_siempre(self):
        cot = _cotizacion('HOSPEDAJE')
        self.assertEqual(cot.impuesto_hospedaje, Decimal('0.00'))
        self.assertEqual(cot.subtotal, Decimal('1000.00'))
        self.assertEqual(cot.iva, Decimal('160.00'))
        self.assertEqual(cot.precio_final, Decimal('1160.00'))

    @override_settings(TASA_ISH=TASA_5)
    def test_hospedaje_directo_causa_ish_y_sube_el_precio_final(self):
        cot = _cotizacion('HOSPEDAJE')
        self.assertEqual(cot.impuesto_hospedaje, Decimal('50.00'))
        self.assertEqual(cot.iva, Decimal('160.00'))
        # base + IVA + ISH
        self.assertEqual(cot.precio_final, Decimal('1210.00'))

    @override_settings(TASA_ISH=TASA_5)
    def test_evento_pasadia_y_arrendamiento_nunca_causan_ish(self):
        for servicio in ('EVENTO', 'PASADIA', 'ARRENDAMIENTO'):
            with self.subTest(servicio=servicio):
                cot = _cotizacion(servicio)
                self.assertEqual(cot.impuesto_hospedaje, Decimal('0.00'))
                self.assertEqual(cot.precio_final, Decimal('1160.00'))

    @override_settings(TASA_ISH=TASA_5)
    def test_el_ish_no_se_calcula_sobre_el_iva(self):
        cot = _cotizacion('HOSPEDAJE')
        # Si se calculara sobre el total con IVA (1160), daría 58.00.
        self.assertNotEqual(cot.impuesto_hospedaje, Decimal('58.00'))
        self.assertEqual(cot.impuesto_hospedaje, Decimal('50.00'))

    @override_settings(TASA_ISH=TASA_5)
    def test_persona_moral_conserva_su_retencion_ademas_del_ish(self):
        cot = _cotizacion('HOSPEDAJE', tipo_persona='MORAL')
        self.assertEqual(cot.retencion_isr, Decimal('12.50'))  # 1.25% RESICO
        self.assertEqual(cot.impuesto_hospedaje, Decimal('50.00'))
        # 1000 + 160 + 50 - 12.50
        self.assertEqual(cot.precio_final, Decimal('1197.50'))

    @override_settings(TASA_ISH=TASA_5)
    def test_el_descuento_reduce_tambien_la_base_del_ish(self):
        cot = _cotizacion('HOSPEDAJE')
        cot.descuento = Decimal('200.00')
        cot.save()
        cot.refresh_from_db()
        # Base 800 -> ISH 40, IVA 128
        self.assertEqual(cot.impuesto_hospedaje, Decimal('40.00'))
        self.assertEqual(cot.precio_final, Decimal('968.00'))


class TasaCongeladaTest(TestCase):
    """
    La tasa se sella en la cotización; no se relee de la configuración.

    Sin esto, encender `TASA_ISH` le subía el precio a cotizaciones ya
    aceptadas en cuanto alguien las reguardara (editarlas en el admin, o
    moverlas a EJECUTADA), y a una ya pagada le reabría saldo por el
    importe del impuesto.
    """

    def test_una_cotizacion_pagada_no_reabre_saldo_al_encender_la_tasa(self):
        from comercial.models import Pago
        with override_settings(TASA_ISH=Decimal('0')):
            cot = _cotizacion('HOSPEDAJE')
            self.assertEqual(cot.precio_final, Decimal('1160.00'))
            Pago.objects.create(cotizacion=cot, monto=cot.precio_final,
                                metodo='TRANSFERENCIA')
            cot.refresh_from_db()
            self.assertEqual(cot.saldo_pendiente(), Decimal('0.00'))
            cot.cambiar_estado('COTIZADA')

        # Meses después se enciende la tasa y alguien reguarda la cotización.
        with override_settings(TASA_ISH=TASA_5):
            cot.refresh_from_db()
            cot.save()
            cot.refresh_from_db()
            self.assertEqual(cot.impuesto_hospedaje, Decimal('0.00'))
            self.assertEqual(cot.precio_final, Decimal('1160.00'))
            self.assertEqual(cot.saldo_pendiente(), Decimal('0.00'))

    def test_una_cotizacion_nueva_si_toma_la_tasa_vigente(self):
        with override_settings(TASA_ISH=TASA_5):
            cot = _cotizacion('HOSPEDAJE')
            self.assertEqual(cot.tasa_ish_aplicada, TASA_5)
            self.assertEqual(cot.impuesto_hospedaje, Decimal('50.00'))

    def test_un_borrador_toma_la_tasa_al_encenderla(self):
        # El caso de la semana de transición: lo que sigue en borrador no se
        # le ha presentado al cliente todavía, así que sí debe actualizarse.
        with override_settings(TASA_ISH=Decimal('0')):
            cot = _cotizacion('HOSPEDAJE')
            self.assertEqual(cot.estado, 'BORRADOR')
        with override_settings(TASA_ISH=TASA_5):
            cot.save()
            cot.refresh_from_db()
            self.assertEqual(cot.impuesto_hospedaje, Decimal('50.00'))
            self.assertEqual(cot.precio_final, Decimal('1210.00'))

    def test_apagar_la_tasa_no_le_quita_el_ish_a_lo_ya_cotizado(self):
        with override_settings(TASA_ISH=TASA_5):
            cot = _cotizacion('HOSPEDAJE')
            cot.cambiar_estado('COTIZADA')
        with override_settings(TASA_ISH=Decimal('0')):
            cot.refresh_from_db()
            cot.save()
            cot.refresh_from_db()
            self.assertEqual(cot.impuesto_hospedaje, Decimal('50.00'))
            self.assertEqual(cot.precio_final, Decimal('1210.00'))

    @override_settings(TASA_ISH=TASA_5)
    def test_cambiar_items_recalcula_el_ish_con_la_tasa_sellada(self):
        cot = _cotizacion('HOSPEDAJE')
        cot.cambiar_estado('COTIZADA')
        producto = Producto.objects.create(
            nombre='Habitación extra', precio_venta_fijo=Decimal('500.00'))
        ItemCotizacion.objects.create(
            cotizacion=cot, producto=producto, cantidad=1,
            precio_unitario=Decimal('500.00'))
        cot.save()
        cot.refresh_from_db()
        # Base 1500 con la tasa sellada del 5%.
        self.assertEqual(cot.impuesto_hospedaje, Decimal('75.00'))


class DesgloseProporcionalISHTest(TestCase):
    """Cada abono parcial lleva su parte proporcional de ISH."""

    @override_settings(TASA_ISH=Decimal('0'))
    def test_sin_ish_el_desglose_cuadra_como_siempre(self):
        cot = _cotizacion('HOSPEDAJE')
        d = calcular_desglose_proporcional(Decimal('1160.00'), cot)
        self.assertEqual(d['impuesto_hospedaje'], Decimal('0.00'))
        self.assertEqual(
            d['subtotal'] + d['iva'] - d['retencion_isr'] - d['retencion_iva'],
            Decimal('1160.00'),
        )

    @override_settings(TASA_ISH=TASA_5)
    def test_pago_total_reparte_el_ish_completo(self):
        cot = _cotizacion('HOSPEDAJE')
        d = calcular_desglose_proporcional(cot.precio_final, cot)
        self.assertEqual(d['impuesto_hospedaje'], Decimal('50.00'))
        self.assertEqual(d['subtotal'], Decimal('1000.00'))
        self.assertEqual(d['iva'], Decimal('160.00'))

    @override_settings(TASA_ISH=TASA_5)
    def test_anticipo_del_50_por_ciento_lleva_la_mitad_del_ish(self):
        cot = _cotizacion('HOSPEDAJE')
        mitad = Decimal('605.00')  # 1210 / 2
        d = calcular_desglose_proporcional(mitad, cot)
        self.assertEqual(d['impuesto_hospedaje'], Decimal('25.00'))
        # El desglose completo sigue cuadrando contra el importe cobrado.
        suma = (d['subtotal'] + d['iva'] + d['impuesto_hospedaje']
                - d['retencion_isr'] - d['retencion_iva'])
        self.assertEqual(suma, mitad)

    @override_settings(TASA_ISH=TASA_5)
    def test_un_abono_de_importe_arbitrario_sigue_cuadrando_al_centavo(self):
        cot = _cotizacion('HOSPEDAJE')
        for monto in ('100.00', '333.33', '0.07', '1209.99'):
            with self.subTest(monto=monto):
                d = calcular_desglose_proporcional(Decimal(monto), cot)
                suma = (d['subtotal'] + d['iva'] + d['impuesto_hospedaje']
                        - d['retencion_isr'] - d['retencion_iva'])
                self.assertEqual(suma, Decimal(monto))

    @override_settings(TASA_ISH=TASA_5)
    def test_el_iva_del_desglose_se_mantiene_dentro_de_la_tolerancia_sat(self):
        # El ISH se aparta primero justamente para no contaminar esta
        # invariante, que es la que valida el PAC al timbrar.
        cot = _cotizacion('HOSPEDAJE')
        for monto in ('100.00', '333.33', '777.77', '1209.99'):
            with self.subTest(monto=monto):
                d = calcular_desglose_proporcional(Decimal(monto), cot)
                esperado = impuestos.iva_de(d['subtotal'])
                self.assertLessEqual(abs(d['iva'] - esperado), Decimal('0.01'))
