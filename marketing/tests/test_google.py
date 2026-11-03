"""Google Ads (fase 8): proveedor REST mockeado, gasto, conciliación, gclid y conversiones offline."""
import json
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import Mock, patch

import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from comercial import views_cotizador
from marketing.models import Campana, EventoConversion, GastoPublicitario, Lead
from marketing.services import conciliacion, conversiones, conversiones_google, gasto, retencion
from marketing.services.proveedores import (
    EstadoCredencial,
    GastoDiario,
    ProveedorAnuncios,
    ProveedorError,
    ProveedorGoogle,
    TokenVencidoError,
)
from marketing.tests import test_pauta
from marketing.tests.test_conversiones import ConversionesBase, respuesta

CRED = dict(client_id='cid', client_secret='SECRETO-CLIENTE', refresh_token='REFRESCO-X', developer_token='DEV-TOKEN',
            customer_id='123-456-7890', login_customer_id='999-888-7777', version='v99')
ADS = dict(GOOGLE_ADS_CLIENT_ID='cid', GOOGLE_ADS_CLIENT_SECRET='SECRETO-CLIENTE', GOOGLE_ADS_REFRESH_TOKEN='REFRESCO-X',
           GOOGLE_ADS_DEVELOPER_TOKEN='DEV-TOKEN', GOOGLE_ADS_CUSTOMER_ID='1234567890',
           GOOGLE_ADS_CONVERSION_LEAD='555', GOOGLE_ADS_CONVERSION_COMPRA='777')
OAUTH = respuesta(200, {'access_token': 'ACCESO-Y'})
CLIC = 'Cj0KCQjw_clic-de-prueba-1234'


def proveedor():
    return ProveedorGoogle(**CRED)


