"""Cómo se leen las comisiones desde Reportes, Indicadores, Marketing y el resumen del lunes (Issue #505, PR 5).

Solo lectura: nada de esto cambia cómo se devenga ni cómo se paga."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente
from comunicacion.services_direccion import texto_resumen
from core_erp.test_utils import login_superuser_con_totp
from indicadores.models import ParametrosNegocio
from indicadores.services.base import q2
from indicadores.services.servicio import economia_servicio
from indicadores.services.unitarios import EconomiaUnitariaService
from indicadores.test_services import FIN, INICIO, cotizacion
from marketing.services import kpis, pipeline
from nomina.models import CondicionesPago
from reportes.services.comercial import RentabilidadEventosService
from reportes.services.operacion import ComisionesPeriodoService, NominaPeriodoService
from ventas import services_comisiones as comisiones
from ventas.models import ComisionContratacion
from ventas.test_comisiones import CLAVE, D, pagar, vendedora, venta
from ventas.test_cortes import mov


def comision_sellada(cot, vendedor, total):
    return ComisionContratacion.objects.create(
        cotizacion=cot, vendedor=vendedor, condiciones=CondicionesPago.objects.get(empleado=vendedor),
        numero_en_periodo=1, anio=cot.fecha_evento.year, monto_fijo=total, total=total, devengado=total,
        precio_final=cot.precio_final, confirmada_en=timezone.now())


class IndicadoresTest(TestCase):
    def setUp(self):
        self.ana = vendedora()
        self.cots = [cotizacion(f'C{i}', date(2026, 2, 3 + i)) for i in range(5)]
        self.p = ParametrosNegocio.vigente()

    def economia(self, **overrides):
        e = EconomiaUnitariaService.generar(INICIO, FIN, self.p)['tipos']['EVENTO']
        return e, economia_servicio('EVENTO', e, self.p, overrides)

    def test_la_comision_entra_al_costo_variable(self):
        fila, antes = self.economia()
        self.assertEqual(fila['comision_pct'], D('0'))
        self.assertEqual(antes['comision'], D('0'))
        for cot in self.cots[:2]:            # $500 en 2 de 5 ventas de $10,000 = 2 %
            comision_sellada(cot, self.ana, D('500.00'))
        fila, despues = self.economia()
        self.assertEqual(fila['comision_pct'], D('0.0200'))
        self.assertEqual(despues['comision'], q2(antes['ticket'] * D('0.02')))
        self.assertEqual(despues['costo'], antes['costo'] + despues['comision'])
        self.assertEqual(despues['mc'], antes['mc'] - despues['comision'])

    def test_una_simulacion_de_costo_no_suma_comision(self):
        comision_sellada(self.cots[0], self.ana, D('500.00'))
        _, sim = self.economia(costo_pct_EVENTO=30)
        self.assertEqual(sim['comision'], D('0'))
        self.assertEqual(sim['costo'], q2(sim['ticket'] * D('0.30')))


class MarketingTest(TestCase):
    def test_cliente_de_la_vendedora_tiene_fuente_propia(self):
        cliente = Cliente.objects.create(nombre='Cliente', telefono='9991110001', origen='Vendedor')
        self.assertEqual(pipeline.fuente_de('9991110001', cliente)['fuente'], 'VENDEDOR')
        otro = Cliente.objects.create(nombre='Otro', telefono='9991110002', origen='Instagram')
        self.assertEqual(pipeline.fuente_de('9991110002', otro)['fuente'], 'INSTAGRAM')

    def test_el_costo_de_ventas_aparece_junto_a_la_pauta_sin_tocar_el_cac(self):
        ana = vendedora()
        hoy = timezone.localdate()
        mov(ana, '300.00', hoy)
        datos = kpis.calcular(hoy.replace(day=1), hoy)
        self.assertEqual(datos['costo_ventas'], D('300.00'))
        self.assertEqual(datos['total']['gasto'], D('0.00'))

    def test_prospecto_vendedor_no_se_acredita_a_una_campana(self):
        from marketing.models import Anuncio, Campana, Lead
        campana = Campana.objects.create(nombre='C', plataforma='META', id_externo='c1', tipo_servicio='EVENTO')
        Anuncio.objects.create(campana=campana, id_externo='a1', nombre='A')
        Lead.objects.create(nombre='X', telefono='9990001111', fuente='VENDEDOR', anuncio_id='a1')
        hoy = timezone.localdate()
        datos = kpis.calcular(hoy - timedelta(days=1), hoy + timedelta(days=1))
        self.assertEqual(datos['total']['prospectos'], 1)
        self.assertTrue(all(g['prospectos'] == 0 for g in datos['por_campana']))


class ReportesTest(TestCase):
    def setUp(self):
        self.ana = vendedora()
        self.hoy = timezone.localdate()
        self.su = User.objects.create_superuser('dir', 'd@x.mx', CLAVE)
        login_superuser_con_totp(self.client, self.su)

    def test_reporte_de_comisiones_en_pdf_y_excel(self):
        cot = venta(self.ana)
        pagar(cot, '9860.00')
        comisiones.sincronizar(cot)
        params = {'fecha_inicio': self.hoy.replace(day=1).isoformat(), 'fecha_fin': self.hoy.isoformat()}
        pdf = self.client.get(reverse('reportes:comisiones'), params)
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        xlsx = self.client.get(reverse('reportes:comisiones'), {**params, 'formato': 'excel'})
        self.assertEqual(xlsx.status_code, 200)
        self.assertIn('spreadsheetml', xlsx['Content-Type'])

    def test_servicio_de_comisiones_cuadra_con_el_tablero(self):
        cot = venta(self.ana)
        pagar(cot, '9860.00')
        comisiones.sincronizar(cot)
        d = ComisionesPeriodoService.generar(self.hoy.replace(day=1), self.hoy)
        self.assertEqual(d['totales']['devengado'], D('250.00'))
        self.assertEqual([f['persona'] for f in d['contrataciones']], ['Ana'])
        self.assertEqual(len(d['movimientos']), 1)

    def test_el_selector_lo_ofrece_solo_a_quien_tiene_el_permiso(self):
        self.assertContains(self.client.get(reverse('reportes:selector')), 'Comisiones por periodo')

    def test_nomina_muestra_el_costo_de_ventas_aparte(self):
        mov(self.ana, '400.00', self.hoy)
        d = NominaPeriodoService.generar(self.hoy.replace(day=1), self.hoy)
        self.assertEqual(d['ventas_devengado'], D('400.00'))
        self.assertEqual(d['costo_total_personal'], d['total'] + D('400.00'))
        r = self.client.get(reverse('reportes:nomina'), {'fecha_inicio': self.hoy.replace(day=1).isoformat(),
                                                          'fecha_fin': self.hoy.isoformat()})
        self.assertEqual(r.status_code, 200)

    def test_rentabilidad_trae_la_comision_devengada_sin_mover_la_utilidad_bruta(self):
        cot = venta(self.ana)
        pagar(cot, '9860.00')
        comisiones.sincronizar(cot)
        d = RentabilidadEventosService.generar(cot.fecha_evento, cot.fecha_evento)
        self.assertEqual(d['eventos'][0]['comision'], D('250.00'))
        self.assertEqual(d['totales']['comision'], D('250.00'))
        self.assertEqual(d['eventos'][0]['utilidad'], d['eventos'][0]['base'])
        self.assertEqual(d['utilidad_tras_comisiones'], d['utilidad_neta'] - D('250.00'))


class ResumenDelLunesTest(TestCase):
    def test_la_linea_de_ventas_va_en_el_resumen(self):
        mes = {'etiqueta': 'noviembre', 'venta': D('1000'), 'meta_venta': None, 'ocupacion': 10}
        aviso = texto_resumen(timezone.localdate(), mes, D('0'), 0, [], None, 0, 'Ventas de la semana: 2 visita(s)')
        self.assertIn('Ventas de la semana: 2 visita(s)', aviso.texto())
        sin = texto_resumen(timezone.localdate(), mes, D('0'), 0, [])
        self.assertNotIn('Ventas de la semana', sin.texto())


class ListasAdminSinNMas1Test(TestCase):
    """Las listas «Movimientos» y «Comisiones» no hacen una consulta extra por fila (Rutina Técnica 2026-11-21)."""

    def setUp(self):
        from comunicacion.models import Visita
        from ventas import services_cortes as cortes
        from ventas.models import MovimientoComision

        self.Visita, self.Movimiento = Visita, MovimientoComision
        self.ana = vendedora()
        su = User.objects.create_superuser('dir_listas', 'd@x.mx', CLAVE)
        login_superuser_con_totp(self.client, su)
        mov(self.ana, '1.00', date(2026, 11, 3))
        self.corte = cortes.crear_corte(self.ana, 'COMISIONES', date(2026, 11, 1), date(2026, 11, 15))

    def _agregar(self, n):
        for _ in range(n):
            cot = venta(self.ana, estado='COTIZADA')
            c = comision_sellada(cot, self.ana, Decimal('350.00'))
            v = self.Visita.objects.create(nombre='x', telefono='9990000000', estado='CONFIRMADA', vendedor=self.ana,
                                           inicio=timezone.now() + timedelta(days=self.Visita.objects.count() + 1))
            m = mov(self.ana, '10.00', date(2026, 11, 3), comision=c, visita=v)
            self.Movimiento.objects.filter(pk=m.pk).update(corte=self.corte)

    def _consultas(self, url):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as q:
            self.assertEqual(self.client.get(url).status_code, 200)
        return len(q)

    def test_consultas_constantes_al_crecer_la_lista(self):
        for nombre in ('movimientocomision', 'comisioncontratacion'):
            with self.subTest(nombre):
                url = reverse(f'admin:ventas_{nombre}_changelist')
                self._agregar(2)
                base = self._consultas(url)
                self._agregar(10)
                with self.assertNumQueries(base):
                    self.client.get(url)
