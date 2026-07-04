"""
Tests del módulo de Descuentos
==============================
Ejecutar: python manage.py test comercial.test_descuentos --verbosity=2
"""
from decimal import Decimal
from datetime import date, timedelta

from django.test import TestCase
from django.contrib.auth.models import User

from comercial.models import (
    Cliente, Cotizacion, ItemCotizacion,
    Descuento, DescuentoAplicado, Temporada, TipoEvento,
)
from comercial.services_descuentos import DescuentoService


class DescuentoBaseTest(TestCase):
    """Utilidades compartidas para armar cotizaciones sin disparar barra."""

    def setUp(self):
        self.user = User.objects.create_user('staff', password='x')
        self.cliente = Cliente.objects.create(nombre='Cliente Test', tipo_persona='FISICA')

    def _cotizacion(self, subtotal, tipo_servicio='EVENTO', tipo_evento=None,
                    fecha=None):
        cot = Cotizacion.objects.create(
            cliente=self.cliente,
            nombre_evento='Test',
            tipo_servicio=tipo_servicio,
            tipo_evento=tipo_evento,
            fecha_evento=fecha or (date.today() + timedelta(days=90)),
            num_personas=100,
            incluye_refrescos=False, incluye_cerveza=False,
            incluye_licor_nacional=False, incluye_licor_premium=False,
            incluye_cocteleria_basica=False, incluye_cocteleria_premium=False,
        )
        ItemCotizacion.objects.create(
            cotizacion=cot, descripcion='Servicio',
            cantidad=Decimal('1'), precio_unitario=Decimal(subtotal),
        )
        cot.refresh_from_db()
        return cot


class MontoMinimoTest(DescuentoBaseTest):

    def _descuento_min_20k(self):
        return Descuento.objects.create(
            nombre='Mínimo 20k', tipo_valor='MONTO_FIJO', valor=Decimal('1000.00'),
            modo='AUTOMATICO', activo=True, monto_minimo=Decimal('20000.00'),
        )

    def test_limite_inferior_no_aplica(self):
        self._descuento_min_20k()
        cot = self._cotizacion('19999.99')
        self.assertEqual(DescuentoService.evaluar_automaticos(cot), [])

    def test_limite_exacto_aplica(self):
        d = self._descuento_min_20k()
        cot = self._cotizacion('20000.00')
        self.assertEqual(DescuentoService.evaluar_automaticos(cot), [d])


class RedondeoDecimalTest(DescuentoBaseTest):

    def test_porcentaje_redondea_half_up_con_decimal(self):
        # 15000.05 * 10% = 1500.005 -> ROUND_HALF_UP -> 1500.01
        d = Descuento.objects.create(
            nombre='10%', tipo_valor='PORCENTAJE', valor=Decimal('10.00'),
            modo='MANUAL', activo=True,
        )
        cot = self._cotizacion('15000.05')
        aplicado = DescuentoService.aplicar(cot, d, usuario=self.user, modo='MANUAL')
        self.assertIsInstance(aplicado.monto_aplicado, Decimal)
        self.assertEqual(aplicado.monto_aplicado, Decimal('1500.01'))
        cot.refresh_from_db()
        self.assertEqual(cot.descuento, Decimal('1500.01'))


