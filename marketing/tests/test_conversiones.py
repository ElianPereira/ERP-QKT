"""Conversions API: bandeja de salida, consentimiento, reintentos, hash y Pixel web (Issue #468, fase 6)."""
import json
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from django.core.exceptions import ValidationError
from django.template import Context, Template
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

import legal
from comercial import views_cotizador
from comercial.models import Cliente, Cotizacion, Pago
from comunicacion.models import BajaWhatsApp, ConversacionWhatsApp, Pendiente
from core_erp import impuestos
from core_erp.middleware import PUBLIC_CSP
from legal.management.commands.seed_documentos_legales import DOCUMENTOS
from legal.models import MARCADORES_SIN_RESOLVER, AceptacionLegal, DocumentoLegal, TipoDocumento
from marketing.models import EventoConversion, Lead
from marketing.services import barrido, conversiones, retencion
from marketing.tests.test_pipeline import Base, hoy_mas

CAPI = dict(META_CAPI_TOKEN='token-de-prueba', META_PIXEL_ID='111222333', META_WA_WABA_ID='999888',
            META_CAPI_DATASET_ID='', META_CAPI_TEST_CODE='')
TEL = '525555550101'


def respuesta(status=200, cuerpo=None):
    r = Mock()
    r.status_code = status
    r.json.return_value = cuerpo if cuerpo is not None else {}
    return r


OK = respuesta(200, {'events_received': 1})


@override_settings(**CAPI)
class ConversionesBase(Base):
    def acepta_marketing(self, cliente, cuando=None):
        AceptacionLegal.objects.create(cliente=cliente, correo='ana@example.com', origen='FORM_COTIZACION',
                                       finalidades_aceptadas=['MARKETING'], aceptado_en=cuando or timezone.now())

    def lead_con_anuncio(self, consentimiento=True):
        """Prospecto con clic de anuncio de WhatsApp y su cliente."""
        self.mensaje(referral={'source_id': 'AD1', 'headline': 'Bodas', 'ctwa_clid': 'CLID-1'})
        lead = Lead.objects.get()
        cli = self.cliente(email='Ana@Example.com ')
        if consentimiento:
            self.acepta_marketing(cli)
        Lead.objects.filter(pk=lead.pk).update(cliente=cli)
        return Lead.objects.get(pk=lead.pk)

    def evento(self, nombre):
        return EventoConversion.objects.get(nombre_evento=nombre)

    def enviar(self, ahora=None, **kw):
        with patch('marketing.services.conversiones.requests.post', **kw) as post:
            resumen = conversiones.enviar_pendientes(ahora)
        return resumen, post

    def cuerpo(self, post, i=0):
        return json.loads(post.call_args_list[i].kwargs['data'].decode())


