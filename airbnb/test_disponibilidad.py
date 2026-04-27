"""Tests para verificar_disponibilidad_fecha considerando cotizaciones apartadas."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from comercial.models import Cliente, Cotizacion
from airbnb.validacion_fechas import verificar_disponibilidad_fecha


class DisponibilidadFechaTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(
            nombre='Cliente Test', tipo_persona='FISICA'
        )
        self.fecha = date.today() + timedelta(days=90)

    def _crear_cot(self, estado, fecha=None):
        cot = Cotizacion.objects.create(
            cliente=self.cliente,
            nombre_evento='Evento',
            fecha_evento=fecha or self.fecha,
            num_personas=100,
            incluye_refrescos=False,
            incluye_cerveza=False,
            incluye_licor_nacional=False,
            incluye_licor_premium=False,
            incluye_cocteleria_basica=False,
            incluye_cocteleria_premium=False,
        )
        Cotizacion.objects.filter(pk=cot.pk).update(estado=estado)
        cot.refresh_from_db()
        return cot

    def test_fecha_libre(self):
        disponible, msg = verificar_disponibilidad_fecha(self.fecha)
        self.assertTrue(disponible)
        self.assertIsNone(msg)

    def test_fecha_con_cotizacion_borrador_no_bloquea(self):
        self._crear_cot('BORRADOR')
        disponible, _ = verificar_disponibilidad_fecha(self.fecha)
        self.assertTrue(disponible)

    def test_fecha_con_cotizacion_cotizada_no_bloquea(self):
        self._crear_cot('COTIZADA')
        disponible, _ = verificar_disponibilidad_fecha(self.fecha)
        self.assertTrue(disponible)

    def test_fecha_con_confirmada_bloquea(self):
        self._crear_cot('CONFIRMADA')
        disponible, msg = verificar_disponibilidad_fecha(self.fecha)
        self.assertFalse(disponible)

    def test_excluir_cotizacion_actual(self):
        cot = self._crear_cot('CONFIRMADA')
        disponible, _ = verificar_disponibilidad_fecha(self.fecha, cotizacion_id=cot.pk)
        self.assertTrue(disponible)