class NoAcumulablePrioridadTest(DescuentoBaseTest):

    def test_gana_mayor_prioridad(self):
        Descuento.objects.create(
            nombre='Baja', tipo_valor='MONTO_FIJO', valor=Decimal('500.00'),
            modo='AUTOMATICO', activo=True, acumulable=False, prioridad=5,
        )
        d_alta = Descuento.objects.create(
            nombre='Alta', tipo_valor='MONTO_FIJO', valor=Decimal('700.00'),
            modo='AUTOMATICO', activo=True, acumulable=False, prioridad=10,
        )
        cot = self._cotizacion('20000.00')
        aplicados = DescuentoService.aplicar_automaticos(cot)
        self.assertEqual(len(aplicados), 1)
        self.assertEqual(aplicados[0].descuento_id, d_alta.id)

    def test_empate_prioridad_gana_mayor_monto_no_primero_por_id(self):
        """OBLIGATORIO: igual prioridad, montos distintos → gana mayor monto
        resultante, no el primero por orden de creación/ID."""
        # d_pct se crea PRIMERO (ID menor) pero rinde menos.
        d_pct = Descuento.objects.create(
            nombre='10%', tipo_valor='PORCENTAJE', valor=Decimal('10.00'),
            modo='AUTOMATICO', activo=True, acumulable=False, prioridad=5,
        )
        d_fijo = Descuento.objects.create(
            nombre='Fijo 2000', tipo_valor='MONTO_FIJO', valor=Decimal('2000.00'),
            modo='AUTOMATICO', activo=True, acumulable=False, prioridad=5,
        )
        cot = self._cotizacion('15000.00')  # 10% = 1500 < 2000
        aplicados = DescuentoService.aplicar_automaticos(cot)
        self.assertEqual(len(aplicados), 1)
        self.assertEqual(aplicados[0].descuento_id, d_fijo.id)
        self.assertLess(d_pct.id, d_fijo.id)  # el ganador NO es el de menor ID
        cot.refresh_from_db()
        self.assertEqual(cot.descuento, Decimal('2000.00'))


class AcumulableTest(DescuentoBaseTest):

    def test_dos_acumulables_se_suman(self):
        Descuento.objects.create(
            nombre='Referido', tipo_valor='MONTO_FIJO', valor=Decimal('500.00'),
            modo='AUTOMATICO', activo=True, acumulable=True, prioridad=1,
        )
        Descuento.objects.create(
            nombre='Bono', tipo_valor='MONTO_FIJO', valor=Decimal('300.00'),
            modo='AUTOMATICO', activo=True, acumulable=True, prioridad=1,
        )
        cot = self._cotizacion('20000.00')
        aplicados = DescuentoService.aplicar_automaticos(cot)
        self.assertEqual(len(aplicados), 2)
        cot.refresh_from_db()
        self.assertEqual(cot.descuento, Decimal('800.00'))

    def test_acumulable_mas_no_acumulable_conviven(self):
        # 1 no-acumulable gana + 1 acumulable se suma aparte.
        Descuento.objects.create(
            nombre='Temporada', tipo_valor='MONTO_FIJO', valor=Decimal('1000.00'),
            modo='AUTOMATICO', activo=True, acumulable=False, prioridad=5,
        )
        Descuento.objects.create(
            nombre='Otro no acum', tipo_valor='MONTO_FIJO', valor=Decimal('400.00'),
            modo='AUTOMATICO', activo=True, acumulable=False, prioridad=1,
        )
        Descuento.objects.create(
            nombre='Referido', tipo_valor='MONTO_FIJO', valor=Decimal('500.00'),
            modo='AUTOMATICO', activo=True, acumulable=True, prioridad=1,
        )
        cot = self._cotizacion('20000.00')
        aplicados = DescuentoService.aplicar_automaticos(cot)
        # Gana solo el no-acumulable de prioridad 5 ($1000) + el acumulable ($500)
        self.assertEqual(len(aplicados), 2)
        cot.refresh_from_db()
        self.assertEqual(cot.descuento, Decimal('1500.00'))


class MaxUsosTest(DescuentoBaseTest):

    def test_max_usos_agotado_no_sugiere(self):
        d = Descuento.objects.create(
            nombre='Una vez', tipo_valor='MONTO_FIJO', valor=Decimal('500.00'),
            modo='AUTOMATICO', activo=True, max_usos=1,
        )
        cot1 = self._cotizacion('20000.00')
        DescuentoService.aplicar(cot1, d, modo='AUTOMATICO')
        d.refresh_from_db()
        self.assertEqual(d.usos, 1)

        cot2 = self._cotizacion('20000.00')
        self.assertEqual(DescuentoService.evaluar_automaticos(cot2), [])