class EncoladoTest(ConversionesBase):
    def test_anuncio_de_whatsapp_encola_el_lead_sin_pii(self):
        self.mensaje(referral={'source_id': 'AD1', 'ctwa_clid': 'CLID-1'})
        ev = self.evento('Lead')
        self.assertEqual((ev.origen, ev.ctwa_clid, ev.estado), ('WHATSAPP', 'CLID-1', 'PENDIENTE'))
        self.assertEqual(ev.valor, None)
        self.assertEqual(ev.lead.telefono, TEL)
        for campo in ('fbp', 'fbc', 'ultimo_error'):
            self.assertNotIn(TEL, getattr(ev, campo))

    def test_sin_atribucion_no_se_encola_nada(self):
        self.mensaje()
        cli = self.cliente()
        self.cotizacion(cli, 'COTIZADA')
        self.assertEqual(EventoConversion.objects.count(), 0)

    @override_settings(META_CAPI_TOKEN='')
    def test_sin_credencial_no_encola_ni_envia(self):
        self.mensaje(referral={'source_id': 'AD1', 'ctwa_clid': 'CLID-1'})
        self.assertEqual(EventoConversion.objects.count(), 0)
        resumen, post = self.enviar()
        self.assertTrue(resumen['apagado'])
        post.assert_not_called()

    @override_settings(META_WA_WABA_ID='')
    def test_whatsapp_sin_waba_no_encola(self):
        self.mensaje(referral={'source_id': 'AD1', 'ctwa_clid': 'CLID-1'})
        self.assertEqual(EventoConversion.objects.count(), 0)

    def test_cotizacion_y_visita_encolan_una_vez(self):
        lead = self.lead_con_anuncio()
        cot = self.cotizacion(Lead.objects.get().cliente, 'COTIZADA')
        self.assertEqual(self.evento('CotizacionEnviada').estado, 'PENDIENTE')
        self.cotizacion(lead.cliente, 'BORRADOR', fecha_evento=hoy_mas(90))  # otro guardado no duplica
        self.assertEqual(EventoConversion.objects.filter(nombre_evento='CotizacionEnviada').count(), 1)
        self.assertTrue(cot.pk)

    def test_purchase_vale_lo_cobrado_sin_iva(self):
        lead = self.lead_con_anuncio()
        cot = self.cotizacion(lead.cliente, 'CONFIRMADA')
        base = Decimal('1000.00')
        Cotizacion.objects.filter(pk=cot.pk).update(subtotal=base, iva=impuestos.iva_de(base),
                                                    precio_final=base + impuestos.iva_de(base))
        Pago.objects.bulk_create([Pago(cotizacion=cot, monto=Decimal('580.00'), metodo='TRANSFERENCIA',
                                       tipo='INGRESO')])
        with self.captureOnCommitCallbacks(execute=True):
            conversiones.encolar_etapa(Lead.objects.get(), 'GANADO')
        ev = self.evento('Purchase')
        self.assertEqual((ev.valor, ev.moneda), (Decimal('500.00'), 'MXN'))  # 580 con IVA -> 500.00 de base

    def test_purchase_sin_cobro_no_se_encola(self):
        lead = self.lead_con_anuncio()
        self.cotizacion(lead.cliente, 'CONFIRMADA')
        with self.captureOnCommitCallbacks(execute=True):
            conversiones.encolar_etapa(Lead.objects.get(), 'GANADO')
        self.assertFalse(EventoConversion.objects.filter(nombre_evento='Purchase').exists())

    def test_reconstruir_historico_pausa_el_encolado(self):
        vistos = []
        with patch('marketing.services.pipeline.registrar_contacto',
                   side_effect=lambda **kw: vistos.append(conversiones._pausado.get())), \
                patch('marketing.services.barrido._contactos_recientes', return_value=[{'telefono': TEL}]):
            barrido.barrer(desde=timezone.now() - timedelta(days=30))
            barrido.barrer()
        self.assertEqual(vistos, [True, False])
        self.assertFalse(conversiones._pausado.get())

    def test_un_error_al_encolar_no_rompe_el_guardado(self):
        with patch('marketing.services.conversiones._crear', side_effect=RuntimeError('boom')):
            self.mensaje(referral={'source_id': 'AD1', 'ctwa_clid': 'CLID-1'})
        self.assertEqual(Lead.objects.count(), 1)


