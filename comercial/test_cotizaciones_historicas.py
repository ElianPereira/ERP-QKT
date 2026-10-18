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

    def test_las_de_iva_se_desglosan_y_todas_quedan_cobradas(self):
        cot = Cotizacion.objects.get(nombre_evento__startswith='[HIST-0149]')
        self.assertEqual(
            (cot.subtotal, cot.iva, cot.precio_final),
            (Decimal('15800.00'), Decimal('2528.00'), Decimal('18328.00')),
        )
        self.assertEqual(cot.items.get().precio_unitario, Decimal('15800.00'))
        self.assertEqual(cot.estado, 'CERRADA')
        self.assertEqual(cot.saldo_pendiente(), Decimal('0.00'))
        for cot in Cotizacion.objects.filter(es_historica=True):
            self.assertLessEqual(cot.saldo_pendiente(), 0, cot.nombre_evento)

    def test_guardar_no_agrega_barra_ni_cambia_el_precio(self):
        cot = Cotizacion.objects.get(nombre_evento__startswith='[HIST-0103]')
        cot.notas_cliente = 'editada en el admin'
        cot.save()
        cot.refresh_from_db()
        self.assertEqual(cot.precio_final, Decimal('13578.99'))
        self.assertFalse(cot.items.filter(descripcion__startswith='Servicio de Barra').exists())

    def test_admin_muestra_sus_conceptos_sin_poder_editarlos(self):
        from django.contrib.admin.sites import site
        from django.test import RequestFactory

        from comercial.admin import ItemCotizacionInline

        cot = Cotizacion.objects.get(nombre_evento__startswith='[HIST-0103]')
        request = RequestFactory().get('/')
        request.user = User.objects.get(username='dir')
        inline = ItemCotizacionInline(Cotizacion, site)
        self.assertFalse(inline.has_add_permission(request, cot))
        self.assertFalse(inline.has_change_permission(request, cot))
        self.assertFalse(inline.has_delete_permission(request, cot))
        self.assertTrue(inline.has_view_permission(request, cot))


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

    def _hist_0149_con_saldo(self):
        cot = Cotizacion.objects.create(
            cliente=self.cot.cliente, tipo_servicio='EVENTO', num_personas=1,
            nombre_evento='[HIST-0149] Evento - Yazmin', fecha_evento='2026-01-10',
        )
        Cotizacion.objects.filter(pk=cot.pk).update(estado='EJECUTADA')
        Pago.objects.bulk_create([Pago(
            cotizacion=cot, tipo='INGRESO', fecha_pago='2025-12-08',
            monto=Decimal('9164.00'), metodo='TRANSFERENCIA',
        )])
        return cot

    def test_desglosa_iva_registra_el_saldo_cobrado_y_cierra(self):
        from contabilidad.models import Poliza

        cot = self._hist_0149_con_saldo()
        migracion.reparar(apps, None)
        cot.refresh_from_db()
        self.assertEqual(
            (cot.subtotal, cot.iva, cot.precio_final, cot.estado),
            (Decimal('15800.00'), Decimal('2528.00'), Decimal('18328.00'), 'CERRADA'),
        )
        self.assertEqual(cot.saldo_pendiente(), Decimal('0.00'))
        saldo = cot.pagos.get(notas=migracion.NOTA_SALDO)
        self.assertEqual((saldo.monto, saldo.metodo), (Decimal('9164.00'), 'OTRO'))
        self.assertFalse(Poliza.objects.exists())
        # Sin IVA: el total es la base.
        self.cot.refresh_from_db()
        self.assertEqual((self.cot.subtotal, self.cot.iva), (Decimal('700.00'), Decimal('0.00')))

    def test_es_idempotente(self):
        cot = self._hist_0149_con_saldo()
        migracion.reparar(apps, None)
        migracion.reparar(apps, None)
        self.assertEqual(ItemCotizacion.objects.filter(cotizacion=self.cot).count(), 1)
        self.assertEqual(cot.pagos.count(), 2)