class VigenciaTest(DescuentoBaseTest):

    def test_fecha_fuera_de_rango_no_aplica(self):
        Descuento.objects.create(
            nombre='Solo enero', tipo_valor='MONTO_FIJO', valor=Decimal('500.00'),
            modo='AUTOMATICO', activo=True,
            fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 1, 31),
        )
        cot = self._cotizacion('20000.00', fecha=date(2026, 6, 15))
        self.assertEqual(DescuentoService.evaluar_automaticos(cot), [])

    def test_fecha_dentro_de_rango_aplica(self):
        d = Descuento.objects.create(
            nombre='Solo junio', tipo_valor='MONTO_FIJO', valor=Decimal('500.00'),
            modo='AUTOMATICO', activo=True,
            fecha_inicio=date(2026, 6, 1), fecha_fin=date(2026, 6, 30),
        )
        cot = self._cotizacion('20000.00', fecha=date(2026, 6, 15))
        self.assertEqual(DescuentoService.evaluar_automaticos(cot), [d])

    def test_temporada_como_condicion(self):
        temp = Temporada.objects.create(
            nombre='Verano', fecha_inicio=date(2026, 6, 1),
            fecha_fin=date(2026, 8, 31), anio=2026, activo=True,
        )
        d = Descuento.objects.create(
            nombre='Verano', tipo_valor='PORCENTAJE', valor=Decimal('5.00'),
            modo='AUTOMATICO', activo=True, temporada=temp,
        )
        dentro = self._cotizacion('20000.00', fecha=date(2026, 7, 10))
        fuera = self._cotizacion('20000.00', fecha=date(2026, 12, 10))
        self.assertEqual(DescuentoService.evaluar_automaticos(dentro), [d])
        self.assertEqual(DescuentoService.evaluar_automaticos(fuera), [])


class RevertirTest(DescuentoBaseTest):

    def test_revertir_recalcula_descuento_y_total(self):
        d = Descuento.objects.create(
            nombre='Fijo', tipo_valor='MONTO_FIJO', valor=Decimal('2000.00'),
            modo='MANUAL', activo=True,
        )
        cot = self._cotizacion('20000.00')
        total_sin_desc = cot.precio_final  # 20000 + 16% IVA = 23200

        aplicado = DescuentoService.aplicar(cot, d, usuario=self.user, modo='MANUAL')
        cot.refresh_from_db()
        self.assertEqual(cot.descuento, Decimal('2000.00'))
        # base 18000, IVA 2880 -> 20880
        self.assertEqual(cot.precio_final, Decimal('20880.00'))

        DescuentoService.revertir(aplicado)
        cot.refresh_from_db()
        aplicado.refresh_from_db()
        self.assertEqual(cot.descuento, Decimal('0.00'))
        self.assertEqual(cot.precio_final, total_sin_desc)
        # El registro de auditoría NO se borra, solo se desactiva.
        self.assertFalse(aplicado.activo)
        self.assertTrue(DescuentoAplicado.objects.filter(pk=aplicado.pk).exists())


class TiposCondicionTest(DescuentoBaseTest):

    def test_tipo_evento_condiciona(self):
        # Los tipos de evento vienen del seed (migración 0047).
        boda = TipoEvento.objects.get(nombre='Boda')
        xv = TipoEvento.objects.get(nombre='XV Años')
        d = Descuento.objects.create(
            nombre='Solo bodas', tipo_valor='PORCENTAJE', valor=Decimal('10.00'),
            modo='AUTOMATICO', activo=True,
        )
        d.tipos_evento.set([boda])
        cot_boda = self._cotizacion('20000.00', tipo_evento=boda)
        cot_xv = self._cotizacion('20000.00', tipo_evento=xv)
        self.assertEqual(DescuentoService.evaluar_automaticos(cot_boda), [d])
        self.assertEqual(DescuentoService.evaluar_automaticos(cot_xv), [])

    def test_tipo_servicio_condiciona(self):
        d = Descuento.objects.create(
            nombre='Solo arrendamiento', tipo_valor='MONTO_FIJO', valor=Decimal('300.00'),
            modo='AUTOMATICO', activo=True, tipos_servicio=['ARRENDAMIENTO'],
        )
        cot_arr = self._cotizacion('20000.00', tipo_servicio='ARRENDAMIENTO')
        cot_evt = self._cotizacion('20000.00', tipo_servicio='EVENTO')
        self.assertEqual(DescuentoService.evaluar_automaticos(cot_arr), [d])
        self.assertEqual(DescuentoService.evaluar_automaticos(cot_evt), [])