class EnvioTest(ConversionesBase):
    def test_whatsapp_manda_clic_y_waba_con_hash_y_token_en_el_cuerpo(self):
        lead = self.lead_con_anuncio()
        resumen, post = self.enviar(return_value=OK)
        self.assertEqual(resumen['enviados'], 1)
        url = post.call_args.args[0]
        self.assertTrue(url.endswith('/111222333/events'))
        self.assertNotIn('token-de-prueba', url)
        cuerpo = self.cuerpo(post)
        self.assertEqual(cuerpo['access_token'], 'token-de-prueba')
        e = cuerpo['data'][0]
        self.assertEqual((e['event_name'], e['action_source'], e['messaging_channel']),
                         ('Lead', 'business_messaging', 'whatsapp'))
        self.assertEqual(e['user_data'], {'whatsapp_business_account_id': '999888', 'ctwa_clid': 'CLID-1'})
        ev = self.evento('Lead')
        self.assertEqual((ev.estado, ev.intentos), ('ENVIADO', 1))
        self.assertIsNotNone(ev.enviado_en)
        self.assertEqual(e['event_id'], str(ev.event_id))
        self.assertEqual(lead.telefono, TEL)

    def test_purchase_va_con_valor_decimal_exacto_y_moneda(self):
        lead = self.lead_con_anuncio()
        cot = self.cotizacion(lead.cliente, 'CONFIRMADA')
        base = Decimal('1000.10')
        Cotizacion.objects.filter(pk=cot.pk).update(subtotal=base, iva=impuestos.iva_de(base),
                                                    precio_final=base + impuestos.iva_de(base))
        Pago.objects.bulk_create([Pago(cotizacion=cot, monto=base + impuestos.iva_de(base),
                                       metodo='TRANSFERENCIA', tipo='INGRESO')])
        Lead.objects.filter(pk=lead.pk).update(etapa='GANADO', cotizacion=cot)
        with self.captureOnCommitCallbacks(execute=True):
            conversiones.encolar_etapa(Lead.objects.get(), 'GANADO')
        EventoConversion.objects.filter(nombre_evento='Lead').delete()
        _, post = self.enviar(return_value=OK)
        crudo = post.call_args.kwargs['data'].decode()
        self.assertIn('"value": 1000.10', crudo)
        self.assertEqual(json.loads(crudo)['data'][0]['custom_data'], {'currency': 'MXN', 'value': 1000.10})

    def test_web_manda_telefono_y_correo_hasheados_y_normalizados(self):
        lead = self.lead_con_anuncio()
        ev = self.evento('Lead')
        EventoConversion.objects.filter(pk=ev.pk).update(
            origen='WEB', ctwa_clid='', fbp='fb.1.1700000000.123', fbc='fb.1.1700000000.AbC',
            contexto_web={'client_user_agent': 'UA/1', 'client_ip_address': '203.0.113.9',
                          'event_source_url': 'https://quintakooxtanil.com/cotizar/'})
        _, post = self.enviar(return_value=OK)
        e = self.cuerpo(post)['data'][0]
        self.assertEqual(e['action_source'], 'website')
        u = e['user_data']
        self.assertEqual(u['ph'], [sha256(b'525555550101').hexdigest()])
        self.assertEqual(u['em'], [sha256(b'ana@example.com').hexdigest()])
        self.assertEqual((u['fbp'], u['fbc'], u['client_user_agent']), ('fb.1.1700000000.123', 'fb.1.1700000000.AbC', 'UA/1'))
        self.assertNotIn(TEL, json.dumps(e))
        self.assertNotIn('ana@example.com', json.dumps(e).lower())
        ev.refresh_from_db()
        self.assertEqual(ev.contexto_web, {})  # IP y navegador se borran al terminar
        self.assertEqual(lead.pk, ev.lead_id)

    def test_hash_normaliza_telefono_y_correo(self):
        esperado = sha256(b'525555550101').hexdigest()
        for t in ('5555550101', '+52 (555) 555-0101', '5215555550101', '52 55 5555 0101'):
            self.assertEqual(conversiones.hash_telefono(t), esperado, t)
        self.assertEqual(conversiones.hash_telefono('123'), '')
        self.assertEqual(conversiones.hash_correo('  Ana@Example.COM '), sha256(b'ana@example.com').hexdigest())
        self.assertEqual(conversiones.hash_correo('sin-arroba'), '')

    def test_codigo_de_prueba_se_incluye_solo_si_esta_definido(self):
        self.lead_con_anuncio()
        with override_settings(META_CAPI_TEST_CODE='TEST123'):
            _, post = self.enviar(return_value=OK)
        self.assertEqual(self.cuerpo(post)['test_event_code'], 'TEST123')


