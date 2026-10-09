"""Recibo de nómina con el sistema de documentos (Issue #373, fase 3)."""
import io
from decimal import Decimal

import pdfplumber
from django.test import TestCase, override_settings

from nomina.models import Empleado, ReciboNomina
from nomina.services_recibos import calcular_recibo, generar_recibos
from nomina.views import _transformar_datos_jibble

STORAGES_PRUEBA = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'privado': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}


def _personas():
    # Miércoles 7 h 56 min (se paga 8) y jueves 5 h 30 min (se paga 5).
    return {'ANA PEREZ': {
        'ultima_salida': '2026-10-08 16:30',
        'dias': [
            {'fecha': '2026-10-07', 'entrada': '08:00', 'salida': '15:56', 'duracion_segundos': 7 * 3600 + 56 * 60},
            {'fecha': '2026-10-08', 'entrada': '11:00', 'salida': '16:30', 'duracion_segundos': 5 * 3600 + 30 * 60},
        ],
    }}


class CalculoReciboTest(TestCase):

    def test_totales_en_decimal_con_la_regla_del_90(self):
        empleado = Empleado.objects.create(nombre='ANA PEREZ', tarifa_base=Decimal('62.50'))
        datos, _ = _transformar_datos_jibble(_personas())
        calculo = calcular_recibo(empleado, datos['ANA PEREZ'])
        self.assertEqual(calculo['horas_reales'], Decimal('13.43'))
        self.assertEqual(calculo['horas_a_pagar'], Decimal('13'))
        self.assertEqual(calculo['total'], Decimal('812.50'))
        self.assertEqual(calculo['sin_redondeo'], Decimal('839.38'))
        self.assertEqual([f['dia'] for f in calculo['filas']], ['Mié', 'Jue'])


@override_settings(STORAGES=STORAGES_PRUEBA)
class GenerarReciboTest(TestCase):

    def test_guarda_el_recibo_y_su_pdf(self):
        Empleado.objects.create(nombre='ANA PEREZ', tarifa_base=Decimal('62.50'))
        datos, emision = _transformar_datos_jibble(_personas())
        self.assertEqual(generar_recibos(datos, emision), 1)

        recibo = ReciboNomina.objects.get()
        self.assertEqual(recibo.total_pagado, Decimal('812.50'))
        self.assertEqual(recibo.horas_trabajadas, Decimal('13'))
        self.assertEqual(recibo.periodo, '2026-10-07 al 2026-10-08')
        self.assertTrue(recibo.archivo_pdf.name.split('/')[-1].startswith('QKT_Nomina_ANA-PEREZ_20261007'))

        with recibo.archivo_pdf.open('rb') as archivo, pdfplumber.open(io.BytesIO(archivo.read())) as pdf:
            texto = pdf.pages[0].extract_text()
        self.assertIn('Recibo de nómina', texto)
        self.assertIn(f'NOM-{recibo.pk:03d}', texto)
        self.assertIn('07/10/2026 al 08/10/2026', texto)
        self.assertIn('Emisión: 08/10/2026', texto)
        self.assertIn('Neto a pagar $812.50', texto)
        self.assertIn('Sin redondeo serían $839.38', texto)
        self.assertIn('Recibí de conformidad', texto)
