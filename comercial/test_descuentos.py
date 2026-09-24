"""
Tests del módulo de Descuentos
==============================
Ejecutar: python manage.py test comercial.test_descuentos --verbosity=2
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from comercial.models import (
    Cliente,
    Cotizacion,
    Descuento,
    DescuentoAplicado,
    ItemCotizacion,
    Producto,
    Temporada,
    TipoEvento,
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

    def _una_vez(self):
        return Descuento.objects.create(
            nombre='Una vez', tipo_valor='MONTO_FIJO', valor=Decimal('500.00'),
            modo='AUTOMATICO', activo=True, max_usos=1,
        )

    def test_max_usos_agotado_no_sugiere(self):
        d = self._una_vez()
        cot1 = self._cotizacion('20000.00')
        DescuentoService.aplicar(cot1, d, modo='AUTOMATICO')
        Cotizacion.objects.filter(pk=cot1.pk).update(estado='CONFIRMADA')
        self.assertEqual(d.usos, 1)

        cot2 = self._cotizacion('20000.00')
        self.assertEqual(DescuentoService.evaluar_automaticos(cot2), [])

    def test_cotizacion_sin_confirmar_no_gasta_usos(self):
        # Una solicitud web abandonada no agota una promoción de "primeros N".
        d = self._una_vez()
        DescuentoService.aplicar(self._cotizacion('20000.00'), d, modo='AUTOMATICO')
        self.assertEqual(d.usos, 0)
        self.assertEqual(DescuentoService.evaluar_automaticos(self._cotizacion('20000.00')), [d])

    def test_descuento_revertido_no_cuenta_como_uso(self):
        d = self._una_vez()
        cot = self._cotizacion('20000.00')
        aplicado = DescuentoService.aplicar(cot, d, modo='AUTOMATICO')
        Cotizacion.objects.filter(pk=cot.pk).update(estado='CONFIRMADA')
        DescuentoService.revertir(aplicado)
        self.assertEqual(d.usos, 0)


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


class CortesiaTest(DescuentoBaseTest):
    """Pasadía/evento regalado: descuento del 100% vía una regla es_cortesia."""

    def test_descuento_100_marca_100_por_ciento_pagado_no_0(self):
        d = Descuento.objects.create(
            nombre='Cortesía', tipo_valor='PORCENTAJE', valor=Decimal('100.00'),
            modo='MANUAL', activo=True, es_cortesia=True,
        )
        cot = self._cotizacion('1500.00')
        DescuentoService.aplicar(cot, d, usuario=self.user, modo='MANUAL')
        cot.refresh_from_db()

        self.assertEqual(cot.precio_final, Decimal('0.00'))
        self.assertEqual(cot.saldo_pendiente(), Decimal('0.00'))
        # precio_final == 0 no debe leerse como "0% cubierto": no se debe nada.
        self.assertEqual(cot.porcentaje_pagado, Decimal('100.0'))

    def test_es_cortesia_distingue_el_descuento_aplicado(self):
        cortesia = Descuento.objects.create(
            nombre='Cortesía', tipo_valor='PORCENTAJE', valor=Decimal('100.00'),
            modo='MANUAL', activo=True, es_cortesia=True,
        )
        promo = Descuento.objects.create(
            nombre='Promo temporada', tipo_valor='PORCENTAJE', valor=Decimal('10.00'),
            modo='MANUAL', activo=True, es_cortesia=False,
        )
        cot = self._cotizacion('1500.00')
        aplicado = DescuentoService.aplicar(cot, cortesia, usuario=self.user, modo='MANUAL')

        self.assertTrue(aplicado.descuento.es_cortesia)
        self.assertFalse(promo.es_cortesia)
        self.assertEqual(
            DescuentoAplicado.objects.filter(descuento__es_cortesia=True).count(), 1
        )


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


class RecalculoPorCambioDeConceptosTest(DescuentoBaseTest):
    """Un descuento en porcentaje sigue siendo ese porcentaje aunque cambien
    los conceptos después de aplicarlo."""

    def _diez_por_ciento(self):
        return Descuento.objects.create(
            nombre='10%', tipo_valor='PORCENTAJE', valor=Decimal('10'), modo='MANUAL')

    def _agregar(self, cot, importe):
        return ItemCotizacion.objects.create(
            cotizacion=cot, descripcion='Extra',
            cantidad=Decimal('1'), precio_unitario=Decimal(importe),
        )

    def test_porcentaje_se_reajusta_al_agregar_un_concepto(self):
        cot = self._cotizacion('10000.00')
        aplicado = DescuentoService.aplicar(cot, self._diez_por_ciento(), usuario=self.user)
        self._agregar(cot, '10000.00')
        cot.refresh_from_db()
        aplicado.refresh_from_db()
        self.assertEqual(cot.descuento, Decimal('2000.00'))
        self.assertEqual(aplicado.monto_aplicado, Decimal('2000.00'))
        self.assertEqual(aplicado.porcentaje_equivalente, Decimal('10.00'))
        self.assertIn('recalculado $1,000.00 → $2,000.00', aplicado.notas)
        self.assertEqual(cot.precio_final, Decimal('20880.00'))  # 18,000 + IVA

    def test_porcentaje_se_reajusta_al_borrar_un_concepto(self):
        cot = self._cotizacion('10000.00')
        extra = self._agregar(cot, '10000.00')
        DescuentoService.aplicar(cot, self._diez_por_ciento(), usuario=self.user)
        extra.delete()
        cot.refresh_from_db()
        self.assertEqual(cot.subtotal, Decimal('10000.00'))
        self.assertEqual(cot.descuento, Decimal('1000.00'))
        self.assertEqual(cot.precio_final, Decimal('10440.00'))

    def test_monto_fijo_topado_recupera_su_valor_si_crece_la_base(self):
        d = Descuento.objects.create(
            nombre='Fijo', tipo_valor='MONTO_FIJO', valor=Decimal('3000'), modo='MANUAL')
        cot = self._cotizacion('1000.00')
        DescuentoService.aplicar(cot, d, usuario=self.user)
        cot.refresh_from_db()
        self.assertEqual(cot.descuento, Decimal('1000.00'))
        self._agregar(cot, '9000.00')
        cot.refresh_from_db()
        self.assertEqual(cot.descuento, Decimal('3000.00'))

    def test_respeta_el_descuento_capturado_a_mano(self):
        cot = self._cotizacion('10000.00')
        Cotizacion.objects.filter(pk=cot.pk).update(descuento=Decimal('500.00'))
        DescuentoService.aplicar(cot, self._diez_por_ciento(), usuario=self.user)
        self._agregar(cot, '10000.00')
        cot.refresh_from_db()
        self.assertEqual(cot.descuento, Decimal('2500.00'))

    def test_descuento_revertido_no_se_recalcula(self):
        cot = self._cotizacion('10000.00')
        DescuentoService.revertir(
            DescuentoService.aplicar(cot, self._diez_por_ciento(), usuario=self.user))
        self._agregar(cot, '10000.00')
        cot.refresh_from_db()
        self.assertEqual(cot.descuento, Decimal('0.00'))


class CortesiaConfirmaTest(DescuentoBaseTest):

    def _cortesia(self, modo='MANUAL'):
        return Descuento.objects.create(
            nombre='Cortesía', tipo_valor='PORCENTAJE', valor=Decimal('100'),
            modo=modo, es_cortesia=True)

    def test_cortesia_manual_total_confirma_la_cotizacion(self):
        cot = self._cotizacion('5000.00')
        DescuentoService.aplicar(cot, self._cortesia(), usuario=self.user)
        cot.refresh_from_db()
        self.assertEqual(cot.precio_final, Decimal('0.00'))
        self.assertEqual(cot.estado, 'CONFIRMADA')

    def test_descuento_automatico_total_no_aparta_fecha(self):
        cot = self._cotizacion('5000.00')
        DescuentoService.aplicar(cot, self._cortesia('AUTOMATICO'), modo='AUTOMATICO')
        cot.refresh_from_db()
        self.assertEqual(cot.estado, 'BORRADOR')

    def test_cortesia_no_confirma_si_la_fecha_ya_esta_apartada(self):
        fecha = date.today() + timedelta(days=60)
        otra = self._cotizacion('5000.00', fecha=fecha)
        Cotizacion.objects.filter(pk=otra.pk).update(estado='CONFIRMADA')
        cot = self._cotizacion('5000.00', fecha=fecha)
        DescuentoService.aplicar(cot, self._cortesia(), usuario=self.user)
        cot.refresh_from_db()
        self.assertEqual(cot.estado, 'BORRADOR')

    def test_cortesia_no_expira_por_falta_de_pago(self):
        cot = self._cotizacion('5000.00')
        DescuentoService.aplicar(cot, self._cortesia(), usuario=self.user)
        cot.refresh_from_db()
        Cotizacion.objects.filter(pk=cot.pk).update(estado='BORRADOR')
        cot.refresh_from_db()
        self.assertIsNone(cot.motivo_expiracion())


class DescuentoPorProductoTest(DescuentoBaseTest):

    def setUp(self):
        super().setUp()
        self.barra = Producto.objects.create(nombre='Barra Premium', precio_venta_fijo=Decimal('1000'))
        self.promo = Descuento.objects.create(
            nombre='50% barra', tipo_valor='PORCENTAJE', valor=Decimal('50'),
            modo='AUTOMATICO', activo=True)
        self.promo.productos.add(self.barra)

    def test_se_calcula_solo_sobre_el_producto(self):
        cot = self._cotizacion('9000.00')
        ItemCotizacion.objects.create(
            cotizacion=cot, producto=self.barra, descripcion='Barra',
            cantidad=Decimal('2'), precio_unitario=Decimal('1000.00'))
        aplicados = DescuentoService.aplicar_automaticos(cot)
        self.assertEqual([a.monto_aplicado for a in aplicados], [Decimal('1000.00')])

    def test_no_aplica_sin_el_producto(self):
        cot = self._cotizacion('9000.00')
        self.assertEqual(DescuentoService.evaluar_automaticos(cot), [])


@override_settings(DEBUG=True, ALLOWED_HOSTS=['*'])
class PromocionEnCotizadorTest(DescuentoBaseTest):
    """El total exhibido en el cotizador público ya trae la promoción."""

    def setUp(self):
        super().setUp()
        Producto.objects.create(
            nombre='Paquete Esencial QKT', precio_venta_fijo=Decimal('4000.00'),
            visible_cotizador=True, cotizador_evento=True, rol_cotizador='BASE_EVENTO')
        self.boda, _ = TipoEvento.objects.get_or_create(nombre='Boda')
        self.promo = Descuento.objects.create(
            nombre='Bodas 10%', tipo_valor='PORCENTAJE', valor=Decimal('10'),
            modo='AUTOMATICO', activo=True, tipos_servicio=['EVENTO'])
        self.promo.tipos_evento.add(self.boda)

    def _total(self, **params):
        base = {'servicio': 'EVENTO', 'personas': '50', 'horas': '6'}
        return self.client.get('/api/cotizador/total/', {**base, **params}).json()

    def test_exhibe_la_promocion_y_el_total_con_descuento(self):
        d = self._total(tipo='Boda')
        self.assertEqual(d['descuentos'], ['Bodas 10%'])
        self.assertEqual(d['total'], '4176.00')  # 3,600 + IVA
        self.assertEqual(d['total_sin_descuento_formateado'], '$4,640.00')
        self.assertEqual(d['ahorro_formateado'], '$464.00')

    def test_sin_promocion_aplicable_no_exhibe_nada(self):
        d = self._total(tipo='XV Años')
        self.assertEqual(d['descuentos'], [])
        self.assertEqual(d['total'], '4640.00')

    def test_vigencia_se_evalua_con_la_fecha_elegida(self):
        self.promo.fecha_inicio = date(2027, 1, 1)
        self.promo.save()
        self.assertEqual(self._total(tipo='Boda', fecha='2026-12-31')['descuentos'], [])
        self.assertEqual(self._total(tipo='Boda', fecha='2027-01-05')['descuentos'], ['Bodas 10%'])