class ConsentimientoTest(ConversionesBase):
    def test_sin_marketing_se_omite_y_no_sale_a_la_red(self):
        self.lead_con_anuncio(consentimiento=False)
        resumen, post = self.enviar(return_value=OK)
        post.assert_not_called()
        self.assertEqual(resumen['omitidos'], 1)
        self.assertEqual(self.evento('Lead').estado, 'OMITIDO_SIN_CONSENTIMIENTO')

    def test_con_baja_se_omite(self):
        lead = self.lead_con_anuncio()
        BajaWhatsApp.objects.create(telefono=TEL, origen='TEXTO')
        _, post = self.enviar(return_value=OK)
        post.assert_not_called()
        self.assertEqual(self.evento('Lead').estado, 'OMITIDO_SIN_CONSENTIMIENTO')
        self.assertTrue(lead.pk)

    def test_aceptar_despues_de_la_baja_vuelve_a_permitir(self):
        lead = self.lead_con_anuncio()
        BajaWhatsApp.objects.create(telefono=TEL, origen='TEXTO')
        self.acepta_marketing(lead.cliente, timezone.now() + timedelta(seconds=5))
        resumen, _ = self.enviar(return_value=OK)
        self.assertEqual(resumen['enviados'], 1)

    def test_el_boton_de_promociones_de_la_conversacion_basta(self):
        self.mensaje(referral={'source_id': 'AD1', 'ctwa_clid': 'CLID-1'})
        ConversacionWhatsApp.objects.update(consentimiento_marketing=True)
        resumen, _ = self.enviar(return_value=OK)
        self.assertEqual(resumen['enviados'], 1)

    def test_prospecto_anonimizado_no_envia(self):
        lead = self.lead_con_anuncio()
        Lead.objects.filter(pk=lead.pk).update(anonimizado_en=timezone.now())
        resumen, post = self.enviar(return_value=OK)
        post.assert_not_called()
        self.assertEqual(resumen['omitidos'], 1)


