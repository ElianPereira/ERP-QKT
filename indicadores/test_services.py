"""
Services de Indicadores (Issue #402, fase 2) con un febrero de 2026 armado a mano.

Ventas del mes (todas confirmadas, `meses_promedio=1`, `inicio_datos=2026-02-01`):
  4 eventos   : 10,000 + 10,000 + 10,000 + (15,000 - 1,000 de descuento) → ticket neto 11,000
  6 pasadías  : 3,000 c/u, cada una con 1,050 de gasto ligado           → costo medido 35%
  2 hospedajes: 6,000 c/u de 3 noches                                    → costo estimado 35%
Costo de evento estimado 35% (0 ventas con gasto ligado). Así el MC% de todo el mes es 65%:
  MC unitario: evento 7,150 · pasadía 1,950 · hospedaje 3,900 (1,300 por noche)
  ingresos 74,000 · MC 48,100 · días ocupados 4 + 6 + 6 = 16 · MC/día ponderado 3,006.25
Costos fijos de febrero: operación 10,000 + administración 4,000 + venta 6,500 + nómina 12,000 = 32,500.
"""
import random
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase

from indicadores.models import MetaMensual, ParametrosNegocio
from indicadores.services.base import mes_rango, ventana_promedio
from indicadores.services.capacidad import CapacidadService
from indicadores.services.costos_fijos import CostosFijosService
from indicadores.services.embudo import EmbudoComercialService
from indicadores.services.equilibrio import EquilibrioService
from indicadores.services.metas import MetasService
from indicadores.services.mezcla import FIN_DE_SEMANA, MezclaOptimaService, asignar_optimo, asignar_voraz
from indicadores.services.servicio import economia_servicio
from indicadores.services.unitarios import EconomiaUnitariaService

D = Decimal
INICIO, FIN = date(2026, 2, 1), date(2026, 2, 28)
HOY = date(2026, 3, 5)   # febrero ya está cerrado


def cotizacion(nombre, fecha, tipo='EVENTO', estado='CONFIRMADA', salida=None, precio='10000.00',
               descuento='0', canal='', creada=date(2026, 2, 1), personas=50):
    from comercial.models import Cliente, Cotizacion, ItemCotizacion

    cliente = Cliente.objects.create(nombre=nombre, telefono='5555550101')
    cot = Cotizacion.objects.create(
        cliente=cliente, nombre_evento=f'Evento {nombre}', tipo_servicio=tipo, fecha_evento=fecha,
        fecha_salida=salida, num_personas=personas, incluye_refrescos=False)
    ItemCotizacion.objects.create(cotizacion=cot, descripcion='Servicio', cantidad=1, precio_unitario=D(precio))
    Cotizacion.objects.filter(pk=cot.pk).update(
        estado=estado, descuento=D(descuento), como_nos_encontro=canal,
        created_at=timezone_aware(creada))
    cot.refresh_from_db()
    return cot


def timezone_aware(dia):
    from datetime import datetime, time

    from django.utils import timezone
    return timezone.make_aware(datetime.combine(dia, time(12, 0)))


def gasto_ligado(cot, monto, categoria='ALIMENTOS', fecha=date(2026, 2, 10)):
    from comercial.models import Compra, Gasto

    compra = Compra.objects.create(proveedor_nombre='Proveedor', fecha_emision=fecha,
                                   subtotal=D(monto), total=D(monto))
    return Gasto.objects.create(compra=compra, descripcion='Insumo', total_linea=D(monto),
                                categoria=categoria, evento_relacionado=cot, fecha_gasto=fecha)


def poliza(cuenta, monto, fecha=date(2026, 2, 15)):
    """Gasto de `monto` contra bancos, aplicado."""
    from contabilidad.models import CuentaContable, MovimientoContable, Poliza, UnidadNegocio

    banco = CuentaContable.objects.get(codigo_sat='102.02.01')
    p = Poliza.objects.create(
        tipo='E', folio=Poliza.siguiente_folio('E', fecha), fecha=fecha, concepto='Gasto',
        unidad_negocio=UnidadNegocio.objects.get(clave='QUINTA'), estado='APLICADA', origen='MANUAL',
        created_by=User.objects.get_or_create(username='contador')[0])
    MovimientoContable.objects.create(poliza=p, cuenta=cuenta, concepto='Gasto', debe=D(monto), haber=D('0'))
    MovimientoContable.objects.create(poliza=p, cuenta=banco, concepto='Gasto', debe=D('0'), haber=D(monto))


def cuenta_de(operacion):
    from contabilidad.models import ConfiguracionContable
    return ConfiguracionContable.obtener_cuenta(operacion)