class ProveedorGoogleTest(SimpleTestCase):
    def test_apagado_sin_credenciales_o_con_una_faltante(self):
        self.assertFalse(ProveedorGoogle(client_id='', client_secret='', refresh_token='', developer_token='',
                                         customer_id='').configurado())
        self.assertFalse(ProveedorGoogle(**{**CRED, 'developer_token': ''}).configurado())
        self.assertTrue(proveedor().configurado())

    def test_micros_a_pesos_con_half_up(self):
        self.assertEqual(ProveedorGoogle.pesos('1234567'), Decimal('1.23'))
        self.assertEqual(ProveedorGoogle.pesos('1235000'), Decimal('1.24'))
        self.assertEqual(ProveedorGoogle.pesos('2000000000'), Decimal('2000.00'))
        self.assertIsInstance(ProveedorGoogle.pesos('10'), Decimal)

    def test_gaql_gasto_por_anuncio_y_resto_de_campana(self):
        anuncios = [{'results': [
            {'segments': {'date': '2026-10-01'}, 'campaign': {'id': '11', 'name': 'Bodas'},
             'adGroupAd': {'ad': {'id': '901', 'name': 'A'}},
             'metrics': {'costMicros': '3000000', 'impressions': '100', 'clicks': '5'}}]},
            {'results': [
                {'segments': {'date': '2026-10-01'}, 'campaign': {'id': '11', 'name': 'Bodas'},
                 'adGroupAd': {'ad': {'id': '901', 'name': 'A'}},
                 'metrics': {'costMicros': '1235000', 'impressions': '10', 'clicks': '1'}}]}]
        campanas = [{'results': [
            {'segments': {'date': '2026-10-01'}, 'campaign': {'id': '11', 'name': 'Bodas'},
             'metrics': {'costMicros': '9000000', 'impressions': '500', 'clicks': '40'}},
            {'segments': {'date': '2026-10-01'}, 'campaign': {'id': '22', 'name': 'Pmax'},
             'metrics': {'costMicros': '0', 'impressions': '0', 'clicks': '0'}}]}]
        with patch('marketing.services.proveedores.requests.post',
                   side_effect=[OAUTH, respuesta(200, anuncios), respuesta(200, campanas)]) as post:
            filas = list(proveedor().gasto_diario(date(2026, 10, 1), date(2026, 10, 1)))
        self.assertEqual(len(filas), 2)
        por_anuncio, resto = filas
        self.assertEqual((por_anuncio.anuncio_id, por_anuncio.monto, por_anuncio.clics), ('901', Decimal('4.24'), 6))
        self.assertEqual((resto.anuncio_id, resto.campana_id, resto.monto), ('', '11', Decimal('4.77')))
        # Cabeceras, versión y cliente de la URL; la consulta pide el rango pedido.
        url, kw = post.call_args_list[1].args[0], post.call_args_list[1].kwargs
        self.assertEqual(url, 'https://googleads.googleapis.com/v99/customers/1234567890/googleAds:searchStream')
        self.assertEqual(kw['headers']['developer-token'], 'DEV-TOKEN')
        self.assertEqual(kw['headers']['login-customer-id'], '9998887777')
        self.assertEqual(kw['headers']['Authorization'], 'Bearer ACCESO-Y')
        self.assertIn("BETWEEN '2026-10-01' AND '2026-10-01'", json.loads(kw['data'])['query'])

    def test_moneda_y_campanas(self):
        with patch('marketing.services.proveedores.requests.post', side_effect=[
                OAUTH, respuesta(200, [{'results': [{'customer': {'currencyCode': 'MXN'}}]}]),
                respuesta(200, [{'results': [{'campaign': {'id': '5', 'name': 'X', 'status': 'ENABLED',
                                                           'advertisingChannelType': 'SEARCH'},
                                              'campaignBudget': {'amountMicros': '150000000'}}]}])]):
            p = proveedor()
            self.assertEqual(p.moneda(), 'MXN')
            c = list(p.listar_campanas())[0]
        self.assertEqual((c.id_externo, c.estado, c.presupuesto_diario), ('5', 'ENABLED', Decimal('150.00')))

    def test_credencial_rechazada_no_filtra_secretos(self):
        mala = respuesta(400, {'error': 'invalid_grant'})
        with patch('marketing.services.proveedores.requests.post', return_value=mala):
            estado = proveedor().estado_credencial()
            self.assertFalse(estado.valida)
            with self.assertRaises(TokenVencidoError) as ctx:
                proveedor().moneda()
        for secreto in ('SECRETO-CLIENTE', 'REFRESCO-X', 'DEV-TOKEN'):
            self.assertNotIn(secreto, str(ctx.exception) + estado.detalle)

    def test_error_de_api_enmascara_el_token(self):
        err = respuesta(403, {'error': {'status': 'PERMISSION_DENIED', 'message': 'mal DEV-TOKEN y ACCESO-Y'}})
        with patch('marketing.services.proveedores.requests.post', side_effect=[OAUTH, err]):
            with self.assertRaises(Exception) as ctx:
                proveedor().moneda()
        self.assertNotIn('DEV-TOKEN', str(ctx.exception))
        self.assertNotIn('ACCESO-Y', str(ctx.exception))

    def test_falla_de_red(self):
        with patch('marketing.services.proveedores.requests.post', side_effect=requests.ConnectionError('x')):
            with self.assertRaises(Exception):
                proveedor().moneda()

    def test_sin_acciones_de_escritura(self):
        p = proveedor()
        self.assertFalse(p.puede_escribir())
        with self.assertRaises(NotImplementedError):
            p.pausar('1')


class FalsoGoogle(ProveedorAnuncios):
    plataforma = 'GOOGLE'
    nombre = 'Google Ads'
    fuente_gasto = 'GOOGLE_API'

    def __init__(self, filas, activo=True):
        self.filas, self.activo = filas, activo

    def configurado(self):
        return self.activo

    def moneda(self):
        return 'MXN'

    def listar_campanas(self):
        return iter([])

    def listar_anuncios(self):
        return iter([])

    def gasto_diario(self, desde, hasta):
        return iter(self.filas)

    def estado_credencial(self):
        return EstadoCredencial(True)


def gg(dia, monto='100.00', ad='GA1', campana='G1'):
    return GastoDiario(fecha=dia, campana_id=campana, anuncio_id=ad, monto=Decimal(monto), impresiones=10, clics=2,
                       campana_nombre='Búsqueda', anuncio_nombre='Anuncio G')


