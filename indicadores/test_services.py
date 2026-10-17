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
from indicadores.services.mezcla import FIN_DE_SEMANA, MezclaOptimaService, asignar_optimo
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

    def economia(self, **kw):
        return EconomiaUnitariaService.generar(INICIO, FIN, self.p, **kw)


class EconomiaUnitariaTest(MesArmado):

    def test_valores_a_mano(self):
        e = self.economia()
        ev, pa, ho = (e['tipos'][t] for t in ('EVENTO', 'PASADIA', 'HOSPEDAJE'))
        self.assertEqual((ev['ventas'], ev['ticket'], ev['costo_variable'], ev['mc'], ev['mc_dia']),
                         (4, D('11000.00'), D('3850.00'), D('7150.00'), D('7150.00')))
        self.assertEqual((pa['ventas'], pa['ticket'], pa['costo_variable'], pa['mc'], pa['mc_dia']),
                         (6, D('3000.00'), D('1050.00'), D('1950.00'), D('1950.00')))
        self.assertEqual((ho['ventas'], ho['ticket'], ho['mc'], ho['dias_por_unidad'], ho['mc_dia']),
                         (2, D('6000.00'), D('3900.00'), D('3.0000'), D('1300.00')))
        self.assertEqual((ho['noches'], ho['noches_habitacion']), (6, 12))
        self.assertEqual((ev['mc_pct'], pa['mc_pct'], ho['mc_pct']), (D('65.0'), D('65.0'), D('65.0')))
        self.assertEqual(e['venta_neta'], D('75000.00'))   # 74,000 + 1,000 de arrendamiento
        self.assertEqual(e['otros_venta'], D('1000.00'))
        self.assertEqual(e['ventas'], 12)

    def test_no_cuentan_cortesias_ni_estados_que_no_son_venta(self):
        e = self.economia()
        self.assertEqual(sum(t['ventas'] for t in e['tipos'].values()), 12)
        self.assertEqual(e['tipos']['EVENTO']['ventas'], 4)

    def test_menos_de_cinco_ventas_con_gasto_usa_parametros_y_se_marca_estimado(self):
        e = self.economia()
        self.assertEqual(e['tipos']['PASADIA']['fuente_costo'], 'medido')
        self.assertEqual(e['tipos']['PASADIA']['muestra'], 6)
        self.assertEqual(e['tipos']['PASADIA']['costo_pct'], D('0.3500'))   # mide 35%, no el 15% de parámetros
        self.assertEqual(e['tipos']['EVENTO']['fuente_costo'], 'estimado')
        self.assertEqual(e['tipos']['EVENTO']['muestra'], 0)
        self.assertEqual(e['tipos']['EVENTO']['costo_pct'], D('0.3500'))
        self.assertTrue(any('Evento' in a for a in e['avisos']))

    def test_simulacion_no_toca_la_base(self):
        e = self.economia(overrides={'ticket_EVENTO': D('20000'), 'costo_pct_EVENTO': D('50')})
        ev = e['tipos']['EVENTO']
        self.assertEqual((ev['ticket'], ev['mc'], ev['fuente_costo']), (D('20000.00'), D('10000.00'), 'simulado'))

    def test_mc_negativo_no_revienta(self):
        e = self.economia(overrides={'costo_pct_PASADIA': D('120')})
        pa = e['tipos']['PASADIA']
        self.assertEqual(pa['mc'], D('-600.00'))
        self.assertIsNone(pa['mc_dia'])
        self.assertTrue(any('margen de contribución' in a for a in pa['avisos']))

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
        self.assertEqual((eq['solo']['EVENTO']['unidades'], eq['solo']['PASADIA']['unidades'],
                          eq['solo']['HOSPEDAJE']['unidades'], eq['solo']['HOSPEDAJE']['noches']), (5, 17, 9, 25))

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

    def test_evento_primero_y_el_resto_a_pasadia(self):
        m = MezclaOptimaService.generar(2026, 2, self.p, hoy=HOY)
        filas = {f['tipo']: f for f in m['filas']}
        self.assertEqual((filas['EVENTO']['unidades'], filas['PASADIA']['unidades'], filas['HOSPEDAJE']['unidades']),
                         (9, 19, 0))
        self.assertEqual(filas['EVENTO']['segmentos'][FIN_DE_SEMANA], 9)
        self.assertEqual(m['contribucion'], D('101400.00'))
        self.assertEqual(m['utilidad'], D('68900.00'))
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
        self.assertEqual(m['real']['unidades'], {'EVENTO': 4, 'PASADIA': 6, 'HOSPEDAJE': 2})
        self.assertEqual(m['real']['contribucion'], D('48100.00'))
        self.assertEqual(m['contribucion'], D('107250.00'))
        self.assertEqual(m['brecha'], D('59150.00'))

    def test_con_precio_y_costo_directo_solo_cuentan_esos_dos_datos(self):
        # Precios con IVA: neto 3,448.28 / 1,293.10 / 1,637.93 por noche; el histórico de febrero se ignora.
        self.p.precio_venta_evento, self.p.costo_directo_evento = D('4000.00'), D('1000.00')
        self.p.precio_venta_pasadia, self.p.costo_directo_pasadia = D('1500.00'), D('150.00')
        self.p.precio_venta_hospedaje, self.p.costo_directo_hospedaje = D('1900.00'), D('100.00')
        m = MezclaOptimaService.generar(2026, 2, self.p, hoy=HOY)
        filas = {f['tipo']: f for f in m['filas']}
        self.assertEqual(filas['EVENTO']['precio'], D('3448.28'))
        self.assertEqual(filas['EVENTO']['costo'], D('1000.00'))
        self.assertEqual(filas['EVENTO']['mc_dia'], D('2448.28'))
        self.assertEqual(filas['PASADIA']['mc_dia'], D('1143.10'))
        self.assertEqual(filas['HOSPEDAJE']['mc_dia'], D('1537.93'))
        self.assertTrue(filas['HOSPEDAJE']['por_noche'])
        self.assertEqual({f['fuente_precio'] for f in m['filas']}, {'parámetros'})
        self.assertEqual({f['fuente_costo'] for f in m['filas']}, {'parámetros'})
        # Evento (9) en los fines de semana permitidos; el resto, lo que más deja por noche: hospedaje.
        self.assertEqual((filas['EVENTO']['unidades'], filas['PASADIA']['unidades'], filas['HOSPEDAJE']['unidades']),
                         (9, 0, 19))
        self.assertEqual(m['contribucion'], 9 * D('2448.28') + 19 * D('1537.93'))

    def test_sin_costo_directo_usa_el_porcentaje_de_respaldo(self):
        self.p.precio_venta_pasadia = D('1500.00')
        m = MezclaOptimaService.generar(2026, 2, self.p, hoy=HOY)
        fila = next(f for f in m['filas'] if f['tipo'] == 'PASADIA')
        self.assertEqual((fila['precio'], fila['costo'], fila['fuente_costo']), (D('1293.10'), D('193.97'), 'estimado'))

    def test_sin_precio_en_parametros_usa_el_historico_y_lo_avisa(self):
        m = MezclaOptimaService.generar(2026, 2, self.p, hoy=HOY)
        fila = next(f for f in m['filas'] if f['tipo'] == 'EVENTO')
        self.assertEqual(fila['fuente_precio'], 'histórico')
        self.assertTrue(any('sin precio de venta en Parámetros' in a for a in m['avisos']))

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

    def test_la_mezcla_real_se_valua_con_el_mismo_margen_por_noche(self):
        self.p.precio_venta_hospedaje, self.p.costo_directo_hospedaje = D('1900.00'), D('100.00')
        m = MezclaOptimaService.generar(2026, 3, self.p, hoy=HOY)
        previo = EconomiaUnitariaService.generar(date(2026, 2, 1), date(2026, 2, 28), parametros=self.p)
        self.assertEqual(m['real']['unidades']['HOSPEDAJE'], previo['tipos']['HOSPEDAJE']['noches'])

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
