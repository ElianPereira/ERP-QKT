"""Decisiones de marketing (fase 4): cada regla dispara y no dispara, muestra insuficiente, idempotencia,
expiración, prospecto sin respuesta, bloque del lunes, permisos y CSRF de los botones."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente, Cotizacion
from comunicacion import services_direccion
from comunicacion.models import ConversacionWhatsApp, Hallazgo, MensajeWhatsApp, Pendiente
from comunicacion.tests.test_fase_4 import MocksWA, _resp
from core_erp import impuestos
from core_erp.programador import POR_NOMBRE
from core_erp.test_utils import login_superuser_con_totp
from indicadores.models import ParametrosNegocio
from marketing.models import Anuncio, Campana, Decision, GastoPublicitario, Lead, ParametroMarketing, SnapshotKPI
from marketing.services import decisiones as d

User = get_user_model()
HOY = timezone.localdate()


def aware(dia, hora=12):
    return timezone.make_aware(datetime(dia.year, dia.month, dia.day, hora))


class Base(TestCase):
    def setUp(self):
        self.p = ParametroMarketing.objects.create(muestra_minima_leads=1, ventana_dias=90)
        self.n = 0
        self.ahora = timezone.now()

    def campana(self, ext='C1', servicio='EVENTO', presupuesto='100.00', estado='ACTIVE', anuncio='AD1',
                estado_anuncio='ACTIVE'):
        c = Campana.objects.create(id_externo=ext, nombre=f'Camp {ext}', tipo_servicio=servicio, estado=estado,
                                   presupuesto_diario=Decimal(presupuesto))
        if anuncio:
            Anuncio.objects.create(campana=c, id_externo=anuncio, nombre=f'Anuncio {anuncio}', estado=estado_anuncio)
        return c

    def gasto(self, campana, monto, dia=HOY, anuncio=None, **kw):
        return GastoPublicitario.objects.create(campana=campana, fecha=dia, monto=Decimal(monto), anuncio=anuncio, **kw)

    def lead(self, anuncio='', etapa='NUEVO', cot=None, fuente='OTRO', dia=HOY, **kw):
        self.n += 1
        kw.setdefault('telefono', f'52555511{self.n:04d}')
        return Lead.objects.create(anuncio_id=anuncio, etapa=etapa, cotizacion=cot,
                                   fuente=fuente, fecha_primer_contacto=aware(dia), **kw)

    def cotizacion(self, subtotal='10000', estado='CONFIRMADA', tipo='EVENTO', dias=30, salida=None):
        self.n += 1
        cli = Cliente.objects.create(nombre=f'C{self.n}', telefono=f'52555500{self.n:04d}')
        cot = Cotizacion.objects.create(cliente=cli, tipo_servicio=tipo, nombre_evento='X',
                                        fecha_evento=HOY + timedelta(days=dias + self.n), fecha_salida=salida)
        base = Decimal(subtotal)
        iva = impuestos.iva_de(base)
        Cotizacion.objects.filter(pk=cot.pk).update(subtotal=base, iva=iva, precio_final=base + iva, estado=estado)
        cot.refresh_from_db()
        return cot

    def ocupar(self, desde, hasta):
        """Hospedaje confirmado que ocupa [HOY+desde, HOY+hasta)."""
        cot = self.cotizacion(tipo='HOSPEDAJE', dias=0)
        Cotizacion.objects.filter(pk=cot.pk).update(fecha_evento=HOY + timedelta(days=desde),
                                                    fecha_salida=HOY + timedelta(days=hasta))

    def correr(self, *reglas):
        return d.generar(hoy=HOY, ahora=self.ahora, parametros=self.p, reglas=reglas)

    def ganado(self, anuncio, subtotal='10000'):
        return self.lead(anuncio, 'GANADO', self.cotizacion(subtotal))

    def assert_una(self, resultado, regla):
        self.assertEqual([x.regla for x in resultado.creadas], [regla])
        return resultado.creadas[0]


class PausarAnuncioTest(Base):
    def setUp(self):
        super().setUp()
        self.c = self.campana()
        self.ad = Anuncio.objects.get(id_externo='AD1')
        self.lead('AD2')  # el rastreo por anuncio funciona

    def test_dispara_con_gasto_y_cero_cotizaciones(self):
        self.gasto(self.c, '2000.00', anuncio=self.ad)
        dec = self.assert_una(self.correr(d._pausar_anuncios), 'PAUSAR_ANUNCIO')
        self.assertEqual(dec.tipo_accion, 'PAUSAR')
        self.assertEqual(dec.accion_propuesta['anuncio_externo'], 'AD1')
        self.assertEqual(dec.hallazgo.tipo, 'MKT_PAUSAR')
        self.assertIn('Gasto sin IVA: $2,000.00', dec.hallazgo.detalle)

    def test_no_dispara_bajo_el_umbral_con_cotizacion_o_pausado(self):
        self.gasto(self.c, '1000.00', anuncio=self.ad)
        self.assertEqual(self.correr(d._pausar_anuncios).creadas, [])
        GastoPublicitario.objects.update(monto=Decimal('2000.00'))
        self.lead('AD1', 'COTIZADO', self.cotizacion(estado='COTIZADA'))
        self.assertEqual(self.correr(d._pausar_anuncios).creadas, [])
        Lead.objects.filter(anuncio_id='AD1').delete()
        Anuncio.objects.update(estado='PAUSED')
        self.assertEqual(self.correr(d._pausar_anuncios).creadas, [])

    def test_sin_prospectos_atribuidos_es_datos_insuficientes(self):
        Lead.objects.all().delete()
        self.gasto(self.c, '2000.00', anuncio=self.ad)
        r = self.correr(d._pausar_anuncios)
        self.assertEqual(r.creadas, [])
        self.assertTrue(any('PAUSAR_ANUNCIO' in x for x in r.insuficientes))


class ReasignarTest(Base):
    def setUp(self):
        super().setUp()
        self.a = self.campana('A', anuncio='ADA', presupuesto='100.00')
        self.b = self.campana('B', anuncio='ADB', presupuesto='200.00')
        self.gasto(self.a, '1000.00')
        self.gasto(self.b, '1000.00')

    def test_dispara_con_cac_30_por_ciento_mayor(self):
        self.ganado('ADA')
        self.ganado('ADA')       # CAC A = 500
        self.ganado('ADB')       # CAC B = 1000
        dec = self.assert_una(self.correr(d._reasignar), 'REASIGNAR')
        a = dec.accion_propuesta
        self.assertEqual((a['desde_campana_id'], a['hacia_campana_id']), (self.b.pk, self.a.pk))
        self.assertEqual(a['monto_diario'], '20.00')           # 20 % del menor presupuesto (A = 100)
        self.assertEqual(a['desde_nuevo'], '180.00')
        self.assertEqual(a['hacia_nuevo'], '120.00')

    def test_no_dispara_con_cac_parecido(self):
        self.ganado('ADA')
        self.ganado('ADB')
        self.assertEqual(self.correr(d._reasignar).creadas, [])

    def test_muestra_insuficiente_en_una_campana(self):
        self.p.muestra_minima_leads = 2
        self.p.save()
        self.ganado('ADA')
        self.ganado('ADA')
        self.ganado('ADB')       # B solo tiene 1 prospecto de 2
        r = self.correr(d._reasignar)
        self.assertEqual(r.creadas, [])
        self.assertTrue(any('REASIGNAR' in x for x in r.insuficientes))


class EscalarTest(Base):
    def setUp(self):
        super().setUp()
        self.c = self.campana(presupuesto='100.00')
        self.gasto(self.c, '500.00')

    def snaps(self, cac=('100', '100', '100'), tope='500', prospectos=5):
        for n, valor in enumerate(cac):
            SnapshotKPI.objects.create(fecha=HOY - timedelta(days=7 * n), campana=self.c, tipo_servicio='EVENTO',
                                       ventana_dias=90, prospectos=prospectos, cac=Decimal(valor),
                                       cac_tope=Decimal(tope))

    def test_dispara_con_tres_semanas_bajo_el_tope(self):
        self.snaps()
        dec = self.assert_una(self.correr(d._escalar), 'ESCALAR')
        self.assertEqual((dec.tipo_accion, dec.accion_propuesta['presupuesto_nuevo']), ('AJUSTAR_PRESUPUESTO', '120.00'))
        self.assertEqual(dec.accion_propuesta['cambio_pct'], '20')

    def test_no_dispara_si_una_semana_rebasa_el_tope_o_la_agenda_esta_llena(self):
        self.snaps(cac=('100', '600', '100'))
        self.assertEqual(self.correr(d._escalar).creadas, [])
        SnapshotKPI.objects.all().update(cac=Decimal('100'))
        self.p.escalar_ocupacion_max = Decimal('0')
        self.assertEqual(self.correr(d._escalar).creadas, [])

    def test_sin_tres_semanas_o_sin_muestra_es_datos_insuficientes(self):
        SnapshotKPI.objects.create(fecha=HOY, campana=self.c, tipo_servicio='EVENTO', ventana_dias=90, prospectos=5,
                                   cac=Decimal('100'), cac_tope=Decimal('500'))
        r = self.correr(d._escalar)
        self.assertEqual(r.creadas, [])
        self.assertTrue(any('ESCALAR' in x for x in r.insuficientes))
        SnapshotKPI.objects.all().delete()
        self.p.muestra_minima_leads = 10
        self.snaps(prospectos=5)
        self.assertEqual(self.correr(d._escalar).creadas, [])


class OcupacionTest(Base):
    def test_agenda_llena_baja_la_pauta(self):
        c = self.campana(presupuesto='100.00')
        self.ocupar(1, 31)
        dec = self.assert_una(self.correr(d._ocupacion_alta), 'OCUPACION_ALTA')
        self.assertEqual((dec.accion_propuesta['campana_id'], dec.accion_propuesta['presupuesto_nuevo']),
                         (c.pk, '80.00'))

    def test_agenda_con_espacio_no_baja(self):
        self.campana()
        self.assertEqual(self.correr(d._ocupacion_alta).creadas, [])

    def test_fines_libres_suben_la_pauta_de_eventos(self):
        c = self.campana(presupuesto='100.00')
        self.campana('H', servicio='HOSPEDAJE', anuncio='ADH')
        dec = self.assert_una(self.correr(d._fines_libres), 'FINES_LIBRES')
        self.assertEqual((dec.accion_propuesta['campana_id'], dec.accion_propuesta['presupuesto_nuevo']),
                         (c.pk, '120.00'))

    def test_sin_fines_libres_o_con_agenda_llena_no_sube(self):
        self.campana()
        self.p.fines_libres_min = 100
        self.assertEqual(self.correr(d._fines_libres).creadas, [])
        self.p.fines_libres_min = 4
        self.ocupar(1, 46)
        self.assertEqual(self.correr(d._fines_libres).creadas, [])

    def test_no_se_duplica_un_ajuste_abierto_de_la_misma_campana(self):
        self.campana()
        self.assert_una(self.correr(d._fines_libres), 'FINES_LIBRES')
        self.assertEqual(self.correr(d._fines_libres).creadas, [])


class CajaTest(Base):
    def test_dispara_si_pauta_y_costos_superan_los_cobros(self):
        ParametrosNegocio.objects.create(costo_fijo_mensual_override=Decimal('50000.00'))
        self.campana(presupuesto='1000.00')
        dec = self.assert_una(self.correr(d._caja), 'CAJA')
        self.assertEqual(dec.tipo_accion, 'INFORMATIVA')
        self.assertEqual(dec.evidencia['costos_fijos'], '50000.00')
        self.assertEqual(dec.objeto_clave, f'mes:{HOY:%Y-%m}')

    def test_no_dispara_si_los_cobros_alcanzan(self):
        ParametrosNegocio.objects.create(costo_fijo_mensual_override=Decimal('50000.00'))
        self.campana(presupuesto='1000.00')
        self.cotizacion('1000000', dias=0)       # saldo por cobrar con plazo dentro de 30 días
        self.assertEqual(self.correr(d._caja).creadas, [])

    def test_sin_costos_fijos_es_datos_insuficientes(self):
        self.campana()
        r = self.correr(d._caja)
        self.assertEqual(r.creadas, [])
        self.assertTrue(any('CAJA' in x for x in r.insuficientes))


class ConcentracionTest(Base):
    def test_dispara_sobre_60_por_ciento(self):
        for _ in range(4):
            self.lead(fuente='META_ANUNCIO')
        self.lead(fuente='WHATSAPP')
        dec = self.assert_una(self.correr(d._concentracion), 'CONCENTRACION')
        self.assertEqual(dec.evidencia['porcentaje'], '80.00')

    def test_no_dispara_repartido_ni_con_muestra_insuficiente(self):
        for fuente in ('META_ANUNCIO', 'META_ANUNCIO', 'WHATSAPP', 'OTRO', 'GOOGLE'):
            self.lead(fuente=fuente)
        self.assertEqual(self.correr(d._concentracion).creadas, [])
        self.p.muestra_minima_leads = 10
        r = self.correr(d._concentracion)
        self.assertEqual(r.creadas, [])
        self.assertTrue(any('CONCENTRACION' in x for x in r.insuficientes))


class AnomaliaTest(Base):
    def setUp(self):
        super().setUp()
        self.c = self.campana()
        self.ayer = HOY - timedelta(days=1)

    def previos(self, n=7, monto='100.00'):
        for i in range(2, 2 + n):
            self.gasto(self.c, monto, dia=HOY - timedelta(days=i))

    def test_gasto_de_mas_del_doble_dispara(self):
        self.previos()
        self.gasto(self.c, '250.00', dia=self.ayer)
        dec = self.assert_una(self.correr(d._gasto_anomalo), 'GASTO_ANOMALO')
        self.assertEqual((dec.evidencia['gasto'], dec.evidencia['promedio']), ('250.00', '100.00'))

    def test_gasto_normal_no_dispara(self):
        self.previos()
        self.gasto(self.c, '150.00', dia=self.ayer)
        self.assertEqual(self.correr(d._gasto_anomalo).creadas, [])

    def test_pocos_dias_previos_es_datos_insuficientes(self):
        self.previos(n=2)
        self.gasto(self.c, '900.00', dia=self.ayer)
        r = self.correr(d._gasto_anomalo)
        self.assertEqual(r.creadas, [])
        self.assertTrue(any('GASTO_ANOMALO' in x for x in r.insuficientes))

    def test_frecuencia_alta_dispara_baja_no_y_pocas_impresiones_es_insuficiente(self):
        self.gasto(self.c, '100.00', frecuencia=Decimal('2.00'), impresiones=5000)
        self.assertEqual(self.correr(d._frecuencia).creadas, [])
        GastoPublicitario.objects.update(frecuencia=Decimal('5.00'), impresiones=100)
        r = self.correr(d._frecuencia)
        self.assertEqual(r.creadas, [])
        self.assertTrue(any('FRECUENCIA' in x for x in r.insuficientes))
        GastoPublicitario.objects.update(impresiones=5000)
        self.assert_una(self.correr(d._frecuencia), 'FRECUENCIA')


class NoConciliadoTest(Base):
    def test_mes_con_diferencia_dispara_y_conciliado_no(self):
        c = self.campana()
        mes = (HOY.replace(day=1) - timedelta(days=1)).replace(day=10)
        self.gasto(c, '800.00', dia=mes, estado_conciliacion=GastoPublicitario.CONCILIADO)
        self.assertEqual(self.correr(d._no_conciliado).creadas, [])
        GastoPublicitario.objects.update(estado_conciliacion=GastoPublicitario.DIFERENCIA)
        dec = self.assert_una(self.correr(d._no_conciliado), 'NO_CONCILIADO')
        self.assertEqual(dec.objeto_clave, f'mes:{mes:%Y-%m}')


class CicloDeVidaTest(Base):
    def propuesta(self, **kw):
        datos = dict(regla='CONCENTRACION', objeto='fuente:X', tipo_accion='INFORMATIVA', titulo='Algo',
                     lineas=['detalle'], accion={'tipo': 'INFORMATIVA'})
        datos.update(kw)
        return lambda ctx: iter([d.Propuesta(**datos)])

    def test_idempotente_una_decision_abierta_por_regla_y_objeto(self):
        regla = self.propuesta()
        self.assertEqual(len(self.correr(regla).creadas), 1)
        self.assertEqual(self.correr(regla).creadas, [])
        self.assertEqual((Decision.objects.count(), Hallazgo.objects.count()), (1, 1))
        self.assertEqual(Hallazgo.objects.get().tipo, 'MKT_CONCENTRA')

    def test_rechazada_no_se_reproduce_durante_el_periodo_de_enfriamiento(self):
        regla = self.propuesta()
        dec = self.correr(regla).creadas[0]
        admin = User.objects.create_superuser('dir', 'd@x.com', 'x')
        self.assertEqual(d.resolver(dec.pk, 'rechazar', admin)[0], True)
        self.assertEqual(self.correr(regla).creadas, [])
        dec.refresh_from_db()
        self.assertEqual((dec.estado, dec.hallazgo.estado), ('RECHAZADA', 'IGNORADO'))

    def test_expira_a_los_14_dias_y_cierra_su_hallazgo(self):
        dec = self.correr(self.propuesta()).creadas[0]
        self.assertEqual(dec.expira_en, self.ahora + timedelta(days=14))
        r = d.generar(hoy=HOY, ahora=self.ahora + timedelta(days=14, minutes=1), parametros=self.p, reglas=())
        self.assertEqual(r.expiradas, 1)
        dec.refresh_from_db()
        self.assertEqual((dec.estado, dec.hallazgo.estado), ('EXPIRADA', 'IGNORADO'))

    def test_pospuesta_vuelve_a_pendiente_al_vencer_el_plazo(self):
        dec = self.correr(self.propuesta()).creadas[0]
        admin = User.objects.create_superuser('dir', 'd@x.com', 'x')
        self.assertTrue(d.resolver(dec.pk, 'posponer', admin)[0])
        dec.refresh_from_db()
        self.assertEqual(dec.estado, 'POSPUESTA')
        r = d.generar(hoy=HOY, ahora=self.ahora + timedelta(days=8), parametros=self.p, reglas=())
        self.assertEqual(r.reabiertas, 1)
        dec.refresh_from_db()
        self.assertEqual(dec.estado, 'PENDIENTE')

    def test_una_regla_que_falla_no_detiene_a_las_demas(self):
        def rota(ctx):
            raise RuntimeError('boom')
            yield  # pragma: no cover
        r = self.correr(rota, self.propuesta())
        self.assertEqual(len(r.creadas), 1)

    def test_resolver_exige_superusuario_y_no_repite(self):
        dec = self.correr(self.propuesta()).creadas[0]
        staff = User.objects.create_user('staff', password='x', is_staff=True)
        self.assertFalse(d.resolver(dec.pk, 'aprobar', staff)[0])
        admin = User.objects.create_superuser('dir', 'd@x.com', 'x')
        self.assertTrue(d.resolver(dec.pk, 'aprobar', admin)[0])
        self.assertFalse(d.resolver(dec.pk, 'rechazar', admin)[0])
        dec.refresh_from_db()
        self.assertEqual((dec.estado, dec.decidido_por, dec.hallazgo.estado), ('APROBADA', admin, 'ATENDIDO'))
        self.assertIsNotNone(dec.decidido_en)

    def test_ignorar_el_hallazgo_desde_comunicacion_rechaza_la_decision(self):
        dec = self.correr(self.propuesta()).creadas[0]
        self.assertIsNone(services_direccion.crear_tarea(dec.hallazgo))
        self.assertTrue(services_direccion.ignorar(dec.hallazgo))
        dec.refresh_from_db()
        self.assertEqual(dec.estado, 'RECHAZADA')

    def test_tarea_del_programador(self):
        self.assertIn('generar_decisiones_marketing', POR_NOMBRE)
        self.assertIn('revisar_respuesta_prospectos', POR_NOMBRE)


class SinRespuestaTest(MocksWA, Base):
    def setUp(self):
        super().setUp()
        self.manana = HOY + timedelta(days=1)
        self.lead_con_chat = self._lead_con_chat()

    def _lead_con_chat(self, etapa='NUEVO'):
        conv = ConversacionWhatsApp.objects.create(telefono='525555559999', nombre='Ana')
        return self.lead(etapa=etapa, conversacion=conv, telefono='525555559999', nombre='Ana')

    def msg(self, direccion, cuando):
        return MensajeWhatsApp.objects.create(conversacion=self.lead_con_chat.conversacion, direccion=direccion,
                                              texto='hola secreto', created_at=cuando)

    def a_las(self, hora, minuto=0):
        return timezone.make_aware(datetime.combine(HOY, time(hora, minuto)))

    def test_dispara_urgente_al_encargado_sin_texto_del_cliente(self):
        ahora = self.a_las(12)
        self.msg('ENTRADA', ahora - timedelta(minutes=10))
        creados = d.revisar_sin_respuesta(ahora, self.p)
        self.assertEqual(len(creados), 1)
        p = Pendiente.objects.get()
        self.assertEqual((p.tipo, p.severidad, p.rol), ('PROSPECTO', 'URGENTE', 'ENCARGADO'))
        self.assertNotIn('secreto', p.detalle)
        d.revisar_sin_respuesta(ahora + timedelta(minutes=5), self.p)
        self.assertEqual(Pendiente.objects.count(), 1)

    def test_no_dispara_si_respondieron_es_pronto_o_esta_cerrado(self):
        ahora = self.a_las(12)
        self.msg('ENTRADA', ahora - timedelta(minutes=10))
        self.msg('AGENTE', ahora - timedelta(minutes=8))
        self.assertEqual(d.revisar_sin_respuesta(ahora, self.p), [])
        MensajeWhatsApp.objects.all().delete()
        self.msg('ENTRADA', ahora - timedelta(minutes=3))
        self.assertEqual(d.revisar_sin_respuesta(ahora, self.p), [])
        MensajeWhatsApp.objects.all().delete()
        self.msg('ENTRADA', ahora - timedelta(minutes=30))
        Lead.objects.update(etapa='PERDIDO')
        self.assertEqual(d.revisar_sin_respuesta(ahora, self.p), [])

    def test_solo_de_8_a_20_y_lo_nocturno_cuenta_desde_las_8(self):
        self.msg('ENTRADA', self.a_las(7, 50))
        self.assertEqual(d.revisar_sin_respuesta(self.a_las(21), self.p), [])
        self.assertEqual(d.revisar_sin_respuesta(self.a_las(8, 3), self.p), [])   # solo 3 min desde las 8:00
        self.assertEqual(len(d.revisar_sin_respuesta(self.a_las(8, 10), self.p)), 1)


class BloqueLunesTest(MocksWA, Base):
    def setUp(self):
        super().setUp()
        _resp('DIRECCION', '525555550021')

    def test_sin_nada_no_hay_bloque(self):
        self.assertIsNone(d.bloque_resumen(HOY, self.p))

    def test_bloque_con_cinco_cifras_y_decisiones(self):
        c = self.campana('A', anuncio='ADA')
        self.gasto(c, '1000.00')
        self.ganado('ADA')
        self.lead('ADA')
        decision = Decision.objects.create(regla='X', objeto_clave='y', tipo_accion='INFORMATIVA', titulo='Revisa el canal',
                                           expira_en=self.ahora + timedelta(days=14))
        bloque = d.bloque_resumen(HOY, self.p)
        texto = '\n'.join(bloque['lineas'])
        self.assertIn('gasto de la semana: $1,000.00', texto)
        self.assertIn('prospectos 2, cotizaciones 1, ganados 1', texto)
        self.assertIn('CAC (90 días) de Evento: $1,000.00', texto)
        self.assertIn(f'Marketing propone: {decision.titulo}', texto)
        self.assertIn('#decisiones', bloque['enlace'])

    def test_el_resumen_del_lunes_lo_incluye(self):
        Decision.objects.create(regla='X', objeto_clave='y', tipo_accion='INFORMATIVA', titulo='Revisa el canal',
                                expira_en=self.ahora + timedelta(days=14))
        with patch.object(services_direccion, 'detectar', return_value=[]):
            self.assertEqual(services_direccion.enviar_resumen_semanal(HOY), 1)
        texto = self.textos()[0]
        self.assertIn('Marketing propone: Revisa el canal', texto)
        self.assertIn('Decisiones de marketing:', texto)

    def test_un_fallo_del_bloque_no_tumba_el_resumen(self):
        with patch('marketing.services.decisiones.bloque_resumen', side_effect=RuntimeError('boom')), \
                patch.object(services_direccion, 'detectar', return_value=[]):
            self.assertEqual(services_direccion.enviar_resumen_semanal(HOY), 1)
        self.assertNotIn('Marketing', self.textos()[0])


class BotonesTest(Base):
    def setUp(self):
        super().setUp()
        self.dec = Decision.objects.create(
            regla='X', objeto_clave='y', tipo_accion='INFORMATIVA', titulo='Revisa el canal',
            hallazgo=Hallazgo.objects.create(clave='mkt:X', tipo='MKT_CONCENTRA', titulo='t'),
            expira_en=self.ahora + timedelta(days=14))
        self.admin = User.objects.create_superuser('dir', 'd@x.com', 'x')

    def url(self, accion):
        return reverse('marketing:decidir', args=[self.dec.pk, accion])

    def test_tarjeta_con_los_tres_botones(self):
        login_superuser_con_totp(self.client, self.admin)
        r = self.client.get(reverse('marketing:tablero'))
        self.assertContains(r, 'id="decisiones"')
        self.assertContains(r, 'Revisa el canal')
        for accion in ('aprobar', 'posponer', 'rechazar'):
            self.assertContains(r, self.url(accion))
        self.assertContains(r, 'data-qkt-confirmar')
        self.assertNotContains(r, 'style=')

    def test_aprobar_deja_constancia_y_no_ejecuta_nada(self):
        login_superuser_con_totp(self.client, self.admin)
        r = self.client.post(self.url('aprobar'))
        self.assertEqual(r.status_code, 302)
        self.dec.refresh_from_db()
        self.assertEqual((self.dec.estado, self.dec.decidido_por, self.dec.ejecutado_en), ('APROBADA', self.admin, None))

    def test_get_no_decide(self):
        login_superuser_con_totp(self.client, self.admin)
        self.assertEqual(self.client.get(self.url('aprobar')).status_code, 405)
        self.dec.refresh_from_db()
        self.assertEqual(self.dec.estado, 'PENDIENTE')

    def test_staff_sin_superusuario_y_anonimo_no_deciden(self):
        self.assertEqual(self.client.post(self.url('aprobar')).status_code, 302)
        staff = User.objects.create_user('staff', password='x', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.post(self.url('aprobar')).status_code, 403)
        self.dec.refresh_from_db()
        self.assertEqual(self.dec.estado, 'PENDIENTE')

    def test_exige_csrf(self):
        cliente = Client(enforce_csrf_checks=True)
        login_superuser_con_totp(cliente, self.admin)
        self.assertEqual(cliente.post(self.url('aprobar')).status_code, 403)
        self.dec.refresh_from_db()
        self.assertEqual(self.dec.estado, 'PENDIENTE')

    def test_accion_invalida(self):
        login_superuser_con_totp(self.client, self.admin)
        self.client.post(self.url('borrar'))
        self.dec.refresh_from_db()
        self.assertEqual(self.dec.estado, 'PENDIENTE')

    def test_historial_en_el_admin_de_solo_lectura(self):
        login_superuser_con_totp(self.client, self.admin)
        self.assertEqual(self.client.get(reverse('admin:marketing_decision_changelist')).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:marketing_decision_add')).status_code, 403)