class GastoGoogleTest(TestCase):
    d = date(2026, 10, 1)

    def test_sin_credenciales_no_hace_nada(self):
        r = gasto.sincronizar(proveedor=FalsoGoogle([gg(self.d)], activo=False))
        self.assertTrue(r['omitido'])
        self.assertFalse(GastoPublicitario.objects.exists())

    def test_upsert_idempotente_con_plataforma_y_fuente_de_google(self):
        d = self.d
        self.assertEqual(gasto.sincronizar(d, d, proveedor=FalsoGoogle([gg(d)]))['filas'], 1)
        self.assertEqual(gasto.sincronizar(d, d, proveedor=FalsoGoogle([gg(d)]))['filas'], 0)
        g = GastoPublicitario.objects.get()
        self.assertEqual((g.fuente, g.campana.plataforma, g.monto), ('GOOGLE_API', 'GOOGLE', Decimal('100.00')))
        self.assertEqual(gasto.sincronizar(d, d, proveedor=FalsoGoogle([gg(d, '120.00')]))['filas'], 1)
        self.assertEqual(GastoPublicitario.objects.count(), 1)

    def test_gasto_de_campana_sin_anuncio_es_idempotente_y_se_apaga_si_ya_lo_explican_anuncios(self):
        d = self.d
        resto = gg(d, '40.00', ad='')
        gasto.sincronizar(d, d, proveedor=FalsoGoogle([resto]))
        self.assertEqual(gasto.sincronizar(d, d, proveedor=FalsoGoogle([resto]))['filas'], 0)
        self.assertEqual(GastoPublicitario.objects.filter(anuncio__isnull=True).count(), 1)
        gasto.sincronizar(d, d, proveedor=FalsoGoogle([gg(d, '40.00')]))
        self.assertEqual(GastoPublicitario.objects.filter(anuncio__isnull=True).get().monto, Decimal('0.00'))

    def test_plataformas_no_se_mezclan_en_la_misma_campana(self):
        gasto.sincronizar(self.d, self.d, proveedor=FalsoGoogle([gg(self.d, campana='77')]))
        Campana.objects.create(plataforma='META', id_externo='77', nombre='Otra')
        self.assertEqual(Campana.objects.filter(id_externo='77').count(), 2)

    def test_comando_apagado_y_con_error_de_un_proveedor_no_detiene_al_otro(self):
        salida = StringIO()
        apagado = FalsoGoogle([], activo=False)
        with patch('marketing.management.commands.sincronizar_gasto_anuncios.proveedor_meta', return_value=apagado), \
                patch('marketing.management.commands.sincronizar_gasto_anuncios.proveedor_google', return_value=apagado):
            call_command('sincronizar_gasto_anuncios', stdout=salida)
        self.assertIn('apagada', salida.getvalue())
        buena = FalsoGoogle([gg(self.d)])
        with patch('marketing.management.commands.sincronizar_gasto_anuncios.proveedor_meta',
                   return_value=Mock(plataforma='META', nombre='Meta Ads', configurado=lambda: True)), \
                patch('marketing.management.commands.sincronizar_gasto_anuncios.gasto.sincronizar',
                      side_effect=[ProveedorError('boom'), {'filas': 1}]), \
                patch('marketing.management.commands.sincronizar_gasto_anuncios.gasto.revisar_credencial'), \
                patch('marketing.management.commands.sincronizar_gasto_anuncios.proveedor_google', return_value=buena):
            with self.assertRaises(CommandError) as ctx:
                call_command('sincronizar_gasto_anuncios', stdout=StringIO())
        self.assertIn('Meta Ads: boom', str(ctx.exception))