class MesArmado(TestCase):
    """Febrero 2026 completo; las pólizas de nómina se agregan según el caso."""

    @classmethod
    def setUpTestData(cls):
        from nomina.models import Empleado, ReciboNomina

        cls.p = ParametrosNegocio.objects.create(
            vigente_desde=date(2026, 2, 1), inicio_datos=INICIO, meses_promedio=1,
            pct_costo_variable_evento=D('35'), pct_costo_variable_pasadia=D('15'),
            pct_costo_variable_hospedaje=D('35'))
        for i, (precio, descuento) in enumerate([('10000', '0')] * 3 + [('15000', '1000')]):
            cotizacion(f'Evento {i}', date(2026, 2, (6, 7, 13, 14)[i]), precio=precio, descuento=descuento,
                       canal='Instagram' if i < 2 else '')
        for i in range(6):
            cot = cotizacion(f'Pasadía {i}', date(2026, 2, (2, 3, 4, 5, 9, 10)[i]), tipo='PASADIA',
                             precio='3000', canal='Instagram' if i < 4 else '')
            gasto_ligado(cot, '1050')
        for entrada, salida in ((date(2026, 2, 16), date(2026, 2, 19)), (date(2026, 2, 23), date(2026, 2, 26))):
            cotizacion(f'Hospedaje {entrada}', entrada, tipo='HOSPEDAJE', salida=salida, precio='6000')
        # Lo que no cuenta como venta ni ocupación
        cotizacion('Cortesía', date(2026, 2, 20), precio='0')
        for i in range(2):
            cotizacion(f'Cotizada {i}', date(2026, 2, 27), estado='COTIZADA')
            cotizacion(f'Cancelada {i}', date(2026, 2, 27), estado='CANCELADA')
        for i in range(3):
            cotizacion(f'Expirada {i}', date(2026, 2, 27), estado='EXPIRADA')
        cotizacion('Arrendamiento', date(2026, 2, 12), tipo='ARRENDAMIENTO', precio='1000')
        cot = cotizacion('Ligada aparte', date(2026, 2, 27), tipo='EVENTO', estado='BORRADOR')
        gasto_ligado(cot, '2100')           # costo de evento sin ligar a una venta real... sí ligado a una cotización
        # Un gasto de costo sin ligar a ninguna cotización
        from comercial.models import Compra, Gasto
        compra = Compra.objects.create(proveedor_nombre='Sin liga', fecha_emision=date(2026, 2, 11),
                                       subtotal=D('2100'), total=D('2100'))
        Gasto.objects.create(compra=compra, descripcion='Suelto', total_linea=D('2100'), categoria='ALIMENTOS',
                             fecha_gasto=date(2026, 2, 11))

        # La contabilidad la pone la prueba: se borra lo que generaron los signals de las compras.
        from contabilidad.models import MovimientoContable, Poliza
        MovimientoContable.objects.all().delete()
        Poliza.objects.all().delete()
        poliza(cuenta_de('GASTO_MANTENIMIENTO'), '10000')
        poliza(cuenta_de('GASTO_CONTABLES'), '4000')
        poliza(cuenta_de('GASTO_PUBLICIDAD'), '6500')

        ana = Empleado.objects.create(nombre='Ana Pech', tarifa_base=D('50.00'))
        for periodo, total, estado in (('2026-02-02 al 2026-02-08', '7000.00', 'PAGADO'),
                                       ('2026-02-09 al 2026-02-15', '5000.00', 'CALCULADO')):
            ReciboNomina.objects.create(empleado=ana, periodo=periodo, horas_trabajadas=D('10'),
                                        tarifa_aplicada=ana.tarifa_base, total_pagado=D(total), estado=estado)

    def economia(self):
        return EconomiaUnitariaService.generar(INICIO, FIN, self.p)

    def precios_de_febrero(self, guardar=False):
        """
        Parámetros con el mismo precio y costo que el histórico de febrero (por venta o por habitación-noche),
        para que las cifras a mano del módulo se mantengan: evento 11,000 / 3,850; pasadía 3,000 / 1,050;
        hospedaje 1,000 / 350 por habitación-noche (6,000 por reservación de 3 noches y 2 habitaciones).
        Sin topes: la mezcla sale como techo.
        """
        from core_erp.impuestos import con_iva

        for tipo, neto, costo in (('evento', '11000', '3850'), ('pasadia', '3000', '1050'),
                                  ('hospedaje', '1000', '350')):
            setattr(self.p, f'precio_venta_{tipo}', con_iva(D(neto)))
            setattr(self.p, f'costo_directo_{tipo}', D(costo))
        if guardar:
            self.p.save()


class EconomiaUnitariaTest(MesArmado):

    def test_valores_a_mano(self):
        e = self.economia()
        ev, pa, ho = (e['tipos'][t] for t in ('EVENTO', 'PASADIA', 'HOSPEDAJE'))
        self.assertEqual((ev['ventas'], ev['tipicas'], ev['ticket'], ev['ticket_unidad']),
                         (4, 4, D('11000.00'), D('11000.00')))
        self.assertEqual((pa['ventas'], pa['tipicas'], pa['ticket'], pa['ticket_unidad']),
                         (6, 6, D('3000.00'), D('3000.00')))
        # Hospedaje: 2 reservaciones de 6,000, 3 noches y 2 habitaciones = 12 habitaciones-noche de 1,000
        self.assertEqual((ho['ventas'], ho['ticket'], ho['ticket_unidad']), (2, D('6000.00'), D('1000.00')))
        self.assertEqual((ho['dias'], ho['noches'], ho['noches_habitacion']), (6, 6, 12))
        self.assertEqual(e['venta_neta'], D('75000.00'))   # 74,000 + 1,000 de arrendamiento
        self.assertEqual(e['otros_venta'], D('1000.00'))
        self.assertEqual(e['ventas'], 12)

    def test_no_cuentan_cortesias_ni_estados_que_no_son_venta(self):
        e = self.economia()
        self.assertEqual(sum(t['ventas'] for t in e['tipos'].values()), 12)
        self.assertEqual(e['tipos']['EVENTO']['ventas'], 4)

    def test_costo_medido_o_de_respaldo(self):
        e = self.economia()
        self.assertEqual(e['tipos']['PASADIA']['fuente_costo'], 'medido')
        self.assertEqual(e['tipos']['PASADIA']['muestra'], 6)
        self.assertEqual(e['tipos']['PASADIA']['costo_pct'], D('0.3500'))   # mide 35%, no el 15% de parámetros
        self.assertEqual(e['tipos']['EVENTO']['fuente_costo'], 'estimado')
        self.assertEqual(e['tipos']['EVENTO']['muestra'], 0)
        self.assertEqual(e['tipos']['EVENTO']['costo_pct'], D('0.3500'))

    def test_una_venta_con_descuento_de_cortesia_cuenta_pero_no_entra_al_precio_tipico(self):
        especial = self.venta_con_cortesia('Asociación', date(2026, 2, 21), tipo='PASADIA', precio='100')
        e = self.economia()['tipos']['PASADIA']
        self.assertEqual((e['ventas'], e['tipicas'], e['dias']), (7, 6, 7))
        self.assertEqual(e['venta_neta'], D('18100.00'))           # 6 × 3,000 + 100
        self.assertEqual((e['ticket'], e['ticket_unidad']), (D('3000.00'), D('3000.00')))
        self.assertEqual(e['muestra'], 6)
        self.assertNotIn(f'COT-{especial.pk:03d}', e['folios'])
        # Sigue contando para lo vendido del mes y para el mes anterior real
        mes = MetasService.generar(2026, 2, self.p, hoy=HOY)
        self.assertEqual(mes['real']['pasadias'], 7)
        mezcla = MezclaOptimaService.generar(2026, 3, self.p, hoy=HOY)
        self.assertEqual(mezcla['real']['unidades']['PASADIA'], 7)

    def test_un_descuento_normal_o_revertido_no_es_precio_especial(self):
        from comercial.models import Descuento, DescuentoAplicado

        normal = Descuento.objects.create(nombre='Promo', valor=D('10'))
        revertida = Descuento.objects.create(nombre='Regalo revertido', valor=D('100'), es_cortesia=True)
        for descuento, activo, fecha in ((normal, True, date(2026, 2, 21)), (revertida, False, date(2026, 2, 22))):
            cot = cotizacion(f'Con {descuento.nombre}', fecha, tipo='PASADIA', precio='3000')
            DescuentoAplicado.objects.create(cotizacion=cot, descuento=descuento, monto_aplicado=D('10'),
                                             modo_aplicacion='MANUAL', activo=activo)
        e = self.economia()['tipos']['PASADIA']
        self.assertEqual((e['ventas'], e['tipicas']), (8, 8))

    def venta_con_cortesia(self, nombre, fecha, tipo='EVENTO', precio='400'):
        from comercial.models import Descuento, DescuentoAplicado

        regla = Descuento.objects.get_or_create(nombre='Precio especial', defaults={
            'valor': D('50'), 'es_cortesia': True})[0]
        cot = cotizacion(nombre, fecha, tipo=tipo, precio=precio)
        DescuentoAplicado.objects.create(cotizacion=cot, descuento=regla, monto_aplicado=D('50'),
                                         modo_aplicacion='MANUAL')
        return cot

    def test_gasto_de_costo_ligado(self):
        # 6 pasadías × 1,050 + 2,100 de la cotización borrador = 8,400 ligados de 10,500
        self.assertEqual(e := EconomiaUnitariaService.pct_gasto_ligado(INICIO, FIN), D('80.0'))
        self.assertIsNotNone(e)

    def test_aviso_si_el_gasto_ligado_es_bajo(self):
        from comercial.models import Compra, Gasto
        compra = Compra.objects.create(proveedor_nombre='Mucho', fecha_emision=date(2026, 2, 12),
                                       subtotal=D('9000'), total=D('9000'))
        Gasto.objects.create(compra=compra, descripcion='Suelto', total_linea=D('9000'), categoria='ALIMENTOS',
                             fecha_gasto=date(2026, 2, 12))
        e = self.economia()
        self.assertEqual(e['pct_gasto_ligado'], D('43.1'))
        self.assertTrue(any('ligado a una cotización' in a for a in e['avisos']))


