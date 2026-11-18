"""Consentimiento de privacidad automático (Issue #506)."""
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.core.management import call_command
from django.test import TestCase, override_settings

from comercial import services_consentimiento as svc
from comercial.models import Cliente, ContratoServicio, Cotizacion, Pago, PortalCliente
from comunicacion.models import ComunicacionCliente, Pendiente
from legal.models import AceptacionLegal, DocumentoLegal, Finalidad, OrigenAceptacion, TipoDocumento
from legal.services import LegalService

TELEFONO = '9991234567'


def _publicar(version='1.0'):
    for tipo in (TipoDocumento.AVISO_PRIVACIDAD, TipoDocumento.TERMINOS, TipoDocumento.POLITICA_CANCELACION):
        DocumentoLegal.objects.create(tipo=tipo, version=version, titulo=f'Doc {tipo}',
                                      contenido_md=f'{tipo} {version}', vigente_desde=date.today(), vigente=True)


def _cotizacion(cliente, estado='CONFIRMADA', **kw):
    return Cotizacion.objects.create(
        cliente=cliente, nombre_evento='Boda', tipo_servicio='EVENTO', estado=estado,
        fecha_evento=date.today() + timedelta(days=60), num_personas=80, **kw)


class Base(TestCase):
    def setUp(self):
        Finalidad.objects.create(clave='MARKETING', nombre='Promociones', requiere_consentimiento=True)
        Finalidad.objects.create(clave='USO_IMAGEN', nombre='Uso de imagen', requiere_consentimiento=True)
        _publicar('1.0')
        self.cliente = Cliente.objects.create(nombre='Ana Ruiz', email='ana@example.com', telefono=TELEFONO)

    def aceptar(self, cliente=None, origen=OrigenAceptacion.FORM_COTIZACION):
        return LegalService.registrar_aceptacion(
            request=None, correo='ana@example.com', origen=origen, cliente=cliente or self.cliente)


class AceptacionVigenteTest(Base):
    def test_sin_aceptacion_es_falso_y_con_ella_verdadero(self):
        self.assertFalse(svc.tiene_aceptacion_vigente(self.cliente))
        self.aceptar()
        self.assertTrue(svc.tiene_aceptacion_vigente(self.cliente))

    def test_una_version_nueva_del_aviso_la_vuelve_a_pedir_una_vez(self):
        self.aceptar()
        _publicar('2.0')
        self.assertFalse(svc.tiene_aceptacion_vigente(self.cliente))
        self.aceptar()
        self.assertTrue(svc.tiene_aceptacion_vigente(self.cliente))

    def test_sin_aviso_publicado_no_se_exige_nada(self):
        DocumentoLegal.objects.all().update(vigente=False)
        self.assertTrue(svc.tiene_aceptacion_vigente(self.cliente))