class ConciliacionGoogleTest(TestCase):
    cargo = test_pauta.ConciliacionTest.cargo

    def setUp(self):
        test_pauta.ConciliacionTest.setUp(self)
        gasto.sincronizar(date(2026, 9, 1), date(2026, 9, 1), proveedor=FalsoGoogle([gg(date(2026, 9, 1), '2000.00')]))

    def test_cuadra_con_iva_y_no_se_mezcla_con_meta(self):
        self.cargo('2320.00', 'GOOGLE *ADS3456789012 CC@GOOGLE.COM')  # 2,000 + IVA
        self.cargo('1740.00')  # Meta, no debe contar para Google
        r = conciliacion.conciliar_mes(2026, 9, plataforma='GOOGLE')
        self.assertEqual((r['esperado'], r['banco'], r['estado']), (Decimal('2320.00'), Decimal('2320.00'), 'CONCILIADO'))
        self.assertEqual(conciliacion.conciliar_mes(2026, 9)['banco'], Decimal('1740.00'))

    def test_otros_cargos_de_google_no_cuentan(self):
        self.cargo('2320.00', 'GOOGLE *GSUITE_QUINTA')
        r = conciliacion.conciliar_mes(2026, 9, plataforma='GOOGLE')
        self.assertEqual((r['banco'], r['estado']), (Decimal('0.00'), 'DIFERENCIA'))

    def test_diferencia_abre_un_pendiente_propio_de_google(self):
        from comunicacion.models import Pendiente
        self.cargo('900.00', 'GOOGLE *ADS123')
        for _ in range(2):
            conciliacion.conciliar_mes(2026, 9, plataforma='GOOGLE')
        self.assertEqual(Pendiente.objects.filter(clave_idempotencia='marketing_conciliacion:google:2026-09').count(), 1)
        self.assertEqual(set(GastoPublicitario.objects.filter(campana__plataforma='META')
                             .values_list('estado_conciliacion', flat=True)), {'PENDIENTE'})