class ReintentosTest(ConversionesBase):
    def setUp(self):
        self.lead_con_anuncio()
        self.t0 = timezone.now()

    def test_error_transitorio_reintenta_con_backoff_exponencial(self):
        esperas = []
        ahora = self.t0
        for n in range(1, 4):
            _, post = self.enviar(ahora, return_value=respuesta(500, {}))
            ev = self.evento('Lead')
            self.assertEqual((ev.estado, ev.intentos), ('PENDIENTE', n))
            esperas.append(ev.proximo_intento - ahora)
            self.assertIn('HTTP 500', ev.ultimo_error)
            ahora = ev.proximo_intento
        self.assertEqual(esperas, [timedelta(minutes=15), timedelta(minutes=30), timedelta(minutes=60)])

    def test_no_reenvia_antes_de_tiempo(self):
        self.enviar(self.t0, return_value=respuesta(500, {}))
        _, post = self.enviar(self.t0 + timedelta(minutes=5), return_value=OK)
        post.assert_not_called()

    def test_agotados_los_intentos_queda_fallido_y_avisa_a_direccion(self):
        ahora = self.t0
        for _ in range(conversiones.MAX_INTENTOS):
            self.enviar(ahora, side_effect=requests.Timeout('lento'))
            ahora = self.evento('Lead').proximo_intento
        ev = self.evento('Lead')
        self.assertEqual((ev.estado, ev.intentos), ('FALLIDO', conversiones.MAX_INTENTOS))
        self.assertIn('Timeout', ev.ultimo_error)
        self.assertNotIn('token-de-prueba', ev.ultimo_error)

    def test_rechazo_definitivo_no_reintenta(self):
        r = respuesta(400, {'error': {'code': 190, 'error_subcode': 463, 'type': 'OAuthException',
                                      'message': 'token-de-prueba caducado'}})
        self.enviar(self.t0, return_value=r)
        ev = self.evento('Lead')
        self.assertEqual((ev.estado, ev.intentos), ('FALLIDO', 1))
        self.assertIn('190', ev.ultimo_error)
        self.assertNotIn('token-de-prueba', ev.ultimo_error)

    def test_evento_de_mas_de_siete_dias_falla_sin_enviar(self):
        EventoConversion.objects.update(event_time=self.t0 - timedelta(days=8))
        _, post = self.enviar(self.t0, return_value=OK)
        post.assert_not_called()
        self.assertEqual(self.evento('Lead').estado, 'FALLIDO')

    def test_tres_fallidos_abren_un_solo_pendiente(self):
        lead = Lead.objects.get()
        for i, nombre in enumerate(('CotizacionEnviada', 'Schedule', 'Purchase')):
            EventoConversion.objects.create(lead=lead, nombre_evento=nombre, origen='WHATSAPP', ctwa_clid='C')
        EventoConversion.objects.update(event_time=self.t0 - timedelta(days=8))
        self.enviar(self.t0, return_value=OK)
        self.enviar(self.t0 + timedelta(hours=1), return_value=OK)
        self.assertEqual(Pendiente.objects.filter(clave_idempotencia__startswith='marketing_capi_fallidos').count(), 1)

    def test_reintentar_devuelve_los_fallidos_a_la_cola(self):
        self.enviar(self.t0, return_value=respuesta(400, {'error': {'code': 100}}))
        self.assertEqual(conversiones.reintentar(EventoConversion.objects.all()), 1)
        ev = self.evento('Lead')
        self.assertEqual((ev.estado, ev.intentos), ('PENDIENTE', 0))

    def test_reclamo_evita_el_doble_envio(self):
        ev = self.evento('Lead')
        EventoConversion.objects.filter(pk=ev.pk).update(proximo_intento=self.t0 + timedelta(minutes=10))
        _, post = self.enviar(self.t0, return_value=OK)
        post.assert_not_called()

    def test_purchase_se_cancela_si_la_venta_se_cae(self):
        lead = Lead.objects.get()
        EventoConversion.objects.create(lead=lead, nombre_evento='Purchase', origen='WHATSAPP', ctwa_clid='C',
                                        valor=Decimal('100.00'))
        _, post = self.enviar(self.t0 + timedelta(minutes=1), return_value=OK)
        self.assertEqual(self.evento('Purchase').estado, 'CANCELADO')
        self.assertEqual(post.call_count, 1)  # solo el Lead