class CostosFijosTest(MesArmado):

    def test_nomina_de_recibos_cuando_no_hay_poliza_de_sueldos(self):
        cf = CostosFijosService.generar(HOY, self.p)
        mes = cf['meses'][0]
        self.assertEqual(len(cf['meses']), 1)
        self.assertEqual(mes['fuente_nomina'], 'recibos')
        self.assertEqual(mes['por_grupo']['NOMINA'], D('12000.00'))
        self.assertEqual((cf['cf'], cf['fuente']), (D('32500.00'), 'contabilidad'))
        self.assertEqual({g: cf['por_grupo'][g] for g in ('OPERACION', 'ADMINISTRACION', 'VENTA')},
                         {'OPERACION': D('10000.00'), 'ADMINISTRACION': D('4000.00'), 'VENTA': D('6500.00')})

    def test_con_poliza_de_sueldos_no_suma_los_recibos(self):
        poliza(cuenta_de('SUELDOS_SALARIOS'), '12000')
        cf = CostosFijosService.generar(HOY, self.p)
        self.assertEqual(cf['meses'][0]['fuente_nomina'], 'contabilidad')
        self.assertEqual(cf['meses'][0]['nomina_recibos'], D('0.00'))
        self.assertEqual(cf['cf'], D('32500.00'))      # nunca las dos

    def test_el_mes_en_curso_no_cuenta_y_el_override_manda(self):
        cf = CostosFijosService.generar(date(2026, 2, 20), self.p)
        self.assertEqual((cf['meses'], cf['fuente'], cf['cf']), ([], 'sin_datos', D('0.00')))
        self.assertTrue(cf['avisos'])
        self.p.costo_fijo_mensual_override = D('40000')
        cf = CostosFijosService.generar(HOY, self.p)
        self.assertEqual((cf['fuente'], cf['cf'], cf['promedio']), ('presupuestado', D('40000.00'), D('32500.00')))

    def test_gastos_por_clasificar_se_reportan_aparte(self):
        poliza(cuenta_de('GASTOS_GENERALES'), '700')
        poliza(cuenta_de('SUELDOS_SALARIOS'), '12000')
        cf = CostosFijosService.generar(HOY, self.p)
        self.assertEqual(cf['por_clasificar'], D('700.00'))
        self.assertEqual(cf['cf'], D('33200.00'))
        self.assertTrue(any('por clasificar' in a for a in cf['avisos']))


class CapacidadTest(MesArmado):

    def setUp(self):
        self.precios_de_febrero()

    def test_valores_a_mano(self):
        c = CapacidadService.generar(INICIO, FIN, self.p, self.economia())
        ev, pa, ho = (c['servicios'][t] for t in ('EVENTO', 'PASADIA', 'HOSPEDAJE'))
        # Semanas de febrero con vie-sáb-dom: 1 + 2 + 2 + 2 + 2 = 9 eventos
        self.assertEqual((ev['unidades'], ev['ingreso_max'], ev['mc_max']), (9, D('99000.00'), D('64350.00')))
        self.assertEqual((pa['unidades'], pa['ingreso_max'], pa['mc_max']), (28, D('84000.00'), D('54600.00')))
        self.assertEqual((ho['dias_vendibles'], ho['noches_habitacion'], ho['ingreso_max'], ho['mc_max']),
                         (28, 56, D('56000.00'), D('36400.00')))
        self.assertEqual((c['dias_periodo'], c['dias_bloqueados'], c['dias_vendibles']), (28, 0, 28))
        self.assertIn('no se suman', c['nota'])

    def test_descuenta_bloqueos_y_dias_no_permitidos(self):
        from comercial.models import BloqueoFecha
        BloqueoFecha.objects.create(fecha_inicio=date(2026, 2, 6), fecha_fin=date(2026, 2, 7))  # vie y sáb
        c = CapacidadService.generar(INICIO, FIN, self.p, self.economia())
        self.assertEqual(c['servicios']['EVENTO']['unidades'], 8)       # la semana del 2 al 8 queda con 1
        self.assertEqual(c['servicios']['PASADIA']['unidades'], 26)
        self.assertEqual(c['dias_bloqueados'], 2)
        self.p.dias_pasadia = '67'          # solo sábado y domingo
        c = CapacidadService.generar(INICIO, FIN, self.p, self.economia())
        self.assertEqual(c['servicios']['PASADIA']['unidades'], 7)      # 8 fines de semana - sáb 7 bloqueado

    def test_preparacion_reduce_los_eventos(self):
        self.p.dias_preparacion_evento = 2         # cada evento ocupa 3 días: 7 // 3 = 2 por semana completa
        self.p.max_eventos_semana = 3
        c = CapacidadService.generar(INICIO, FIN, self.p, self.economia())
        # semanas completas: min(3, 3 permitidos, 7 // 3 = 2) = 2; la primera (1 día libre) y la última (6): 0 y 2
        self.assertEqual(c['servicios']['EVENTO']['unidades'], 0 + 2 + 2 + 2 + 2)


