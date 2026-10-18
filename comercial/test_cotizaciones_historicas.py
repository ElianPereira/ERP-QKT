"""Ventas importadas del sistema anterior: su total nunca se recalcula."""

import importlib
from decimal import Decimal
from io import StringIO

from django.apps import apps
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from comercial.models import Cliente, Cotizacion, ItemCotizacion, Pago

migracion = importlib.import_module('comercial.migrations.0117_cotizaciones_historicas')


class ImportarHistoricoTest(TestCase):
    def setUp(self):
        User.objects.create_superuser('dir', 'dir@x.com', 'x')
        call_command('importar_historico', stdout=StringIO())

    def test_total_y_concepto_originales(self):
        cot = Cotizacion.objects.get(nombre_evento__startswith='[HIST-0147]')
        self.assertTrue(cot.es_historica)
        self.assertEqual(cot.precio_final, Decimal('700.00'))
        self.assertEqual(
            list(cot.items.values_list('descripcion', flat=True)),
            ['Venta registrada en el sistema anterior (folio 147)'],
        )
        self.assertEqual(cot.total_pagado(), Decimal('700.00'))

    def test_guardar_no_agrega_barra_ni_cambia_el_precio(self):
        cot = Cotizacion.objects.get(nombre_evento__startswith='[HIST-0103]')
        cot.notas_cliente = 'editada en el admin'
        cot.save()
        cot.refresh_from_db()
        self.assertEqual(cot.precio_final, Decimal('13578.99'))
        self.assertFalse(cot.items.filter(descripcion__startswith='Servicio de Barra').exists())


class RepararHistoricasTest(TestCase):
    """Estado de producción: barra agregada y precio recalculado."""

    def setUp(self):
        cliente = Cliente.objects.create(nombre='PUBLICO EN GENERAL')
        self.cot = Cotizacion.objects.create(
            cliente=cliente, tipo_servicio='ARRENDAMIENTO', num_personas=1,
            nombre_evento='[HIST-0147] Arrendamiento - Publico En General',
            fecha_evento='2025-11-15', estado='BORRADOR',
        )
        Cotizacion.objects.filter(pk=self.cot.pk).update(estado='CERRADA')
        Pago.objects.bulk_create([Pago(
            cotizacion=self.cot, tipo='INGRESO', fecha_pago='2025-11-15',
            monto=Decimal('700.00'), metodo='EFECTIVO',
        )])
        # Lo que dejó el recálculo en producción (aquí no hay insumos de barra).
        ItemCotizacion.objects.create(
            cotizacion=self.cot, cantidad=1, precio_unitario=Decimal('2813.20'),
            descripcion='Servicio de Barra [Básico]  | 1 Pax - 5 Hrs',
        )
        self.cot.refresh_from_db()
        self.assertEqual(self.cot.precio_final, Decimal('3263.31'))

    def test_repara_total_conceptos_y_conserva_pagos(self):
        migracion.reparar(apps, None)
        self.cot.refresh_from_db()
        self.assertTrue(self.cot.es_historica)
        self.assertEqual(self.cot.precio_final, Decimal('700.00'))
        self.assertEqual(self.cot.saldo_pendiente(), Decimal('0.00'))
        self.assertEqual(
            list(self.cot.items.values_list('descripcion', flat=True)),
            ['Venta registrada en el sistema anterior (folio 147)'],
        )

    def test_es_idempotente(self):
        migracion.reparar(apps, None)
        migracion.reparar(apps, None)
        self.assertEqual(ItemCotizacion.objects.filter(cotizacion=self.cot).count(), 1)