@override_settings(**ADS)
class ConversionesGoogleTest(ConversionesBase):
    def lead_web(self, consentimiento=True, **clic):
        cli = self.cliente(email='ana@example.com')
        if consentimiento:
            self.acepta_marketing(cli)
        cot = self.cotizacion(cli, 'COTIZADA')
        conversiones_google.registrar_clic_web(cotizacion=cot, **(clic or {'gclid': CLIC}))
        return Lead.objects.get(), cot

    def enviar_google(self, ahora=None, respuestas=None):
        """Un solo parche de `requests.post`: el OAuth responde solo y las cargas salen de `respuestas`."""
        pendientes = list(respuestas or [respuesta(200, {'results': [{}]})])
        cargas = []

        def falso(url, **kw):
            if 'oauth2' in url:
                return OAUTH
            cargas.append(Mock(args=(url,), kwargs=kw))
            return pendientes.pop(0)
        with patch('requests.post', side_effect=falso):
            resumen = conversiones.enviar_pendientes(ahora)
        return resumen, Mock(call_args=cargas[-1] if cargas else None, call_args_list=cargas,
                             assert_not_called=lambda: self.assertEqual(cargas, []))

    def test_gclid_se_guarda_y_la_fuente_pasa_a_google(self):
        lead, _ = self.lead_web()
        self.assertEqual((lead.gclid, lead.fuente), (CLIC, 'GOOGLE_ANUNCIO'))
        self.assertTrue(lead.eventos.filter(tipo='FUENTE').exists())
        ev = EventoConversion.objects.get(plataforma='GOOGLE', nombre_evento='Lead')
        self.assertEqual(ev.estado, 'PENDIENTE')

    def test_gclid_invalido_se_ignora(self):
        cli = self.cliente()
        cot = self.cotizacion(cli, 'COTIZADA')
        conversiones_google.registrar_clic_web(cotizacion=cot, gclid='<script>')
        self.assertEqual(Lead.objects.get().gclid, '')
        self.assertFalse(EventoConversion.objects.filter(plataforma='GOOGLE').exists())

    def test_la_vista_del_cotizador_entrega_el_gclid(self):
        cli = self.cliente()
        cot = self.cotizacion(cli, 'COTIZADA')
        from django.test import RequestFactory
        views_cotizador._registrar_atribucion_web(RequestFactory().post('/cotizar/enviar/'), {'gclid': CLIC}, cot)
        self.assertEqual(Lead.objects.get().gclid, CLIC)

    def test_no_pisa_una_fuente_mejor_ni_el_clic_ya_guardado(self):
        lead = self.lead_con_anuncio()
        cot = self.cotizacion(lead.cliente, 'COTIZADA')
        conversiones_google.registrar_clic_web(cotizacion=cot, gclid=CLIC)
        lead.refresh_from_db()
        self.assertEqual(lead.fuente, 'META_ANUNCIO')
        conversiones_google.registrar_clic_web(cotizacion=cot, gclid='Otro_clic_distinto_999')
        lead.refresh_from_db()
        self.assertEqual(lead.gclid, CLIC)

    def test_envia_lead_con_clic_orderid_y_sin_datos_personales(self):
        self.lead_web()
        resumen, post = self.enviar_google()
        self.assertEqual(resumen['enviados'], 1)
        url = post.call_args.args[0]
        self.assertEqual(url, 'https://googleads.googleapis.com/v22/customers/1234567890:uploadClickConversions')
        cuerpo = json.loads(post.call_args.kwargs['data'].decode())
        conv = cuerpo['conversions'][0]
        self.assertTrue(cuerpo['partialFailure'])
        self.assertEqual(conv['gclid'], CLIC)
        self.assertEqual(conv['conversionAction'], 'customers/1234567890/conversionActions/555')
        self.assertEqual(conv['orderId'], str(EventoConversion.objects.get(plataforma='GOOGLE').event_id))
        self.assertNotIn('conversionValue', conv)
        self.assertNotIn('ana@example.com', post.call_args.kwargs['data'].decode())
        self.assertEqual(EventoConversion.objects.get(plataforma='GOOGLE').estado, 'ENVIADO')

    def test_purchase_con_valor_exacto_sin_iva(self):
        lead, cot = self.lead_web()
        from marketing.services import pipeline
        with patch('marketing.services.kpis.cobrado_sin_iva', return_value=Decimal('1000.10')):
            self.estado(cot, 'CONFIRMADA')
            with self.captureOnCommitCallbacks(execute=True):
                pipeline.sincronizar(lead)
        ev = EventoConversion.objects.get(plataforma='GOOGLE', nombre_evento='Purchase')
        self.assertEqual((ev.valor, ev.moneda), (Decimal('1000.10'), 'MXN'))
        _, post = self.enviar_google()
        texto = post.call_args_list[-1].kwargs['data'].decode()
        self.assertIn('"conversionValue": 1000.10', texto)
        self.assertIn('"currencyCode": "MXN"', texto)
        self.assertEqual(json.loads(texto)['conversions'][0]['conversionAction'].rsplit('/', 1)[1], '777')

    def test_sin_consentimiento_se_omite_y_no_sale_nada(self):
        self.lead_web(consentimiento=False)
        resumen, post = self.enviar_google()
        post.assert_not_called()
        self.assertEqual(resumen['omitidos'], 1)
        self.assertEqual(EventoConversion.objects.get(plataforma='GOOGLE').estado, 'OMITIDO_SIN_CONSENTIMIENTO')

    def test_con_baja_se_omite(self):
        from comunicacion.models import BajaWhatsApp
        lead, _ = self.lead_web()
        BajaWhatsApp.objects.create(telefono=lead.telefono)
        _, post = self.enviar_google()
        post.assert_not_called()
        self.assertEqual(EventoConversion.objects.get(plataforma='GOOGLE').estado, 'OMITIDO_SIN_CONSENTIMIENTO')

    def test_error_transitorio_reintenta_con_espera_y_el_mismo_orderid(self):
        self.lead_web()
        ahora = timezone.now() + timedelta(minutes=1)
        temporal = respuesta(200, {'partialFailureError': {'code': 3, 'details': [
            {'errors': [{'errorCode': {'conversionUploadError': 'TOO_RECENT_CONVERSION_ACTION'}}]}]}})
        resumen, _ = self.enviar_google(ahora, [temporal])
        ev = EventoConversion.objects.get(plataforma='GOOGLE')
        self.assertEqual((resumen['reintentos'], ev.estado, ev.intentos), (1, 'PENDIENTE', 1))
        self.assertEqual(ev.proximo_intento, ahora + timedelta(minutes=15))
        orden = str(ev.event_id)
        _, post = self.enviar_google(ahora + timedelta(minutes=16))
        self.assertEqual(json.loads(post.call_args.kwargs['data'].decode())['conversions'][0]['orderId'], orden)

    def test_rechazo_definitivo_y_duplicado(self):
        self.lead_web()
        definitivo = respuesta(200, {'partialFailureError': {'details': [
            {'errors': [{'errorCode': {'conversionUploadError': 'UNPARSEABLE_GCLID'}}]}]}})
        self.enviar_google(None, [definitivo])
        ev = EventoConversion.objects.get(plataforma='GOOGLE')
        self.assertEqual(ev.estado, 'FALLIDO')
        self.assertIn('UNPARSEABLE_GCLID', ev.ultimo_error)
        self.assertNotIn(CLIC, ev.ultimo_error)
        # Reintento manual que Google dice que ya recibió: éxito idempotente.
        conversiones.reintentar(EventoConversion.objects.filter(pk=ev.pk))
        duplicado = respuesta(200, {'partialFailureError': {'details': [
            {'errors': [{'errorCode': {'conversionUploadError': 'DUPLICATE_ORDER_ID'}}]}]}})
        self.enviar_google(None, [duplicado])
        ev.refresh_from_db()
        self.assertEqual(ev.estado, 'ENVIADO')

    def test_sin_accion_de_conversion_no_se_encola(self):
        with override_settings(GOOGLE_ADS_CONVERSION_LEAD=''):
            self.lead_web()
        self.assertFalse(EventoConversion.objects.filter(plataforma='GOOGLE', nombre_evento='Lead').exists())

    @override_settings(GOOGLE_ADS_REFRESH_TOKEN='', META_CAPI_TOKEN='')
    def test_apagado_sin_credencial_ni_encola_ni_envia(self):
        self.lead_web()
        self.assertFalse(EventoConversion.objects.filter(plataforma='GOOGLE').exists())
        resumen, post = self.enviar_google()
        post.assert_not_called()
        self.assertTrue(resumen['apagado'])

    def test_la_ventana_de_google_es_de_90_dias(self):
        self.lead_web()
        EventoConversion.objects.update(event_time=timezone.now() - timedelta(days=30))
        resumen, _ = self.enviar_google()
        self.assertEqual(resumen['enviados'], 1)  # a Meta se le habría vencido
        EventoConversion.objects.create(lead=Lead.objects.get(), plataforma='GOOGLE', nombre_evento='Purchase',
                                        origen='WEB', valor=Decimal('1.00'),
                                        event_time=timezone.now() - timedelta(days=91))

    def test_anonimizar_borra_el_clic(self):
        lead, _ = self.lead_web()
        Lead.objects.update(etapa='PERDIDO', fecha_ultima_interaccion=timezone.now() - timedelta(days=400))
        retencion.anonimizar_vencidos()
        lead.refresh_from_db()
        self.assertEqual((lead.gclid, lead.gbraid, lead.wbraid), ('', '', ''))
        resumen, post = self.enviar_google()
        post.assert_not_called()

    def test_meta_y_google_conviven_en_la_bandeja_del_mismo_prospecto(self):
        lead = self.lead_con_anuncio()
        cot = self.cotizacion(lead.cliente, 'COTIZADA')
        conversiones_google.registrar_clic_web(cotizacion=cot, gclid=CLIC)
        self.assertEqual(set(EventoConversion.objects.filter(nombre_evento='Lead').values_list('plataforma', flat=True)),
                         {'META', 'GOOGLE'})


class CapturaGclidTest(TestCase):
    def test_etiqueta_y_paginas(self):
        html = Template('{% load pixel_meta %}{% captura_google %}').render(Context({}))
        for texto in ('gclid', 'gbraid', 'wbraid', 'localStorage', 'qktGads'):
            self.assertIn(texto, html)
        for ruta in ('/', '/cotizar/'):
            self.assertContains(self.client.get(ruta, secure=True), 'window.qktGads', msg_prefix=ruta)
        self.assertContains(self.client.get('/cotizar/', secure=True), 'payload.gclid')


class TablerosGoogleTest(TestCase):
    def test_por_plataforma_en_kpis(self):
        from marketing.services import kpis
        d = timezone.localdate()
        gasto.sincronizar(d, d, proveedor=FalsoGoogle([gg(d, '300.00')]))
        r = kpis.calcular(d, d)
        google = [g for g in r['por_plataforma'] if g['plataforma'] == 'GOOGLE'][0]
        self.assertEqual((google['nombre'], google['gasto']), ('Google', Decimal('300.00')))
        self.assertEqual([c['plataforma'] for c in r['por_campana']], ['Google'])