class EquilibrioTest(MesArmado):

    def setUp(self):
        self.precios_de_febrero()

    def generar(self, **kw):
        return EquilibrioService.generar(2026, 2, self.p, hoy=HOY, **kw)

    def test_valores_a_mano(self):
        eq = self.generar()
        b = eq['base']
        self.assertEqual((eq['cf'], eq['fuente_cf']), (D('32500.00'), 'contabilidad'))
        self.assertEqual(b['mc_pct'], D('0.6500'))
        self.assertEqual(b['pesos'], D('50000.00'))
        self.assertEqual(b['mc_dia'], D('3006.25'))
        self.assertEqual(b['dias'], D('10.81'))
        self.assertEqual(b['ocupacion_pct'], D('38.6'))
        # Hospedaje por habitación-noche: 32,500 / 650 = 50 unidades, que ocupan 25 días-quinta
        self.assertEqual((eq['solo']['EVENTO']['unidades'], eq['solo']['PASADIA']['unidades'],
                          eq['solo']['HOSPEDAJE']['unidades'], eq['solo']['HOSPEDAJE']['dias']), (5, 17, 50, 25))

    def test_con_utilidad_objetivo(self):
        self.p.utilidad_mensual_objetivo = D('6500')
        o = self.generar()['objetivo']
        self.assertEqual((o['objetivo'], o['pesos'], o['dias'], o['ocupacion_pct']),
                         (D('39000.00'), D('60000.00'), D('12.97'), D('46.3')))

    def test_sin_margen_devuelve_none_y_aviso(self):
        eq = self.generar(overrides={f'costo_pct_{t}': D('100') for t in ('EVENTO', 'PASADIA', 'HOSPEDAJE')})
        self.assertIsNone(eq['base']['pesos'])
        self.assertIsNone(eq['base']['dias'])
        self.assertTrue(any('No se puede calcular' in a for a in eq['avisos']))

    def test_base_vacia_no_revienta(self):
        ParametrosNegocio.objects.all().delete()
        sin_datos = ParametrosNegocio(inicio_datos=date(2030, 1, 1))
        eq = EquilibrioService.generar(2030, 6, sin_datos, hoy=date(2030, 6, 10))
        self.assertIsNone(eq['base']['pesos'])
        self.assertTrue(eq['avisos'])