class PortalTest(Base):
    def setUp(self):
        super().setUp()
        self.cot = _cotizacion(self.cliente, estado='COTIZADA')
        self.portal = PortalCliente.objects.get_or_create(cotizacion=self.cot)[0]
        self.url = f'/mi-evento/{self.portal.token}/'
        patcher = patch.object(ContratoServicio._meta.get_field('archivo'), 'storage', InMemoryStorage())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.url_aceptar = f'{self.url}privacidad/'

    def test_muestra_la_tarjeta_y_se_puede_ver_la_cotizacion(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="card-consentimiento"')
        self.assertContains(r, 'Aceptar y continuar')
        self.assertContains(r, 'name="acepta_legales"')
        self.assertContains(r, 'Cotización PDF')

    def test_aceptar_registra_evidencia_del_cliente_y_la_tarjeta_no_vuelve(self):
        r = self.client.post(self.url_aceptar, {'acepta_legales': 'si'}, HTTP_USER_AGENT='Mozilla/5.0 prueba',
                             REMOTE_ADDR='203.0.113.7')
        self.assertRedirects(r, self.url, fetch_redirect_response=False)
        a = AceptacionLegal.objects.get(cliente=self.cliente)
        self.assertEqual(a.origen, OrigenAceptacion.PORTAL_CLIENTE)
        self.assertEqual(a.ip, '203.0.113.7')
        self.assertIn('Mozilla', a.user_agent)
        self.assertEqual(a.finalidades_aceptadas, [])
        self.assertIn('MARKETING', a.finalidades_rechazadas)
        self.assertTrue(any(d['tipo'] == TipoDocumento.AVISO_PRIVACIDAD and d['version'] == '1.0'
                            for d in a.snapshot_documentos))
        self.assertNotContains(self.client.get(self.url), 'id="card-consentimiento"')

    def test_casilla_opcional_de_marketing(self):
        self.client.post(self.url_aceptar, {'acepta_legales': 'si', 'finalidades': ['MARKETING', 'INVENTADA']})
        self.assertEqual(AceptacionLegal.objects.get().finalidades_aceptadas, ['MARKETING'])

    def test_sin_la_casilla_obligatoria_no_registra_nada(self):
        r = self.client.post(self.url_aceptar, {'finalidades': ['MARKETING']})
        self.assertEqual(AceptacionLegal.objects.count(), 0)
        self.assertEqual(r.status_code, 302)
        self.assertContains(self.client.get(r['Location']), 'Marca la primera casilla')

    def test_aceptar_dos_veces_no_duplica(self):
        self.client.post(self.url_aceptar, {'acepta_legales': 'si'})
        self.client.post(self.url_aceptar, {'acepta_legales': 'si'})
        self.assertEqual(AceptacionLegal.objects.count(), 1)

    def test_quien_ya_acepto_en_el_cotizador_no_ve_la_tarjeta(self):
        self.aceptar()
        self.assertNotContains(self.client.get(self.url), 'id="card-consentimiento"')

    def test_firmar_y_solicitar_se_rechazan_en_el_servidor_sin_aceptacion(self):
        for ruta, datos in (('contrato/firmar/', {'accion': 'codigo'}), ('solicitud/', {'accion': 'iniciar'})):
            with self.subTest(ruta=ruta):
                if ruta.startswith('contrato'):
                    contrato = ContratoServicio(cotizacion=self.cot, numero='CONT-2026-0001', tipo_servicio='EVENTO')
                    contrato.archivo.save('contrato.pdf', ContentFile(b'%PDF-1.4'), save=False)
                    contrato.save()
                r = self.client.post(f'{self.url}{ruta}', datos)
                self.assertContains(r, 'acepta el Aviso de Privacidad')

    def test_el_enlace_del_portal_ya_no_acepta_get_en_el_endpoint(self):
        self.assertEqual(self.client.get(self.url_aceptar).status_code, 405)


class ElegiblesTest(Base):
    def test_solo_confirmadas_o_con_pago_y_nunca_historicas_ni_cerradas(self):
        otro = Cliente.objects.create(nombre='Luis', telefono='9990000001')
        historica = Cliente.objects.create(nombre='Hist', telefono='9990000002')
        cerrada = Cliente.objects.create(nombre='Cerrada', telefono='9990000003')
        borrador = Cliente.objects.create(nombre='Borrador', telefono='9990000004')
        _cotizacion(self.cliente)                                  # confirmada
        pagada = _cotizacion(otro, estado='COTIZADA')
        Cotizacion.objects.filter(pk=pagada.pk).update(precio_final=Decimal('1160.00'))
        pagada.refresh_from_db()
        Pago.objects.create(cotizacion=pagada, monto=Decimal('100.00'), metodo='EFECTIVO')
        h = _cotizacion(historica)
        Cotizacion.objects.filter(pk=h.pk).update(es_historica=True)
        _cotizacion(cerrada, estado='CERRADA')
        _cotizacion(borrador, estado='BORRADOR')
        self.assertEqual({c.nombre for c in svc.clientes_sin_aceptar()}, {'Ana Ruiz', 'Luis'})


@override_settings(META_WA_PLANTILLA_CONSENTIMIENTO='autorizacion_privacidad')
class SolicitudWhatsAppTest(Base):
    def setUp(self):
        super().setUp()
        _cotizacion(self.cliente)

    def _correr(self, ventana=False, **kw):
        from comunicacion.models import ComunicacionCliente as C
        with patch('comunicacion.services_pendientes.ventana_abierta', return_value=ventana), \
                patch('comunicacion.services.enviar_whatsapp_template') as plantilla, \
                patch('comunicacion.services.enviar_whatsapp_botones') as botones, \
                patch('comunicacion.services_agente.registrar_mensaje_automatico'):
            plantilla.return_value = botones.return_value = C(estado='ENVIADO', proveedor_id='wamid.X')
            resumen = svc.pedir_consentimientos_pendientes(**kw)
        return resumen, plantilla, botones

    def test_sin_ventana_manda_la_plantilla_con_tres_botones_una_sola_vez(self):
        r, plantilla, botones = self._correr()
        self.assertEqual((r['candidatos'], r['enviadas']), (1, 1))
        botones.assert_not_called()
        payloads = [c['parameters'][0]['payload'] for c in plantilla.call_args.kwargs['components']
                    if c['type'] == 'button']
        self.assertEqual(payloads, [f'con:{self.cliente.pk}:{o}:1.0' for o in ('ok', 'promo', 'no')])

    def test_idempotente_por_cliente_y_version(self):
        from comunicacion.services import reservar_comunicacion
        reservar_comunicacion(canal='WHATSAPP', tipo='OTRO', trigger='CRON', destinatario='x', asunto='',
                              estado='ENVIADO', clave_idempotencia=f'consentimiento:{self.cliente.pk}:1.0:whatsapp')
        r, plantilla, _ = self._correr()
        self.assertEqual(r['ya_enviadas'], 1)
        plantilla.assert_not_called()

    def test_con_ventana_abierta_usa_botones(self):
        r, plantilla, botones = self._correr(ventana=True)
        self.assertEqual(r['enviadas'], 1)
        plantilla.assert_not_called()
        self.assertEqual(len(botones.call_args.kwargs['botones']), 3)

    @override_settings(META_WA_PLANTILLA_CONSENTIMIENTO='')
    def test_sin_plantilla_ni_ventana_no_manda_y_reintenta_otro_dia(self):
        r, plantilla, botones = self._correr()
        self.assertEqual(r['sin_plantilla'], 1)
        plantilla.assert_not_called()
        botones.assert_not_called()
        self.assertFalse(ComunicacionCliente.objects.filter(clave_idempotencia__startswith='consentimiento:').exists())

    def test_quien_ya_acepto_no_recibe_nada(self):
        self.aceptar()
        r, plantilla, _ = self._correr()
        self.assertEqual(r['candidatos'], 0)
        plantilla.assert_not_called()

    def test_un_envio_fallido_libera_la_clave_para_reintentar(self):
        from comunicacion.services import reservar_comunicacion
        comm = reservar_comunicacion(canal='WHATSAPP', tipo='OTRO', trigger='CRON', destinatario='x', asunto='',
                                     estado='FALLIDO', clave_idempotencia='consentimiento:x:1.0:whatsapp')
        svc._liberar_clave_si_fallo(comm)
        comm.refresh_from_db()
        self.assertIsNone(comm.clave_idempotencia)

    def test_dry_run_no_manda_nada(self):
        r, plantilla, botones = self._correr(dry_run=True)
        self.assertEqual(r['candidatos'], 1)
        plantilla.assert_not_called()
        botones.assert_not_called()

    def test_comando(self):
        salida = StringIO()
        with patch('comunicacion.services.enviar_whatsapp_template') as plantilla, \
                patch('comunicacion.services_pendientes.ventana_abierta', return_value=False):
            call_command('pedir_consentimientos_pendientes', '--dry-run', stdout=salida)
        plantilla.assert_not_called()
        self.assertIn('1 sin aceptar', salida.getvalue())


class SinContactoTest(Base):
    def test_sin_telefono_ni_correo_abre_un_solo_pendiente_para_el_encargado(self):
        sin = Cliente.objects.create(nombre='Sin Medios')
        usuario = get_user_model().objects.create_user('vendedora', password='x')
        _cotizacion(sin, usuario=usuario)
        for _ in range(2):
            svc.pedir_consentimientos_pendientes()
        p = Pendiente.objects.get(clave_idempotencia=f'consentimiento_sin_contacto:{sin.pk}')
        self.assertEqual((p.rol, p.severidad), ('ENCARGADO', 'NORMAL'))
        self.assertIn('vendedora', p.detalle)

    def test_con_correo_pero_sin_telefono_no_hay_pendiente(self):
        solo_correo = Cliente.objects.create(nombre='Solo Correo', email='c@example.com')
        _cotizacion(solo_correo)
        svc.pedir_consentimientos_pendientes()
        self.assertEqual(Pendiente.objects.count(), 0)


class BotonTest(Base):
    def _boton(self, opcion, version='1.0', cliente=None):
        return f'con:{(cliente or self.cliente).pk}:{opcion}:{version}'

    def test_acepto_deja_evidencia_whatsapp_con_el_wamid(self):
        r = svc.atender_boton('52' + TELEFONO, self._boton('ok'), 'wamid.BOTON')
        self.assertIn('quedó registrada', r)
        a = AceptacionLegal.objects.get(cliente=self.cliente)
        self.assertEqual((a.origen, a.referencia_externa), (OrigenAceptacion.WHATSAPP, 'wamid.BOTON'))
        self.assertEqual(a.finalidades_aceptadas, [])

    def test_acepto_con_promociones(self):
        svc.atender_boton(TELEFONO, self._boton('promo'), 'wamid.P')
        self.assertEqual(AceptacionLegal.objects.get().finalidades_aceptadas, ['MARKETING'])

    def test_no_acepto_no_guarda_nada(self):
        self.assertIn('no registramos', svc.atender_boton(TELEFONO, self._boton('no'), 'w'))
        self.assertEqual(AceptacionLegal.objects.count(), 0)

    def test_boton_desde_otro_telefono_se_descarta(self):
        self.assertIsNone(svc.atender_boton('9998887766', self._boton('ok'), 'w'))
        self.assertEqual(AceptacionLegal.objects.count(), 0)

    def test_boton_de_una_version_vieja_no_registra(self):
        _publicar('2.0')
        self.assertIn('venció', svc.atender_boton(TELEFONO, self._boton('ok', '1.0'), 'w'))
        self.assertEqual(AceptacionLegal.objects.count(), 0)

    def test_tocar_dos_veces_no_duplica(self):
        svc.atender_boton(TELEFONO, self._boton('ok'), 'w1')
        svc.atender_boton(TELEFONO, self._boton('ok'), 'w2')
        self.assertEqual(AceptacionLegal.objects.count(), 1)

    def test_el_webhook_lo_trata_como_boton_interno(self):
        from comunicacion.views_whatsapp import _es_boton_interno
        self.assertTrue(_es_boton_interno(self._boton('ok')))


class ResumenYAdminTest(Base):
    def test_linea_del_resumen_solo_si_hay_casos(self):
        from comunicacion.services_direccion import texto_resumen
        mes = {'meta_venta': 0, 'venta': 0, 'etiqueta': 'nov', 'ocupacion': 0}
        sin = texto_resumen(date.today(), mes, Decimal('0'), 0, [], None, 0).texto()
        con = texto_resumen(date.today(), mes, Decimal('0'), 0, [], None, 3).texto()
        self.assertNotIn('Aviso de Privacidad', sin)
        self.assertIn('3 clientes con reservación confirmada sin aceptar el Aviso de Privacidad', con)

    def test_conteo_de_confirmados_sin_aceptar(self):
        _cotizacion(self.cliente)
        self.assertEqual(svc.contar_confirmados_sin_aceptar(), 1)
        self.aceptar()
        self.assertEqual(svc.contar_confirmados_sin_aceptar(), 0)

    def test_columna_y_filtro_en_clientes(self):
        from core_erp.test_utils import login_superuser_con_totp
        admin = get_user_model().objects.create_superuser('dir', 'd@example.com', 'x')
        login_superuser_con_totp(self.client, admin)
        otro = Cliente.objects.create(nombre='Pendiente Pérez')
        self.aceptar()
        r = self.client.get('/admin/comercial/cliente/')
        self.assertContains(r, 'Aceptado')
        self.assertContains(r, 'Pendiente')
        r = self.client.get('/admin/comercial/cliente/?privacidad=pendiente')
        self.assertContains(r, otro.nombre)
        self.assertNotContains(r, 'Ana Ruiz')


class AvisoEnDocumentosTest(Base):
    def test_el_pdf_y_el_correo_llevan_el_enlace_al_aviso(self):
        from django.template.loader import render_to_string
        cot = _cotizacion(self.cliente, estado='COTIZADA')
        for plantilla in ('emails/cotizacion.html', 'comunicacion/email/cotizacion.html'):
            html = render_to_string(plantilla, {'cotizacion': cot, 'cliente': self.cliente, 'portal_url': 'https://x'})
            self.assertIn('quintakooxtanil.com/aviso-de-privacidad', html)
        from comercial.views import obtener_contexto_cotizacion
        from core_erp.documentos import render_pdf  # noqa: F401  (el PDF usa la misma plantilla)
        html = render_to_string('cotizaciones/pdf_recibo.html', obtener_contexto_cotizacion(cot))
        self.assertIn('quintakooxtanil.com/aviso-de-privacidad', html)


