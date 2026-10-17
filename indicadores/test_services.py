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
from indicadores.services.plan_anual import PlanAnualService
from indicadores.services.servicio import economia_servicio
from indicadores.services.tablero import TableroService
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
        marzo = MetasService.generar(2026, 3, self.p, hoy=HOY)
        self.assertEqual(marzo['real']['pasadias'], 0)

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
    """Capacidad, equilibrio y plan anual leen el precio y el margen de `economia_servicio`."""

    def paneles(self, overrides=None):
        economia = self.economia()
        capacidad = CapacidadService.generar(INICIO, FIN, self.p, economia, overrides)
        equilibrio = EquilibrioService.generar(2026, 2, self.p, overrides, HOY, economia=economia, capacidad=capacidad)
        plan = PlanAnualService.generar(2026, self.p, hoy=HOY, economia=economia, overrides=overrides)
        return economia, capacidad, equilibrio, plan

    def comprobar(self, overrides=None):
        economia, capacidad, equilibrio, plan = self.paneles(overrides)
        for tipo in ('EVENTO', 'PASADIA', 'HOSPEDAJE'):
            esperado = economia_servicio(tipo, economia['tipos'][tipo], self.p, overrides)
            cap, eq = capacidad['servicios'][tipo], equilibrio['servicios'][tipo]
            self.assertEqual(cap['ticket'], esperado['ticket'], tipo)
            self.assertEqual(eq['ticket'], esperado['ticket'], tipo)
            if esperado['ticket'] is not None:
                self.assertEqual(plan['servicios'][tipo]['neto'], esperado['ticket'], tipo)
            self.assertEqual(eq['mc'], esperado['mc'], tipo)
            if esperado['mc'] is not None and esperado['mc'] > 0:
                self.assertEqual(equilibrio['solo'][tipo]['mc'], esperado['mc'], tipo)
                self.assertEqual(cap['mc_max'], esperado['mc'] * cap['unidades'], tipo)
                self.assertEqual(plan['servicios'][tipo]['costo'], esperado['costo'], tipo)
        return economia_servicio

    def test_sin_parametros_solo_vale_el_historico_con_muestra(self):
        self.comprobar()
        _, capacidad, equilibrio, plan = self.paneles()
        self.assertEqual(capacidad['servicios']['PASADIA']['ticket'], D('3000.00'))
        for tipo in ('EVENTO', 'HOSPEDAJE'):           # 4 y 2 ventas: menos de 5, sin precio
            self.assertIsNone(capacidad['servicios'][tipo]['ticket'])
            self.assertIsNone(capacidad['servicios'][tipo]['ingreso_max'])
            self.assertIsNone(equilibrio['servicios'][tipo]['ticket'])
            self.assertNotIn(tipo, plan['servicios'])
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
        _, capacidad, equilibrio, plan = self.paneles()
        for avisos in (capacidad['avisos'], equilibrio['avisos'], plan['avisos']):
            aviso = next(a for a in avisos if a.startswith('Evento: sin precio de venta'))
            self.assertIn('Captura el precio en Parámetros', aviso)
            self.assertIn('COT-', aviso)


class MetasDesdeElPlanTest(TestCase):

    def parametros(self):
        return ParametrosNegocio.objects.create(
            costo_fijo_mensual_override=D('3000'), precio_venta_evento=D('12000'), costo_directo_evento=D('4000'),
            precio_venta_pasadia=D('2000'), costo_directo_pasadia=D('300'))

    def test_con_plan_la_meta_sale_de_la_parte_del_mes(self):
        from indicadores.services.metas import proponer_meta, valores_desde_plan

        self.parametros()
        hoy = date(2026, 4, 10)
        fila = PlanAnualService.del_mes(2026, 4, hoy=hoy)['fila']
        valores = valores_desde_plan(2026, 4, hoy=hoy)
        self.assertEqual((valores['meta_eventos'], valores['meta_pasadias'], valores['meta_noches']),
                         (fila['eventos'], fila['pasadias'], fila['noches']))
        self.assertEqual((valores['meta_venta_neta'], valores['meta_ocupacion_pct']), (fila['venta'], fila['ocupacion']))
        self.assertGreater(valores['meta_eventos'], 0)
        meta = proponer_meta(2026, 4, hoy=hoy)
        self.assertEqual((meta.meta_eventos, meta.meta_venta_neta), (valores['meta_eventos'], valores['meta_venta_neta']))
        self.assertIn('desde plan', meta.notas)

    def test_sin_precios_no_hay_plan(self):
        from indicadores.services.metas import valores_desde_plan

        self.assertIsNone(valores_desde_plan(2026, 4, hoy=date(2026, 4, 10)))


class BaseVaciaTest(TestCase):

    def test_ningun_service_revienta_sin_datos(self):
        p = ParametrosNegocio.vigente()
        hoy = date(2026, 10, 16)
        EconomiaUnitariaService.generar(*mes_rango(2026, 10), p)
        CostosFijosService.generar(hoy, p)
        CapacidadService.generar(*mes_rango(2026, 10), p)
        EquilibrioService.generar(2026, 10, p, hoy=hoy)
        PlanAnualService.generar(2026, p, hoy=hoy)
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


class MesDelTableroTest(TestCase):
    """`TableroService._mes` con números fijos."""

    @staticmethod
    def _armar(venta, pesos, dias, mc_pct, plan_vacio=True):
        eq = {'base': {'pesos': pesos, 'dias': dias, 'mc_pct': mc_pct}, 'cf': D('50000.00'), 'dias_vendibles': 28}
        metas = {'real': {'venta_neta': venta, 'venta_realizada': venta}, 'meta': None}
        plan = {'plan': {'vacio': plan_vacio}, 'fila': {'venta': D('100000.00'), 'ocupacion': D('60')}}
        ocupacion = {'dias_ocupados': 3, 'ocupacion': D('10.7')}
        return TableroService._mes(eq, metas, plan, ocupacion, 2026, 10)

    def test_falta_cf_y_dias_antes_del_equilibrio(self):
        m = self._armar(D('30000.00'), D('80000.00'), D('4.2'), D('0.6'))
        self.assertEqual(m['etiqueta'], 'Octubre 2026')
        self.assertEqual(m['falta_cf'], D('50000.00'))
        self.assertEqual(m['dias_equilibrio'], 5)
        self.assertEqual(m['dias_faltan'], 2)
        self.assertEqual(m['utilidad'], D('-32000.00'))        # 30,000 × 0.6 − 50,000
        self.assertEqual(m['fuente_meta'], 'equilibrio')

    def test_pasado_el_equilibrio_no_faltan_ni_dias_ni_pesos(self):
        m = self._armar(D('90000.00'), D('80000.00'), D('2'), D('0.6'), plan_vacio=False)
        self.assertEqual(m['falta_cf'], D('0.00'))
        self.assertEqual(m['dias_faltan'], 0)
        self.assertEqual(m['fuente_meta'], 'plan')
        self.assertEqual(m['tono_meta'], 'alerta')              # 90 % del plan de 100,000

    def test_sin_margen_no_hay_utilidad(self):
        m = self._armar(D('1000.00'), None, None, None)
        self.assertIsNone(m['utilidad'])
        self.assertIsNone(m['falta_cf'])
        self.assertIsNone(m['dias_faltan'])
        self.assertIsNone(m['meta_venta'])
        self.assertEqual(m['tono_meta'], 'neutro')