class MezclaTest(MesArmado):

    def setUp(self):
        self.precios_de_febrero()

    def test_evento_primero_y_el_resto_a_pasadia(self):
        m = MezclaOptimaService.generar(2026, 2, self.p, hoy=HOY)
        filas = {f['tipo']: f for f in m['filas']}
        self.assertEqual((filas['EVENTO']['unidades'], filas['PASADIA']['unidades'], filas['HOSPEDAJE']['unidades']),
                         (9, 19, 0))
        self.assertEqual(filas['EVENTO']['segmentos'][FIN_DE_SEMANA], 9)
        self.assertEqual(m['contribucion'], D('101400.00'))
        self.assertEqual(m['utilidad'], D('68900.00'))
        self.assertEqual(m['plan']['utilidad_neta'], D('68900.00'))
        self.assertEqual(m['ocupacion'], D('100.0'))
        self.assertEqual(m['dias_por_segmento'], {'FIN_DE_SEMANA': 12, 'ENTRE_SEMANA': 16})

    def test_demanda_maxima_de_eventos(self):
        m = MezclaOptimaService.generar(2026, 2, self.p, overrides={'demanda_EVENTO': 2}, hoy=HOY)
        filas = {f['tipo']: f for f in m['filas']}
        self.assertEqual((filas['EVENTO']['unidades'], filas['PASADIA']['unidades']), (2, 26))
        self.assertEqual(m['contribucion'], D('65000.00'))

    def test_compara_contra_la_mezcla_real_del_mes_anterior(self):
        self.p.meses_promedio = 2
        m = MezclaOptimaService.generar(2026, 3, self.p, hoy=HOY)
        # Hospedaje real en habitaciones-noche: 2 reservaciones × 3 noches × 2 habitaciones
        self.assertEqual(m['real']['unidades'], {'EVENTO': 4, 'PASADIA': 6, 'HOSPEDAJE': 12})
        self.assertEqual(m['real']['contribucion'], D('48100.00'))
        self.assertEqual(m['contribucion'], D('107250.00'))
        self.assertEqual(m['brecha'], D('59150.00'))

    def test_con_precio_costo_directo_y_topes_sale_un_plan(self):
        # Precios con IVA: neto 3,448.28 / 1,293.10 / 1,637.93 por habitación-noche; el histórico se ignora.
        self.p.precio_venta_evento, self.p.costo_directo_evento = D('4000.00'), D('1000.00')
        self.p.precio_venta_pasadia, self.p.costo_directo_pasadia = D('1500.00'), D('150.00')
        self.p.precio_venta_hospedaje, self.p.costo_directo_hospedaje = D('1900.00'), D('100.00')
        self.p.demanda_max_evento_mes, self.p.demanda_max_pasadia_mes = 4, 10
        self.p.demanda_max_hospedaje_mes = 20
        m = MezclaOptimaService.generar(2026, 2, self.p, hoy=HOY)
        filas = {f['tipo']: f for f in m['filas']}
        self.assertEqual(m['modo'], 'plan')
        self.assertEqual(filas['EVENTO']['precio'], D('3448.28'))
        self.assertEqual(filas['EVENTO']['costo'], D('1000.00'))
        self.assertEqual(filas['EVENTO']['mc_dia'], D('2448.28'))
        self.assertEqual(filas['PASADIA']['mc_dia'], D('1143.10'))
        self.assertEqual(filas['HOSPEDAJE']['mc_dia'], D('3075.86'))      # 1,537.93 por media jornada de la quinta
        self.assertEqual({f['fuente_precio'] for f in m['filas']}, {'parámetros'})
        self.assertEqual({f['fuente_costo'] for f in m['filas']}, {'parámetros'})
        self.assertEqual((filas['EVENTO']['unidades'], filas['PASADIA']['unidades'], filas['HOSPEDAJE']['unidades']),
                         (4, 10, 20))
        self.assertEqual(m['contribucion'], 4 * D('2448.28') + 10 * D('1143.10') + 20 * D('1537.93'))

    def test_sin_costo_directo_usa_el_porcentaje_de_respaldo(self):
        self.p = ParametrosNegocio(inicio_datos=INICIO, meses_promedio=1, precio_venta_pasadia=D('1500.00'),
                                   pct_costo_variable_pasadia=D('15'))
        m = MezclaOptimaService.generar(2026, 2, self.p, hoy=HOY)
        fila = next(f for f in m['filas'] if f['tipo'] == 'PASADIA')
        self.assertEqual((fila['precio'], fila['costo'], fila['fuente_costo']), (D('1293.10'), D('193.97'), 'estimado'))

    def test_sin_precio_ni_muestra_suficiente_no_entra_y_avisa_con_los_folios(self):
        self.p.precio_venta_evento = None
        m = MezclaOptimaService.generar(2026, 2, self.p, hoy=HOY)
        evento = next(f for f in m['filas'] if f['tipo'] == 'EVENTO')
        self.assertEqual((evento['unidades'], evento['precio']), (0, None))
        folios = [f'COT-{c.pk:03d}' for c in self.ventas_de('EVENTO')]
        aviso = next(a for a in m['avisos'] if a.startswith('Evento: sin precio de venta en Parámetros'))
        self.assertIn('solo 4 de 5 ventas típicas', aviso)
        for folio in folios:
            self.assertIn(folio, aviso)

    def test_con_cinco_ventas_tipicas_el_historico_vale_como_precio(self):
        m = MezclaOptimaService.generar(2026, 2, ParametrosNegocio(inicio_datos=INICIO, meses_promedio=1), hoy=HOY)
        pasadia = next(f for f in m['filas'] if f['tipo'] == 'PASADIA')
        self.assertEqual((pasadia['precio'], pasadia['fuente_precio'], pasadia['fuente_costo']),
                         (D('3000.00'), 'histórico', 'medido'))
        self.assertTrue(any(a.startswith('Pasadía: sin precio de venta en Parámetros; se usa el promedio de 6')
                            for a in m['avisos']))

    def test_costo_directo_que_rebasa_el_precio_saca_al_servicio(self):
        self.p.precio_venta_evento, self.p.costo_directo_evento = D('1000.00'), D('900.00')
        m = MezclaOptimaService.generar(2026, 2, self.p, hoy=HOY)
        self.assertEqual(next(f for f in m['filas'] if f['tipo'] == 'EVENTO')['unidades'], 0)
        self.assertTrue(any('no entra a la mezcla' in a for a in m['avisos']))

    def test_la_simulacion_manda_sobre_parametros(self):
        self.p.precio_venta_pasadia, self.p.costo_directo_pasadia = D('1500.00'), D('150.00')
        m = MezclaOptimaService.generar(2026, 2, self.p, overrides={'ticket_PASADIA': D('2000')}, hoy=HOY)
        fila = next(f for f in m['filas'] if f['tipo'] == 'PASADIA')
        self.assertEqual((fila['precio'], fila['costo'], fila['fuente_precio']), (D('2000.00'), D('150.00'), 'simulado'))

    def test_la_mezcla_real_de_hospedaje_se_mide_en_habitaciones_noche(self):
        m = MezclaOptimaService.generar(2026, 3, self.p, hoy=HOY)
        previo = EconomiaUnitariaService.generar(date(2026, 2, 1), date(2026, 2, 28), parametros=self.p)
        self.assertEqual(m['real']['unidades']['HOSPEDAJE'], previo['tipos']['HOSPEDAJE']['noches_habitacion'])

    def ventas_de(self, tipo):
        from comercial.models import Cotizacion
        return Cotizacion.objects.filter(tipo_servicio=tipo, estado='CONFIRMADA', fecha_evento__gte=INICIO,
                                         fecha_evento__lte=FIN).exclude(precio_final=0)

    def test_la_asignacion_es_optima_contra_fuerza_bruta(self):
        casos = [
            # (días fin de semana, días entre semana, tope eventos, tope pasadías, MC evento, pasadía, hospedaje/día)
            (4, 4, 3, 3, 700, 200, 150),
            (4, 4, 3, None, 700, 200, 150),
            (3, 5, 2, 4, 900, 300, 250),
            (5, 2, 4, 1, 500, 260, 240),
            (6, 6, 2, 2, 800, 100, 90),
            (2, 6, 5, 2, 600, 500, 400),
            # Estancias de 2 días: ceder una pasadía completa una estancia (los casos donde el voraz puro pierde)
            (1, 4, 3, 3, 508, 144, 108),
            (5, 1, 0, 4, 497, 341, 148),
            (4, 3, 0, 1, 827, 423, 277),
        ]
        rng = random.Random(7)  # noqa: S311 - semilla fija para reproducir el caso, no es criptografía
        for _ in range(120):
            mc_p = rng.randint(100, 500)
            casos.append((rng.randint(1, 6), rng.randint(1, 6), rng.randint(0, 5),
                          rng.choice([None, 0, 1, 2, 3, 4, 5]), rng.randint(mc_p + 1, 900), mc_p,
                          rng.randint(40, mc_p)))
        todos = ('EVENTO', 'PASADIA', 'HOSPEDAJE')
        for fs, es, tope_e, tope_p, mc_e, mc_p, mc_h in casos:
            bolsas = {(FIN_DE_SEMANA, todos): D(fs), ('ENTRE_SEMANA', ('PASADIA', 'HOSPEDAJE')): D(es)}
            servicios = [
                {'tipo': 'EVENTO', 'dias_unidad': D(1), 'mc': D(mc_e), 'tope': tope_e},
                {'tipo': 'PASADIA', 'dias_unidad': D(1), 'mc': D(mc_p), 'tope': tope_p},
                {'tipo': 'HOSPEDAJE', 'dias_unidad': D(2), 'mc': D(mc_h * 2), 'tope': None},
            ]
            mc = {'EVENTO': mc_e, 'PASADIA': mc_p, 'HOSPEDAJE': mc_h * 2}
            asignado = asignar_optimo(bolsas, servicios)
            optimo = sum(n * mc[t] for (t, _), n in asignado.items())
            self.assertEqual(optimo, self._fuerza_bruta(fs, es, tope_e, tope_p, mc),
                             msg=str((fs, es, tope_e, tope_p, mc_e, mc_p, mc_h)))

    @staticmethod
    def _fuerza_bruta(fs, es, tope_e, tope_p, mc):
        mejor = 0
        for e in range(0, min(tope_e, fs) + 1):
            for p_fs in range(0, fs - e + 1):
                for p_es in range(0, es + 1):
                    if tope_p is not None and p_fs + p_es > tope_p:
                        continue
                    for h_fs in range(0, (fs - e - p_fs) // 2 + 1):
                        for h_es in range(0, (es - p_es) // 2 + 1):
                            valor = (e * mc['EVENTO'] + (p_fs + p_es) * mc['PASADIA']
                                     + (h_fs + h_es) * mc['HOSPEDAJE'])
                            mejor = max(mejor, valor)
        return mejor


class EmbudoTest(MesArmado):

    def test_valores_a_mano(self):
        e = EmbudoComercialService.generar(INICIO, FIN, whatsapp=False)
        t = e['total']
        # 12 ventas + cortesía + arrendamiento = 14 en estado de venta; + 2 cotizadas + 2 canceladas + 3 expiradas + 1 borrador
        self.assertEqual((t['solicitudes'], t['ventas'], t['expiradas'], t['canceladas']), (22, 14, 3, 2))
        self.assertEqual((t['tasa_conversion'], t['tasa_expiracion'], t['tasa_cancelacion']),
                         (D('63.6'), D('13.6'), D('9.1')))
        canales = {c['nombre']: c['solicitudes'] for c in e['por_canal']}
        self.assertEqual(canales, {'Instagram': 6, 'Sin dato': 16})
        self.assertEqual(e['gasto_marketing'], D('6500.00'))
        self.assertEqual(e['costo_por_solicitud'], D('295.45'))
        self.assertEqual(e['cac'], D('464.29'))
        self.assertEqual(e['roas'], D('11.54'))        # 75,000 de venta neta nueva / 6,500
        self.assertIsNone(e['dias_a_confirmar'])

    def test_whatsapp_reutiliza_el_tablero_de_kooxi(self):
        with mock.patch('comunicacion.services_tablero.metricas_periodo',
                        return_value={'conversion': {'nuevas': 7}}) as m:
            e = EmbudoComercialService.generar(INICIO, FIN)
        m.assert_called_once_with(INICIO, FIN)
        self.assertEqual(e['whatsapp'], {'nuevas': 7})


class MetasTest(MesArmado):

    def setUp(self):
        self.precios_de_febrero()

    def test_cascada_a_mano(self):
        m = MetasService.generar(2026, 2, self.p, hoy=HOY)
        self.assertEqual(m['venta_necesaria'], D('50000.00'))
        self.assertEqual(m['ticket_ponderado'], D('6166.67'))          # 74,000 / 12 ventas
        self.assertEqual(m['ventas_necesarias'], 9)                    # ceil(50,000 / 6,166.67)
        self.assertEqual(m['tasa_conversion'], D('63.6'))              # 14 de 22 solicitudes
        self.assertEqual(m['solicitudes_necesarias'], 15)              # ceil(9 / 0.6364)
        self.assertEqual(m['costo_por_solicitud'], D('295.45'))        # 6,500 / 22
        self.assertEqual(m['presupuesto_marketing'], D('4431.75'))
        self.assertEqual(m['avisos'], [])

    def test_avisa_si_la_meta_no_cabe_en_la_capacidad(self):
        self.p.utilidad_mensual_objetivo = D('5000000')
        m = MetasService.generar(2026, 2, self.p, hoy=HOY)
        self.assertTrue(any('no cabe en la capacidad' in a for a in m['avisos']), m['avisos'])

    def test_avance_contra_la_meta_activa(self):
        MetaMensual.objects.create(anio=2026, mes=2, meta_venta_neta=D('100000'), meta_ocupacion_pct=D('50'),
                                   meta_eventos=8, meta_pasadias=12, meta_noches=10,
                                   presupuesto_marketing=D('13000'))
        m = MetasService.generar(2026, 2, self.p, hoy=date(2026, 2, 15))
        self.assertEqual(m['avance']['venta_neta']['real'], D('75000.00'))
        self.assertEqual(m['avance']['venta_neta']['pct'], D('75.0'))
        self.assertEqual(m['avance']['eventos']['real'], 4)
        self.assertEqual(m['avance']['pasadias']['pct'], D('50.0'))
        self.assertEqual(m['avance']['marketing']['real'], D('6500.00'))
        # Lo realizado al día 15 es menor que lo ya confirmado para todo el mes
        self.assertLess(m['real']['venta_realizada'], m['real']['venta_neta'])

    def test_sin_meta_no_hay_avance(self):
        self.assertIsNone(MetasService.generar(2026, 2, self.p, hoy=HOY)['avance'])


class FuenteUnicaTest(MesArmado):
    """Capacidad, equilibrio y mezcla leen el precio y el margen de `economia_servicio`."""

    def paneles(self, overrides=None):
        economia = self.economia()
        capacidad = CapacidadService.generar(INICIO, FIN, self.p, economia, overrides)
        equilibrio = EquilibrioService.generar(2026, 2, self.p, overrides, HOY, economia=economia, capacidad=capacidad)
        mezcla = MezclaOptimaService.generar(2026, 2, self.p, overrides, HOY, economia=economia)
        return economia, capacidad, equilibrio, mezcla

    def comprobar(self, overrides=None):
        economia, capacidad, equilibrio, mezcla = self.paneles(overrides)
        filas = {f['tipo']: f for f in mezcla['filas']}
        for tipo in ('EVENTO', 'PASADIA', 'HOSPEDAJE'):
            esperado = economia_servicio(tipo, economia['tipos'][tipo], self.p, overrides)
            cap, eq = capacidad['servicios'][tipo], equilibrio['servicios'][tipo]
            self.assertEqual(cap['ticket'], esperado['ticket'], tipo)
            self.assertEqual(eq['ticket'], esperado['ticket'], tipo)
            self.assertEqual(filas[tipo]['precio'], esperado['ticket'], tipo)
            self.assertEqual(eq['mc'], esperado['mc'], tipo)
            if esperado['mc'] is not None and esperado['mc'] > 0:
                self.assertEqual(equilibrio['solo'][tipo]['mc'], esperado['mc'], tipo)
                self.assertEqual(cap['mc_max'], esperado['mc'] * cap['unidades'], tipo)
                self.assertEqual(filas[tipo]['costo'], esperado['costo'], tipo)
        return economia_servicio

    def test_sin_parametros_solo_vale_el_historico_con_muestra(self):
        self.comprobar()
        _, capacidad, equilibrio, mezcla = self.paneles()
        self.assertEqual(capacidad['servicios']['PASADIA']['ticket'], D('3000.00'))
        for tipo in ('EVENTO', 'HOSPEDAJE'):           # 4 y 2 ventas: menos de 5, sin precio
            self.assertIsNone(capacidad['servicios'][tipo]['ticket'])
            self.assertIsNone(capacidad['servicios'][tipo]['ingreso_max'])
            self.assertIsNone(equilibrio['servicios'][tipo]['ticket'])
            self.assertEqual(next(f for f in mezcla['filas'] if f['tipo'] == tipo)['unidades'], 0)
        self.assertEqual(equilibrio['base']['ventas_base'], 6)         # solo la pasadía pesa en el equilibrio

    def test_con_parametros_el_mismo_precio_y_margen_en_todos_los_paneles(self):
        self.precios_de_febrero()
        self.comprobar()
        _, capacidad, equilibrio, _ = self.paneles()
        self.assertEqual(capacidad['servicios']['HOSPEDAJE']['ticket'], D('1000.00'))
        self.assertEqual(equilibrio['servicios']['HOSPEDAJE']['mc'], D('650.00'))

    def test_con_una_simulacion_tambien(self):
        self.precios_de_febrero()
        self.comprobar({'ticket_EVENTO': D('20000'), 'costo_pct_PASADIA': D('50'), 'ticket_HOSPEDAJE': D('2000')})

    def test_sin_muestra_los_avisos_traen_los_folios_en_cada_panel(self):
        _, capacidad, equilibrio, mezcla = self.paneles()
        for avisos in (capacidad['avisos'], equilibrio['avisos'], mezcla['avisos']):
            aviso = next(a for a in avisos if a.startswith('Evento: sin precio de venta'))
            self.assertIn('Captura el precio en Parámetros', aviso)
            self.assertIn('COT-', aviso)


class HospedajePorHabitacionNocheTest(TestCase):

    TODOS = ('EVENTO', 'PASADIA', 'HOSPEDAJE')

    def servicios(self, tope_hospedaje=None, tope_evento=5):
        return [
            {'tipo': 'HOSPEDAJE', 'dias_unidad': D(1) / 2, 'mc': D('800'), 'tope': tope_hospedaje},   # 1,600 por día
            {'tipo': 'EVENTO', 'dias_unidad': D(1), 'mc': D('500'), 'tope': tope_evento},
        ]

    def test_dos_habitaciones_noche_ocupan_un_dia_y_el_evento_no_lo_usa(self):
        asignado = asignar_voraz({(FIN_DE_SEMANA, self.TODOS): D(1)}, self.servicios(tope_hospedaje=2))
        self.assertEqual(asignado, {('HOSPEDAJE', FIN_DE_SEMANA): 2})

    def test_tres_habitaciones_noche_ocupan_dos_dias(self):
        asignado = asignar_voraz({(FIN_DE_SEMANA, self.TODOS): D(3)}, self.servicios(tope_hospedaje=3))
        self.assertEqual(asignado, {('HOSPEDAJE', FIN_DE_SEMANA): 3, ('EVENTO', FIN_DE_SEMANA): 1})

    def test_el_resto_de_los_dias_se_llena_en_enteros(self):
        asignado = asignar_voraz({(FIN_DE_SEMANA, self.TODOS): D(5)}, self.servicios())
        self.assertEqual(asignado, {('HOSPEDAJE', FIN_DE_SEMANA): 10})
        self.assertEqual(asignar_optimo({(FIN_DE_SEMANA, self.TODOS): D(5)}, self.servicios()), asignado)

    def test_el_plan_del_mes_cuenta_los_dias_redondeados_hacia_arriba(self):
        p = ParametrosNegocio(
            precio_venta_hospedaje=D('1160.00'), costo_directo_hospedaje=D('300'), demanda_max_hospedaje_mes=3,
            dias_hospedaje='1234567', dias_evento='', dias_pasadia='', costo_fijo_mensual_override=D('0'))
        m = MezclaOptimaService.generar(2026, 4, p, hoy=date(2026, 4, 10))
        hospedaje = next(f for f in m['filas'] if f['tipo'] == 'HOSPEDAJE')
        self.assertEqual((hospedaje['unidades'], hospedaje['dias'], hospedaje['precio']), (3, D('2.00'), D('1000.00')))
        self.assertEqual(m['dias_ocupados'], D('2.00'))
        self.assertIn('las dos habitaciones se rentan la misma noche', m['nota_hospedaje'])


class EjemploFijoTest(TestCase):
    """
    Abril de 2026 (30 días, sin bloqueos), días de venta por defecto, costos fijos $3,000.
    Precios con IVA / costo directo / tope: Evento 12,000 / 4,000 / 4; Pasadía 2,000 / 300 / 6;
    Hospedaje (por habitación-noche) 1,000 / 120 / 20.

    A mano, netos: evento 12,000 / 1.16 = 10,344.83 (MC 6,344.83 por día); pasadía 1,724.14 (MC 1,424.14);
    hospedaje 862.07 (MC 742.07 por habitación-noche = 1,484.14 por día-quinta). Orden por MC/día:
    evento, hospedaje, pasadía. Los días sobran (4 + 10 + 6 de 30), así que se llenan los tres topes.
    """

    def parametros(self, **extra):
        datos = dict(
            costo_fijo_mensual_override=D('3000'), precio_venta_evento=D('12000'), costo_directo_evento=D('4000'),
            demanda_max_evento_mes=4, precio_venta_pasadia=D('2000'), costo_directo_pasadia=D('300'),
            demanda_max_pasadia_mes=6, precio_venta_hospedaje=D('1000'), costo_directo_hospedaje=D('120'),
            demanda_max_hospedaje_mes=20)
        datos.update(extra)
        return ParametrosNegocio(**datos)

    def generar(self, p):
        return MezclaOptimaService.generar(2026, 4, p, hoy=date(2026, 4, 10))

    def test_el_plan_llena_los_topes_y_la_utilidad_neta_coincide_con_el_calculo_a_mano(self):
        m = self.generar(self.parametros(utilidad_mensual_objetivo=D('40000')))
        self.assertEqual(m['modo'], 'plan')
        filas = {f['tipo']: f for f in m['filas']}
        self.assertEqual((filas['EVENTO']['unidades'], filas['PASADIA']['unidades'], filas['HOSPEDAJE']['unidades']),
                         (4, 6, 20))
        self.assertEqual(filas['EVENTO']['mc_dia'], D('6344.83'))
        self.assertEqual(filas['HOSPEDAJE']['mc_dia'], D('1484.14'))
        self.assertEqual(filas['PASADIA']['mc_dia'], D('1424.14'))
        venta = 4 * D('10344.83') + 6 * D('1724.14') + 20 * D('862.07')                 # 68,965.56
        costo = 4 * D('4000') + 6 * D('300') + 20 * D('120')                            # 20,200
        plan = m['plan']
        self.assertEqual((plan['venta'], plan['costo_directo']), (venta, costo))
        self.assertEqual(plan['contribucion'], D('48765.56'))
        self.assertEqual((plan['cf'], plan['utilidad_neta']), (D('3000.00'), D('45765.56')))
        self.assertEqual((plan['utilidad_objetivo'], plan['diferencia_objetivo']), (D('40000.00'), D('5765.56')))
        self.assertEqual(plan['frase'], 'Vende 4 eventos (4 en fin de semana), 6 pasadías y 20 noches-habitación')

    def test_cuando_los_dias_alcanzan_primero_va_lo_que_mas_deja_por_dia(self):
        p = self.parametros(demanda_max_pasadia_mes=100, demanda_max_hospedaje_mes=100)
        filas = {f['tipo']: f for f in self.generar(p)['filas']}
        # Los 4 eventos toman 4 días; los 26 restantes van a hospedaje (52 habitaciones-noche), que deja más por día
        self.assertEqual((filas['EVENTO']['unidades'], filas['PASADIA']['unidades'], filas['HOSPEDAJE']['unidades']),
                         (4, 0, 52))

    def test_escenarios_cumplen_utilidad_neta_igual_contribucion_menos_cf_y_respetan_topes(self):
        m = self.generar(self.parametros())
        nombres = [e['nombre'] for e in m['escenarios']]
        self.assertEqual(nombres, ['Plan óptimo', 'Mes anterior (real)', 'Solo eventos', 'Solo pasadías',
                                   'Solo hospedaje'])
        topes = {'EVENTO': 4, 'PASADIA': 6, 'HOSPEDAJE': 20}
        for e in m['escenarios']:
            self.assertEqual(e['utilidad_neta'], e['contribucion'] - e['cf'], e['nombre'])
            self.assertEqual(e['contribucion'], e['venta'] - e['costo_directo'], e['nombre'])
        por_nombre = {e['nombre']: e for e in m['escenarios']}
        for solo, tipo in (('Solo eventos', 'EVENTO'), ('Solo pasadías', 'PASADIA'), ('Solo hospedaje', 'HOSPEDAJE')):
            unidades = por_nombre[solo]['unidades']
            self.assertEqual(unidades[tipo], topes[tipo], solo)       # los días alcanzan: llega al tope, no lo pasa
            self.assertEqual(sum(n for t, n in unidades.items() if t != tipo), 0, solo)
        self.assertEqual(por_nombre['Solo eventos']['utilidad_neta'], 4 * D('6344.83') - D('3000'))
        self.assertEqual(por_nombre['Mes anterior (real)']['unidades'], {'EVENTO': 0, 'PASADIA': 0, 'HOSPEDAJE': 0})
        self.assertEqual(por_nombre['Mes anterior (real)']['utilidad_neta'], D('-3000.00'))

    def test_solo_x_no_pasa_su_tope_aunque_sobren_dias(self):
        p = self.parametros(demanda_max_pasadia_mes=3)
        solo = next(e for e in self.generar(p)['escenarios'] if e['nombre'] == 'Solo pasadías')
        self.assertEqual(solo['unidades']['PASADIA'], 3)

    def test_un_servicio_sin_precio_se_omite_de_su_escenario_y_se_avisa(self):
        m = self.generar(self.parametros(precio_venta_pasadia=None, costo_directo_pasadia=None,
                                         demanda_max_pasadia_mes=None))
        self.assertNotIn('Solo pasadías', [e['nombre'] for e in m['escenarios']])
        self.assertTrue(any('Solo pasadías' in a and 'omitido' in a for a in m['avisos']))

    def test_sin_un_tope_es_un_techo_teorico(self):
        m = self.generar(self.parametros(demanda_max_hospedaje_mes=None))
        self.assertEqual(m['modo'], 'techo')
        self.assertTrue(any('Falta el tope al mes de Hospedaje' in a for a in m['avisos']))
        self.assertEqual(m['escenarios'][0]['nombre'], 'Techo teórico')
        # Sin tope, el hospedaje se come todos los días que le deja el evento
        self.assertGreater(next(f for f in m['filas'] if f['tipo'] == 'HOSPEDAJE')['unidades'], 20)
        self.assertEqual(self.generar(self.parametros())['modo'], 'plan')

    def test_sin_ningun_dato_la_mezcla_no_revienta(self):
        m = self.generar(ParametrosNegocio())
        self.assertEqual(m['plan']['frase'], 'Sin servicios que vender con los datos actuales.')
        self.assertEqual([e['nombre'] for e in m['escenarios']], ['Plan óptimo', 'Mes anterior (real)'])


class MetasDesdeElPlanTest(TestCase):

    def test_con_plan_la_meta_sale_de_el(self):
        from indicadores.services.metas import proponer_meta, valores_desde_plan

        ejemplo = EjemploFijoTest().parametros()
        ejemplo.save()
        valores = valores_desde_plan(2026, 4, hoy=date(2026, 4, 10))
        self.assertEqual((valores['meta_eventos'], valores['meta_pasadias'], valores['meta_noches']), (4, 6, 20))
        self.assertEqual(valores['meta_venta_neta'], D('68965.56'))
        meta = proponer_meta(2026, 4, hoy=date(2026, 4, 10))
        self.assertEqual((meta.meta_eventos, meta.meta_pasadias, meta.meta_noches, meta.meta_venta_neta),
                         (4, 6, 20, D('68965.56')))
        self.assertIn('desde plan', meta.notas)

    def test_sin_topes_no_hay_plan(self):
        from indicadores.services.metas import valores_desde_plan

        EjemploFijoTest().parametros(demanda_max_evento_mes=None).save()
        self.assertIsNone(valores_desde_plan(2026, 4, hoy=date(2026, 4, 10)))


class BaseVaciaTest(TestCase):

    def test_ningun_service_revienta_sin_datos(self):
        p = ParametrosNegocio.vigente()
        hoy = date(2026, 10, 16)
        EconomiaUnitariaService.generar(*mes_rango(2026, 10), p)
        CostosFijosService.generar(hoy, p)
        CapacidadService.generar(*mes_rango(2026, 10), p)
        EquilibrioService.generar(2026, 10, p, hoy=hoy)
        MezclaOptimaService.generar(2026, 10, p, hoy=hoy)
        EmbudoComercialService.generar(*mes_rango(2026, 10), whatsapp=False)
        MetasService.generar(2026, 10, p, hoy=hoy)
        self.assertEqual(ventana_promedio(2026, 10, p), (date(2026, 8, 1), date(2026, 10, 31)))


class SinFloatTest(TestCase):

    def test_sin_float_ni_iva_en_indicadores(self):
        base = Path(settings.BASE_DIR) / 'indicadores'
        for ruta in base.rglob('*.py'):
            if ruta.name.startswith('test_'):
                continue
            texto = ruta.read_text()
            self.assertIsNone(re.search(r'\bfloat\(', texto), msg=f'float( en {ruta.name}')
            self.assertIsNone(re.search(r'0\.16|1\.16', texto), msg=f'IVA en {ruta.name}')
