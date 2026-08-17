"""
Tests de Cotizacion.monto_minimo_pago_detalle() — el mínimo que el portal
exige al cliente en su siguiente pago, según tenga o no plan de pagos.
Ejecutar: python manage.py test comercial.test_monto_minimo_pago --verbosity=2
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import Client, TestCase

from comercial.models import Cliente, Cotizacion, ItemCotizacion, Pago, PlanPago, PortalCliente


def _crear_cotizacion(precio_items=Decimal('10000.00'), tipo_servicio='EVENTO', dias_para_evento=120):
    """precio_final termina con IVA incluido (16%) — las pruebas usan
    cot.precio_final, no el precio_items pasado, como base de cálculo."""
    cliente = Cliente.objects.create(nombre='Cliente Plan', tipo_persona='FISICA', telefono='9991234567')
    cotizacion = Cotizacion.objects.create(
        cliente=cliente, nombre_evento='Evento Plan',
        tipo_servicio=tipo_servicio,
        fecha_evento=date.today() + timedelta(days=dias_para_evento),
        incluye_refrescos=False,
    )
    ItemCotizacion.objects.create(
        cotizacion=cotizacion, descripcion='Servicio de evento',
        cantidad=1, precio_unitario=precio_items,
    )
    cotizacion.save()
    cotizacion.refresh_from_db()
    return cotizacion


def _pagar(cotizacion, monto):
    Pago.objects.create(
        cotizacion=cotizacion, tipo='INGRESO', concepto='VENTA',
        monto=monto, metodo='EFECTIVO',
    )


def _crear_plan(cotizacion, montos_y_fechas):
    plan = PlanPago.objects.create(cotizacion=cotizacion)
    for i, (monto, dias_offset) in enumerate(montos_y_fechas, start=1):
        plan.parcialidades.create(
            numero=i, concepto=f'Parcialidad {i}', monto=monto,
            porcentaje=Decimal('0.00'), fecha_limite=date.today() + timedelta(days=dias_offset),
        )
    return plan


class SinPlanDePagosTest(TestCase):
    def test_primer_pago_requiere_50_por_ciento(self):
        cot = _crear_cotizacion(Decimal('10000.00'))
        minimo, motivo = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, (cot.precio_final * Decimal('0.50')))
        self.assertTrue(motivo)

    def test_despues_del_primer_pago_no_hay_minimo(self):
        cot = _crear_cotizacion(Decimal('10000.00'))
        _pagar(cot, cot.precio_final / 2)
        minimo, motivo = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, Decimal('0.00'))
        self.assertEqual(motivo, '')

    def test_saldado_no_pide_minimo(self):
        cot = _crear_cotizacion(Decimal('10000.00'))
        _pagar(cot, cot.precio_final)
        minimo, _ = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, Decimal('0.00'))


class ConPlanDePagosTest(TestCase):
    def test_al_corriente_puede_abonar_libremente(self):
        cot = _crear_cotizacion(Decimal('9000.00'))
        tercio = (cot.precio_final / 3).quantize(Decimal('0.01'))
        _crear_plan(cot, [
            (tercio, -30),  # vencida hace 30 días
            (tercio, 30),   # futura
            (cot.precio_final - 2 * tercio, 60),  # futura
        ])
        _pagar(cot, tercio)  # ya cubrió lo vencido
        minimo, motivo = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, Decimal('0.00'))
        self.assertEqual(motivo, '')

    def test_vencidas_mas_la_actual_se_suman(self):
        """Dos fechas ya pasadas sin pagar + la que toca hoy: se suman las tres."""
        cot = _crear_cotizacion(Decimal('9000.00'))
        cuarto = (cot.precio_final / 4).quantize(Decimal('0.01'))
        _crear_plan(cot, [
            (cuarto, -60),  # vencida
            (cuarto, -30),  # vencida
            (cuarto, 0),    # hoy
            (cot.precio_final - 3 * cuarto, 60),  # futura, no cuenta
        ])
        minimo, motivo = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, cuarto * 3)
        self.assertTrue(motivo)

    def test_pago_parcial_de_lo_vencido_reduce_el_minimo(self):
        cot = _crear_cotizacion(Decimal('6000.00'))
        mitad = (cot.precio_final / 2).quantize(Decimal('0.01'))
        _crear_plan(cot, [(mitad, -10), (cot.precio_final - mitad, 0)])
        _pagar(cot, Decimal('2000.00'))
        minimo, _ = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, cot.precio_final - Decimal('2000.00'))

    def test_minimo_nunca_excede_el_saldo_pendiente(self):
        cot = _crear_cotizacion(Decimal('9000.00'))
        # Parcialidad "vencida" mayor al total real (simula datos desalineados) —
        # el mínimo no debe pedir más de lo que efectivamente falta por pagar.
        _crear_plan(cot, [(cot.precio_final + Decimal('1000.00'), -10)])
        minimo, _ = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, cot.saldo_pendiente())
        self.assertEqual(minimo, cot.precio_final)

    def test_plan_inactivo_se_trata_como_sin_plan(self):
        cot = _crear_cotizacion(Decimal('10000.00'))
        plan = _crear_plan(cot, [(cot.precio_final, -10)])
        plan.activo = False
        plan.save(update_fields=['activo'])
        minimo, motivo = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, cot.precio_final * Decimal('0.50'))  # cae al 50% inicial
        self.assertTrue(motivo)


class PagoTotalPorServicioTest(TestCase):
    """Cerca de la fecha (o en arrendamiento) ya no se acepta anticipo: va el 100%."""

    def test_evento_a_mas_de_quince_dias_sigue_pidiendo_el_50(self):
        cot = _crear_cotizacion(tipo_servicio='EVENTO', dias_para_evento=15)
        minimo, _ = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, cot.precio_final * Decimal('0.50'))

    def test_evento_a_menos_de_quince_dias_pide_el_total(self):
        cot = _crear_cotizacion(tipo_servicio='EVENTO', dias_para_evento=14)
        minimo, motivo = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, cot.precio_final)
        self.assertIn('15 días', motivo)

    def test_pasadia_a_mas_de_siete_dias_sigue_pidiendo_el_50(self):
        cot = _crear_cotizacion(tipo_servicio='PASADIA', dias_para_evento=7)
        minimo, _ = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, cot.precio_final * Decimal('0.50'))

    def test_pasadia_a_menos_de_siete_dias_pide_el_total(self):
        cot = _crear_cotizacion(tipo_servicio='PASADIA', dias_para_evento=6)
        minimo, motivo = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, cot.precio_final)
        self.assertIn('7 días', motivo)

    def test_una_pasadia_a_diez_dias_no_se_trata_como_evento(self):
        # 10 días: dentro del umbral del evento (15) pero fuera del de pasadía (7).
        cot = _crear_cotizacion(tipo_servicio='PASADIA', dias_para_evento=10)
        minimo, _ = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, cot.precio_final * Decimal('0.50'))

    def test_arrendamiento_pide_el_total_aunque_falten_meses(self):
        cot = _crear_cotizacion(tipo_servicio='ARRENDAMIENTO', dias_para_evento=200)
        minimo, motivo = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, cot.precio_final)
        self.assertIn('100%', motivo)

    def test_el_total_exigido_es_el_saldo_no_el_precio_completo(self):
        # Si ya abonó algo, el mínimo es lo que falta, no el total otra vez.
        cot = _crear_cotizacion(tipo_servicio='ARRENDAMIENTO')
        _pagar(cot, Decimal('1000.00'))
        minimo, _ = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, cot.saldo_pendiente())
        self.assertEqual(minimo, cot.precio_final - Decimal('1000.00'))

    def test_saldado_no_pide_nada_aunque_sea_arrendamiento(self):
        cot = _crear_cotizacion(tipo_servicio='ARRENDAMIENTO')
        _pagar(cot, cot.precio_final)
        minimo, motivo = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, Decimal('0.00'))
        self.assertEqual(motivo, '')

    def test_manda_sobre_el_plan_de_pagos(self):
        # Un plan con parcialidades futuras no puede dejar pasar un abono parcial
        # cuando la fecha ya está encima.
        cot = _crear_cotizacion(tipo_servicio='EVENTO', dias_para_evento=5)
        _crear_plan(cot, [(cot.precio_final / 2, -1), (cot.precio_final / 2, 3)])
        minimo, _ = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, cot.precio_final)

    def test_una_fecha_ya_pasada_tambien_pide_el_total(self):
        cot = _crear_cotizacion(tipo_servicio='EVENTO', dias_para_evento=-3)
        minimo, _ = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, cot.precio_final)


class ValidacionServidorTest(TestCase):
    """El endpoint del checkout rechaza montos por debajo del mínimo, no solo el JS."""

    def setUp(self):
        self.cot = _crear_cotizacion(Decimal('10000.00'))
        self.portal = PortalCliente.objects.get(cotizacion=self.cot)
        self.client = Client()

    def test_rechaza_primer_pago_menor_al_50_porciento(self):
        url = f'/mi-evento/{self.portal.token}/pagar-openpay/'
        response = self.client.post(url, {
            'metodo': 'store', 'monto': '1000.00',
        }, secure=True)
        data = response.json()
        self.assertFalse(data['ok'])
        self.assertIn('mínimo', data['mensaje'].lower())
        self.assertEqual(Pago.objects.filter(cotizacion=self.cot).count(), 0)

    def test_rechaza_un_anticipo_en_una_pasadia_a_menos_de_siete_dias(self):
        cot = _crear_cotizacion(tipo_servicio='PASADIA', dias_para_evento=3)
        portal = PortalCliente.objects.get(cotizacion=cot)
        response = self.client.post(
            f'/mi-evento/{portal.token}/pagar-openpay/',
            {'metodo': 'store', 'monto': str(cot.precio_final / 2)},
            secure=True,
        )
        data = response.json()
        self.assertFalse(data['ok'])
        self.assertIn('mínimo', data['mensaje'].lower())
        self.assertEqual(Pago.objects.filter(cotizacion=cot).count(), 0)