class DedupeWebTest(ConversionesBase):
    def peticion(self, **extra):
        return RequestFactory().post('/cotizar/enviar/', HTTP_USER_AGENT='UA/1', REMOTE_ADDR='203.0.113.9', **extra)

    def test_el_event_id_del_navegador_es_el_del_servidor(self):
        cli = self.cliente(email='ana@example.com')
        self.acepta_marketing(cli)
        cot = self.cotizacion(cli, 'COTIZADA')
        event_id = uuid.uuid4()
        data = {'meta_event_id': str(event_id), 'fbp': 'fb.1.1700000000.123', 'fbc': 'basura'}
        views_cotizador._registrar_atribucion_web(self.peticion(), data, cot)
        ev = self.evento('Lead')
        self.assertEqual(ev.event_id, event_id)
        self.assertEqual((ev.origen, ev.fbp, ev.fbc), ('WEB', 'fb.1.1700000000.123', ''))  # fbc inválido se descarta
        self.assertEqual(ev.contexto_web['client_user_agent'], 'UA/1')
        # El de cotización enviada nace con la misma atribución web.
        self.assertEqual(self.evento('CotizacionEnviada').origen, 'WEB')
        _, post = self.enviar(return_value=OK)
        ids = {e['event_id'] for i in range(post.call_count) for e in self.cuerpo(post, i)['data']}
        self.assertIn(str(event_id), ids)

    def test_reenviar_el_mismo_event_id_no_duplica(self):
        cli = self.cliente()
        cot = self.cotizacion(cli, 'COTIZADA')
        data = {'meta_event_id': str(uuid.uuid4())}
        views_cotizador._registrar_atribucion_web(self.peticion(), data, cot)
        views_cotizador._registrar_atribucion_web(self.peticion(), data, cot)
        self.assertEqual(EventoConversion.objects.filter(nombre_evento='Lead').count(), 1)

    def test_event_id_invalido_se_ignora_sin_romper(self):
        cli = self.cliente()
        cot = self.cotizacion(cli, 'COTIZADA')
        views_cotizador._registrar_atribucion_web(self.peticion(), {'meta_event_id': 'no-es-uuid'}, cot)
        self.assertEqual(EventoConversion.objects.count(), 0)

    def test_si_ya_habia_lead_pendiente_de_whatsapp_se_reemplaza_por_el_web(self):
        lead = self.lead_con_anuncio()
        cot = self.cotizacion(lead.cliente, 'COTIZADA')
        event_id = uuid.uuid4()
        views_cotizador._registrar_atribucion_web(self.peticion(), {'meta_event_id': str(event_id)}, cot)
        ev = self.evento('Lead')
        self.assertEqual((ev.event_id, ev.origen, ev.ctwa_clid), (event_id, 'WEB', ''))


class RetencionTest(ConversionesBase):
    def test_anonimizar_borra_los_identificadores_de_la_bandeja(self):
        self.lead_con_anuncio()
        Lead.objects.update(etapa='PERDIDO', fecha_ultima_interaccion=timezone.now() - timedelta(days=400))
        retencion.anonimizar_vencidos()
        ev = self.evento('Lead')
        self.assertEqual((ev.ctwa_clid, ev.fbp, ev.fbc, ev.contexto_web), ('', '', '', {}))


class PixelWebTest(TestCase):
    def render(self):
        return Template('{% load pixel_meta %}{% pixel_meta %}').render(Context({})).strip()

    @override_settings(META_PIXEL_ID='')
    def test_sin_pixel_no_renderiza_nada(self):
        self.assertEqual(self.render(), '')

    @override_settings(META_PIXEL_ID='abc"<script>')
    def test_un_id_no_numerico_no_renderiza_nada(self):
        self.assertEqual(self.render(), '')

    @override_settings(META_PIXEL_ID='111222333')
    def test_el_pixel_no_se_carga_sin_aceptar(self):
        html = self.render()
        self.assertIn('id="qkt-cookies"', html)
        self.assertIn('Aceptar', html)
        self.assertIn('Rechazar', html)
        self.assertIn('localStorage', html)
        # La URL del script solo aparece dentro de cargarPixel(), que se llama únicamente tras aceptar.
        self.assertEqual(html.count('connect.facebook.net'), 1)
        fuera_de_la_funcion = re.sub(r'function cargarPixel\(\) \{.*?\n  \}\n', '', html, flags=re.S)
        self.assertNotIn('connect.facebook.net', fuera_de_la_funcion)
        self.assertNotIn('<script async src', html)
        llamadas = re.findall(r'cargarPixel\(\)', fuera_de_la_funcion)
        self.assertEqual(len(llamadas), 2)  # al aceptar y al recordar una aceptación previa
        self.assertIn("if (decision === 'si') { cargarPixel();", html)

    @override_settings(META_PIXEL_ID='111222333')
    def test_landing_y_cotizador_incluyen_el_banner(self):
        for ruta in ('/', '/cotizar/'):
            r = self.client.get(ruta, secure=True)
            self.assertContains(r, 'id="qkt-cookies"', msg_prefix=ruta)

    @override_settings(META_PIXEL_ID='')
    def test_landing_y_cotizador_sin_pixel_no_traen_banner(self):
        for ruta in ('/', '/cotizar/'):
            r = self.client.get(ruta, secure=True)
            self.assertNotContains(r, 'qkt-cookies', msg_prefix=ruta)

    def test_el_cotizador_manda_el_event_id_y_dispara_el_lead(self):
        r = self.client.get('/cotizar/', secure=True)
        self.assertContains(r, 'payload.meta_event_id')
        self.assertContains(r, 'window.qktMeta.lead(')


