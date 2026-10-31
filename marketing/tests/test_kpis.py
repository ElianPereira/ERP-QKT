"""Indicadores de marketing: razones, cortes de ingreso, cohortes, tope de CAC, pronóstico y pantalla."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, Pago
from core_erp import impuestos
from core_erp.test_utils import login_superuser_con_totp
from indicadores.models import ParametrosNegocio
from marketing.models import Anuncio, Campana, GastoPublicitario, Lead, ParametroMarketing, SnapshotKPI
from marketing.services import kpis, snapshots

User = get_user_model()
HOY = date(2026, 10, 31)


def aware(dia, hora=12):
    return timezone.make_aware(datetime(dia.year, dia.month, dia.day, hora))


class Base(TestCase):
    def setUp(self):
        self.p = ParametroMarketing.objects.create(muestra_minima_leads=1, ventana_dias=90)
        self.n = 0

    def campana(self, servicio='EVENTO', ext='C1', anuncio='AD1'):
        c = Campana.objects.create(id_externo=ext, nombre=f'Camp {ext}', tipo_servicio=servicio)
        Anuncio.objects.create(campana=c, id_externo=anuncio)
        return c

    def gasto(self, campana, monto, dia=HOY):
        return GastoPublicitario.objects.create(campana=campana, fecha=dia, monto=Decimal(monto))

    def cotizacion(self, subtotal, descuento='0', tipo='EVENTO', estado='CONFIRMADA'):
        """Cotización con importes fijos (se evita el recálculo del `save`): IVA sobre la base."""
        self.n += 1
        cli = Cliente.objects.create(nombre=f'C{self.n}', telefono=f'52555500{self.n:04d}')
        cot = Cotizacion.objects.create(cliente=cli, tipo_servicio=tipo, nombre_evento='X',
                                        fecha_evento=HOY + timedelta(days=30 + self.n))
        base = Decimal(subtotal) - Decimal(descuento)
        iva = impuestos.iva_de(base)
        Cotizacion.objects.filter(pk=cot.pk).update(
            subtotal=Decimal(subtotal), descuento=Decimal(descuento), iva=iva, precio_final=base + iva,
            estado=estado)
        cot.refresh_from_db()
        return cot

    def pago(self, cot, monto, tipo='INGRESO'):
        Pago.objects.bulk_create([Pago(cotizacion=cot, monto=Decimal(monto), metodo='TRANSFERENCIA', tipo=tipo)])

    def lead(self, dia=HOY, anuncio='', etapa='NUEVO', cot=None, servicio=''):
        self.n += 1
        return Lead.objects.create(
            telefono=f'52555511{self.n:04d}', anuncio_id=anuncio, etapa=etapa, cotizacion=cot,
            tipo_servicio=servicio, fecha_primer_contacto=aware(dia))

    def ganado(self, anuncio, subtotal='10000', cobrado=None, dia=HOY, tipo='EVENTO'):
        cot = self.cotizacion(subtotal, tipo=tipo)
        if cobrado:
            self.pago(cot, cobrado)
        return self.lead(dia, anuncio, 'GANADO', cot)


class CobradoSinIvaTest(Base):
    def test_iva_fuera_y_a_un_centavo(self):
        cot = self.cotizacion('1000')            # base 1000.00, IVA 160.00, total 1160.00
        self.pago(cot, '580.00')
        self.assertEqual(kpis.cobrado_sin_iva(cot), Decimal('500.00'))
        self.pago(cot, '0.01')                   # 580.01 / 1.16 = 500.0086 -> 500.01
        self.assertEqual(kpis.cobrado_sin_iva(cot), Decimal('500.01'))

    def test_redondeo_half_up(self):
        cot = self.cotizacion('1000')            # 0.58 * 1000/1160 = 0.5 exacto; 0.87 -> 0.75; 0.29 -> 0.25
        self.pago(cot, '0.29')
        self.assertEqual(kpis.cobrado_sin_iva(cot), Decimal('0.25'))
        cot2 = self.cotizacion('1000')
        self.pago(cot2, '0.01')                  # 0.00862 -> 0.01 (no 0.00)
        self.assertEqual(kpis.cobrado_sin_iva(cot2), Decimal('0.01'))
        cot3 = self.cotizacion('1000')
        self.pago(cot3, '0.0')                   # nada cobrado
        self.assertEqual(kpis.cobrado_sin_iva(cot3), Decimal('0.00'))

    def test_el_reembolso_resta(self):
        cot = self.cotizacion('1000')
        self.pago(cot, '1160.00')
        self.pago(cot, '580.00', tipo='REEMBOLSO')
        self.assertEqual(kpis.cobrado_sin_iva(cot), Decimal('500.00'))

    def test_reembolso_total_deja_cero_nunca_negativo(self):
        cot = self.cotizacion('1000')
        self.pago(cot, '1160.00')
        self.pago(cot, '1160.00', tipo='REEMBOLSO')
        self.assertEqual(kpis.cobrado_sin_iva(cot), Decimal('0.00'))
        self.pago(cot, '100.00', tipo='REEMBOLSO')
        self.assertEqual(kpis.cobrado_sin_iva(cot), Decimal('0.00'))

    def test_con_descuento_el_contratado_es_la_base_neta(self):
        cot = self.cotizacion('1000', descuento='200')   # base 800, total 928
        self.pago(cot, '928.00')
        self.assertEqual(kpis.cobrado_sin_iva(cot), Decimal('800.00'))


class RazonesTest(Base):
    def test_cortes_y_razones_de_una_campana(self):
        c = self.campana()
        self.gasto(c, '1000.00')
        self.ganado('AD1', '10000', cobrado='5800.00')       # contratado 10,000; cobrado base 5,000
        self.lead(anuncio='AD1')
        self.lead(anuncio='AD1', etapa='COTIZADO', cot=self.cotizacion('3000', estado='COTIZADA'))
        datos = kpis.calcular(HOY - timedelta(days=30), HOY)
        g = datos['por_campana'][0]
        self.assertEqual((g['prospectos'], g['cotizaciones'], g['ganados']), (3, 2, 1))
        self.assertEqual((g['contratado'], g['cobrado'], g['gasto']),
                         (Decimal('10000.00'), Decimal('5000.00'), Decimal('1000.00')))
        self.assertEqual(g['neto'], Decimal('4000.00'))
        self.assertEqual(g['cpl'], Decimal('333.33'))                  # 1000 / 3
        self.assertEqual(g['costo_cotizacion'], Decimal('500.00'))
        self.assertEqual(g['tasa_cierre'], Decimal('33.33'))
        self.assertEqual(g['cac'], Decimal('1000.00'))
        self.assertEqual(g['roas_cobrado'], Decimal('5.00'))
        self.assertEqual(g['roas_contratado'], Decimal('10.00'))
        self.assertEqual(datos['total']['cac'], Decimal('1000.00'))

    def test_gasto_cero_no_inventa_cifras(self):
        self.ganado('', '10000', cobrado='5800.00')
        t = kpis.calcular(HOY - timedelta(days=30), HOY)['total']
        for clave in ('cpl', 'costo_cotizacion', 'cac', 'roas_cobrado', 'roas_contratado'):
            self.assertIsNone(t[clave], clave)
            self.assertEqual(t['motivos'][clave], 'Sin gasto en el periodo', clave)
        self.assertEqual(t['tasa_cierre'], Decimal('100.00'))

    def test_division_por_cero_sin_prospectos_ni_ganados(self):
        c = self.campana()
        self.gasto(c, '500.00')
        t = kpis.calcular(HOY - timedelta(days=30), HOY)['total']
        self.assertIsNone(t['tasa_cierre'])
        self.assertIsNone(t['cac'])
        self.assertEqual(t['prospectos'], 0)
        self.lead(anuncio='AD1')
        t = kpis.calcular(HOY - timedelta(days=30), HOY)['total']
        self.assertEqual(t['motivos']['cac'], 'Sin ventas ganadas')
        self.assertEqual(t['motivos']['costo_cotizacion'], 'Sin cotizaciones')
        self.assertEqual(t['cpl'], Decimal('500.00'))

    def test_muestra_pequena_devuelve_none_con_motivo(self):
        ParametroMarketing.objects.create(muestra_minima_leads=5)
        c = self.campana()
        self.gasto(c, '500.00')
        self.ganado('AD1', cobrado='5800.00')
        t = kpis.calcular(HOY - timedelta(days=30), HOY)['total']
        self.assertFalse(t['suficiente'])
        self.assertIsNone(t['cac'])
        self.assertEqual(t['motivos']['cac'], 'Muestra insuficiente: 1 de 5 prospectos')

    def test_prospecto_fuera_del_periodo_no_cuenta(self):
        self.lead(dia=HOY - timedelta(days=200))
        self.assertEqual(kpis.calcular(HOY - timedelta(days=30), HOY)['total']['prospectos'], 0)

    def test_por_servicio_usa_el_servicio_de_la_campana(self):
        ce, cp = self.campana('EVENTO', 'C1', 'AD1'), self.campana('PASADIA', 'C2', 'AD2')
        self.gasto(ce, '900.00')
        self.gasto(cp, '100.00')
        self.ganado('AD1', '10000')
        self.lead(anuncio='AD2')
        d = kpis.calcular(HOY - timedelta(days=30), HOY)
        self.assertEqual(d['por_servicio']['EVENTO']['gasto'], Decimal('900.00'))
        self.assertEqual(d['por_servicio']['EVENTO']['ganados'], 1)
        self.assertEqual(d['por_servicio']['PASADIA']['prospectos'], 1)
        self.assertEqual(d['por_servicio']['HOSPEDAJE']['gasto'], Decimal('0.00'))
        self.assertNotIn('', d['por_servicio'])
        self.assertEqual(d['total']['gasto'], Decimal('1000.00'))


class CohortesTest(Base):
    def test_madurez_segun_el_ciclo_de_cada_servicio(self):
        filas = kpis.cohortes(HOY, meses=6, parametros=self.p)
        self.assertEqual([f['etiqueta'] for f in filas][-1], '10/2026')
        por_mes = {f['etiqueta']: f for f in filas}
        # Mayo termina el 31/05: +120 días = 28/09 <= hoy (madura), pasadía +30 también.
        self.assertTrue(por_mes['05/2026']['madura'])
        # Julio termina el 31/07: +120 = 28/11 > hoy, pero pasadía/hospedaje (+30) ya maduró.
        self.assertFalse(por_mes['07/2026']['madura'])
        self.assertTrue(por_mes['07/2026']['madura_por_servicio']['PASADIA'])
        self.assertFalse(por_mes['07/2026']['madura_por_servicio']['EVENTO'])
        self.assertFalse(por_mes['10/2026']['madura'])

    def test_la_cohorte_agrupa_por_mes_de_primer_contacto_con_su_gasto(self):
        c = self.campana()
        self.gasto(c, '300.00', dia=date(2026, 9, 10))
        self.gasto(c, '700.00', dia=date(2026, 10, 10))
        self.ganado('AD1', dia=date(2026, 9, 12))
        por_mes = {f['etiqueta']: f['total'] for f in kpis.cohortes(HOY, meses=3, parametros=self.p)}
        self.assertEqual((por_mes['09/2026']['gasto'], por_mes['09/2026']['ganados']), (Decimal('300.00'), 1))
        self.assertEqual((por_mes['10/2026']['gasto'], por_mes['10/2026']['ganados']), (Decimal('700.00'), 0))


class TopeCacTest(Base):
    def negocio(self, **kw):
        ParametrosNegocio.objects.create(
            precio_venta_evento=Decimal('11600.00'), costo_directo_evento=Decimal('4000.00'), **kw)

    def test_tope_es_margen_por_reinversion(self):
        self.negocio()                           # neto 10,000 - costo 4,000 = margen 6,000
        t = kpis.topes_cac(HOY, self.p)['EVENTO']
        self.assertEqual((t['margen'], t['tope'], t['fuente']), (Decimal('6000.00'), Decimal('1800.00'), 'margen'))

    def test_tope_manual_gana(self):
        self.negocio()
        self.p.cac_tope_manual = {'EVENTO': '2500'}
        t = kpis.topes_cac(HOY, self.p)['EVENTO']
        self.assertEqual((t['tope'], t['fuente']), (Decimal('2500.00'), 'manual'))

    def test_sin_margen_no_hay_tope_y_dice_por_que(self):
        t = kpis.topes_cac(HOY, self.p)['PASADIA']
        self.assertIsNone(t['tope'])
        self.assertIn('Sin margen', t['motivo'])

    def test_semaforo(self):
        self.assertEqual(kpis.semaforo(Decimal('2000'), Decimal('1800'), Decimal('5')), 'error')
        self.assertEqual(kpis.semaforo(Decimal('1000'), Decimal('1800'), Decimal('0.50')), 'alerta')
        self.assertEqual(kpis.semaforo(Decimal('1000'), Decimal('1800'), Decimal('3')), 'exito')
        self.assertEqual(kpis.semaforo(None, None, None), 'neutro')


class PronosticoTest(Base):
    def test_ponderado_por_etapa_valor_sin_iva_y_porcentaje_de_anticipo(self):
        cot = self.cotizacion('10000', estado='COTIZADA')
        self.lead(etapa='COTIZADO', cot=cot)
        cot2 = self.cotizacion('2000', tipo='PASADIA', estado='BORRADOR')
        self.lead(etapa='CONTACTADO', cot=cot2)
        self.lead(etapa='GANADO', cot=self.cotizacion('9999'))       # ya ganado: no es pronóstico
        pct = cot.porcentaje_anticipo_confirmacion()
        esperado = (Decimal('10000') * Decimal('0.30') * pct / 100).quantize(Decimal('0.01'))
        esperado += (Decimal('2000') * Decimal('0.05') * pct / 100).quantize(Decimal('0.01'))
        r = kpis.pronostico_anticipos(self.p)
        self.assertEqual(r['total']['cotizaciones'], 2)
        self.assertEqual(r['total']['valor'], Decimal('12000.00'))
        self.assertEqual(r['total']['esperado'], esperado)
        self.assertEqual(r['por_servicio']['EVENTO']['valor'], Decimal('10000.00'))

    def test_probabilidad_editable(self):
        self.p.prob_etapa = {**self.p.prob_etapa, 'COTIZADO': '0.5'}
        self.p.save()
        cot = self.cotizacion('1000', estado='COTIZADA')
        self.lead(etapa='COTIZADO', cot=cot)
        pct = cot.porcentaje_anticipo_confirmacion()
        r = kpis.pronostico_anticipos(ParametroMarketing.vigente())
        self.assertEqual(r['total']['esperado'], (Decimal('500') * pct / 100).quantize(Decimal('0.01')))

    def test_sin_cotizaciones_vivas_es_cero(self):
        r = kpis.pronostico_anticipos(self.p)
        self.assertEqual((r['total']['cotizaciones'], r['total']['esperado']), (0, Decimal('0.00')))


class ParametroTest(TestCase):
    def test_vigente_de_fabrica_sin_registro(self):
        p = ParametroMarketing.vigente()
        self.assertIsNone(p.pk)
        self.assertEqual((p.pct_reinversion, p.dias_sin_respuesta), (Decimal('30.00'), 15))

    def test_un_nuevo_activo_desactiva_el_anterior(self):
        a = ParametroMarketing.objects.create()
        b = ParametroMarketing.objects.create(pct_reinversion=Decimal('25'))
        a.refresh_from_db()
        self.assertFalse(a.activo)
        self.assertEqual(ParametroMarketing.vigente().pk, b.pk)

    def test_validaciones(self):
        for campo, valor in (('cac_tope_manual', {'OTRO': '10'}), ('cac_tope_manual', {'EVENTO': '-1'}),
                             ('prob_etapa', {'COTIZADO': '1.5'}), ('prob_etapa', {'MARCIANO': '0.1'})):
            with self.subTest(campo=campo, valor=valor), self.assertRaises(ValidationError):
                ParametroMarketing(**{campo: valor}).clean()

    def test_dias_sin_respuesta_lo_lee_el_embudo(self):
        from marketing.services import pipeline
        ParametroMarketing.objects.create(dias_sin_respuesta=3)
        lead = Lead.objects.create(telefono='525555550777', fecha_ultima_interaccion=timezone.now() - timedelta(days=5))
        pipeline.sincronizar(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.etapa, 'PERDIDO')


class SnapshotTest(Base):
    def test_idempotente_por_fecha_servicio_y_campana(self):
        c = self.campana()
        self.gasto(c, '1000.00')
        self.ganado('AD1', cobrado='5800.00')
        snapshots.generar(HOY, self.p)
        primera = SnapshotKPI.objects.count()
        snapshots.generar(HOY, self.p)
        self.assertEqual(SnapshotKPI.objects.count(), primera)
        total = SnapshotKPI.objects.get(fecha=HOY, tipo_servicio='', campana__isnull=True)
        self.assertEqual((total.gasto, total.cac, total.roas_cobrado), (Decimal('1000.00'), Decimal('1000.00'),
                                                                        Decimal('5.00')))
        self.assertTrue(SnapshotKPI.objects.filter(fecha=HOY, tipo_servicio='EVENTO', campana=c).exists())

    def test_comando(self):
        salida = StringIO()
        call_command('generar_snapshot_marketing', '--fecha', '2026-10-31', stdout=salida)
        self.assertIn('Instantánea 31/10/2026', salida.getvalue())
        self.assertTrue(SnapshotKPI.objects.filter(fecha=HOY, tipo_servicio='').exists())

    def test_tarea_en_el_programador(self):
        from core_erp.programador import POR_NOMBRE
        self.assertIn('generar_snapshot_marketing', POR_NOMBRE)


class PantallaTest(Base):
    def test_solo_superusuario(self):
        url = reverse('marketing:tablero')
        self.assertEqual(self.client.get(url).status_code, 302)
        staff = User.objects.create_user('staff', password='x', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_superusuario_ve_la_pantalla_sin_datos_y_con_datos(self):
        admin = User.objects.create_superuser('dir', 'd@x.com', 'x')
        login_superuser_con_totp(self.client, admin)
        r = self.client.get(reverse('marketing:tablero'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Anticipos esperados')
        c = self.campana()
        self.gasto(c, '1000.00', dia=timezone.localdate())
        r = self.client.get(reverse('marketing:tablero'))
        self.assertContains(r, 'Camp C1')
        self.assertNotContains(r, 'style=')

    def test_supuestos_oculto_del_menu_pero_accesible(self):
        from django.conf import settings
        self.assertIn('marketing.ParametroMarketing', settings.JAZZMIN_SETTINGS['hide_models'])
        admin = User.objects.create_superuser('dir', 'd@x.com', 'x')
        login_superuser_con_totp(self.client, admin)
        self.assertEqual(self.client.get(reverse('admin:marketing_parametromarketing_changelist')).status_code, 200)