class CspTest(TestCase):
    def csp(self, ruta):
        return self.client.get(ruta, secure=True).headers['Content-Security-Policy']

    @override_settings(META_PIXEL_ID='111222333')
    def test_landing_y_cotizador_permiten_solo_los_origenes_de_meta(self):
        for ruta in ('/', '/cotizar/'):
            csp = self.csp(ruta)
            self.assertIn("script-src 'self' 'unsafe-inline' https://connect.facebook.net", csp)
            self.assertIn('connect-src \'self\' https://www.facebook.com https://connect.facebook.net', csp)
            self.assertIn('img-src \'self\' data: https://www.facebook.com', csp)
            # Sigue bloqueando lo demás.
            self.assertIn("default-src 'self'", csp)
            self.assertIn("object-src 'none'", csp)
            self.assertIn("frame-ancestors 'self'", csp)
            self.assertNotIn('*', csp.replace("'self'", ''))
            self.assertNotIn('https://www.google-analytics.com', csp)

    @override_settings(META_PIXEL_ID='111222333')
    def test_las_apis_publicas_no_cambian(self):
        self.assertEqual(self.csp('/api/fechas-ocupadas/'), PUBLIC_CSP)

    @override_settings(META_PIXEL_ID='')
    def test_sin_pixel_la_politica_es_la_de_siempre(self):
        for ruta in ('/', '/cotizar/'):
            self.assertEqual(self.csp(ruta), PUBLIC_CSP)
        self.assertNotIn('facebook', PUBLIC_CSP)


class AvisoBorradorTest(TestCase):
    """El Aviso v2.7 es un borrador para el abogado: no se carga ni se puede publicar."""
    RUTA = Path(legal.__file__).parent / 'documentos_iniciales' / 'aviso_privacidad_v2.7.md'

    def test_describe_pixel_remarketing_y_retencion_con_marcadores(self):
        texto = self.RUTA.read_text(encoding='utf-8')
        for frase in ('Pixel de Meta', 'remarketing', 'solo se activan si usted las acepta', 'hash', '1 año',
                      'Versión 2.7'):
            self.assertIn(frase, texto, frase)
        self.assertGreaterEqual(len(MARCADORES_SIN_RESOLVER.findall(texto)), 5)
        self.assertIn('[CONFIRMAR: fecha de vigencia', texto)

    def test_el_candado_de_confirmar_impide_publicarlo(self):
        contenido = self.RUTA.read_text(encoding='utf-8')
        with self.assertRaises(ValidationError):
            DocumentoLegal.objects.create(tipo=TipoDocumento.AVISO_PRIVACIDAD, version='2.7', titulo='Aviso',
                                          contenido_md=contenido, vigente_desde=date.today(), vigente=True)
        doc = DocumentoLegal.objects.create(tipo=TipoDocumento.AVISO_PRIVACIDAD, version='2.7', titulo='Aviso',
                                            contenido_md=contenido, vigente_desde=date.today(), vigente=False)
        self.assertTrue(doc.marcadores_pendientes())

    def test_el_seed_sigue_apuntando_a_la_version_vigente(self):
        self.assertNotIn('aviso_privacidad_v2.7.md', DOCUMENTOS)
        self.assertIn('aviso_privacidad_v2.6.md', DOCUMENTOS)
