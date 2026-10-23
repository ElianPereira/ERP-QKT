# ruff: noqa: S303
"""Descarga masiva del SAT: firma, máquina de estados, importación y vínculo.

El SAT real no se toca: el cliente HTTP es un doble. Lo que sí se prueba contra
criptografía real es la firma (certificado y llave generados en el test)."""
import base64
import io
import zipfile
from datetime import date, datetime, timedelta
from datetime import timezone as dt_tz
from decimal import Decimal
from unittest.mock import patch

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID, ObjectIdentifier
from django.test import TestCase, override_settings
from django.utils import timezone
from lxml import etree

from comercial.models import Cliente, Compra
from facturacion.models import SolicitudFactura

from . import services_sat as sat
from . import services_sat_ws as ws
from .models import CfdiSAT, CredencialSAT, SolicitudDescargaSAT

STORAGE_MEMORIA = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
LLAVE = Fernet.generate_key().decode()
RFC = 'PECE010202IA0'
CLAVE = 'contraseña-de-prueba'


def fiel_de_prueba(rfc=RFC, dias=365):
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, 'TITULAR DE PRUEBA'),
        x509.NameAttribute(ObjectIdentifier('2.5.4.45'), f'{rfc} / CURP010101HYNXXX00'),
    ])
    ahora = datetime.now(dt_tz.utc)
    cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre).public_key(llave.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(ahora - timedelta(days=1))
            .not_valid_after(ahora + timedelta(days=dias)).sign(llave, hashes.SHA256()))
    key_der = llave.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(CLAVE.encode()))
    return cert.public_bytes(serialization.Encoding.DER), key_der


def cfdi(uuid, emisor, receptor, total='1160.00', tipo='I', fecha='2026-10-05T10:00:00', regimen='626'):
    return (
        '<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" Version="4.0" '
        f'TipoDeComprobante="{tipo}" Total="{total}" SubTotal="1000.00" Fecha="{fecha}">'
        f'<cfdi:Emisor Rfc="{emisor}" Nombre="EMISOR SA"/>'
        f'<cfdi:Receptor Rfc="{receptor}" Nombre="RECEPTOR SA" UsoCFDI="G03" RegimenFiscalReceptor="{regimen}"/>'
        '<cfdi:Complemento><tfd:TimbreFiscalDigital xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" '
        f'UUID="{uuid}"/></cfdi:Complemento></cfdi:Comprobante>'
    ).encode()


def paquete(*xmls):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for i, x in enumerate(xmls):
            z.writestr(f'{i}.xml', x)
    return buf.getvalue()


class ClienteFalso:
    """Doble de ClienteSAT con las respuestas que se le programen."""

    def __init__(self, solicita=None, verifica=None, descarga=None):
        self.solicita, self.verifica, self.descarga = solicita, verifica, descarga or {}

    def solicitar(self, tipo, desde, hasta):
        return self.solicita

    def verificar(self, id_solicitud):
        return self.verifica

    def descargar(self, id_paquete):
        return self.descarga[id_paquete]


@override_settings(ERP_SAT_FIEL_CIFRADO_SECRET=LLAVE, STORAGES=STORAGE_MEMORIA)
class FielYFirmaTest(TestCase):
    def setUp(self):
        self.cer, self.key = fiel_de_prueba()
        self.fiel = ws.Fiel(self.cer, self.key, CLAVE)

    def test_lee_rfc_y_vigencia(self):
        self.assertEqual(self.fiel.rfc, RFC)
        self.assertFalse(self.fiel.vencida)

    def test_contrasena_incorrecta(self):
        with self.assertRaises(ws.FielError):
            ws.Fiel(self.cer, self.key, 'mala')

    def test_cer_y_key_de_distinta_pareja(self):
        otro_cer, _ = fiel_de_prueba()
        with self.assertRaises(ws.FielError):
            ws.Fiel(otro_cer, self.key, CLAVE)

    def _verificar_firma(self, sobre, nodo):
        raiz = etree.fromstring(sobre)
        el = next(e for e in raiz.iter() if isinstance(e.tag, str) and etree.QName(e).localname == nodo)
        sig = el.find(f'{{{ws.NS_DS}}}Signature')
        info = sig.find(f'{{{ws.NS_DS}}}SignedInfo')
        digest = info.find(f'.//{{{ws.NS_DS}}}DigestValue').text
        el.remove(sig)
        h = hashes.Hash(hashes.SHA1())
        h.update(ws._c14n(el))
        self.assertEqual(digest, base64.b64encode(h.finalize()).decode())
        el.append(sig)
        valor = base64.b64decode(sig.find(f'{{{ws.NS_DS}}}SignatureValue').text)
        self.fiel.cert.public_key().verify(valor, ws._c14n(info), padding.PKCS1v15(), hashes.SHA1())

    def test_peticion_firmada_es_verificable(self):
        sobre = ws._sobre_firmado(self.fiel, 'SolicitaDescargaEmitidos', 'solicitud',
                                  {'RfcSolicitante': RFC, 'RfcEmisor': RFC, 'FechaInicial': '2026-10-01T00:00:00'})
        self._verificar_firma(sobre, 'solicitud')

    def test_autenticacion_firma_el_timestamp(self):
        sobre = ws.sobre_autenticacion(self.fiel)
        raiz = etree.fromstring(sobre)
        ts = next(e for e in raiz.iter() if isinstance(e.tag, str) and etree.QName(e).localname == 'Timestamp')
        sig = next(e for e in raiz.iter() if isinstance(e.tag, str) and etree.QName(e).localname == 'Signature')
        info = sig.find(f'{{{ws.NS_DS}}}SignedInfo')
        h = hashes.Hash(hashes.SHA1())
        h.update(ws._c14n(ts))
        self.assertEqual(info.find(f'.//{{{ws.NS_DS}}}DigestValue').text, base64.b64encode(h.finalize()).decode())
        valor = base64.b64decode(sig.find(f'{{{ws.NS_DS}}}SignatureValue').text)
        self.fiel.cert.public_key().verify(valor, ws._c14n(info), padding.PKCS1v15(), hashes.SHA1())
        self.assertIn(b'BinarySecurityToken', sobre)


@override_settings(ERP_SAT_FIEL_CIFRADO_SECRET=LLAVE, STORAGES=STORAGE_MEMORIA)
class CredencialTest(TestCase):
    def test_guarda_cifrada_y_se_puede_recuperar(self):
        cer, key = fiel_de_prueba()
        cred = sat.guardar_credencial(cer, key, CLAVE)
        self.assertEqual(cred.rfc, RFC)
        self.assertNotIn(CLAVE.encode(), bytes(cred.password_cifrada))
        self.assertNotEqual(bytes(cred.key_cifrada), key)
        self.assertEqual(sat.fiel_de(cred).rfc, RFC)

    def test_sin_llave_de_cifrado_no_guarda(self):
        cer, key = fiel_de_prueba()
        with override_settings(ERP_SAT_FIEL_CIFRADO_SECRET=''), self.assertRaises(sat.ConfiguracionSATError):
            sat.guardar_credencial(cer, key, CLAVE)

    def test_la_llave_tolera_comillas_espacios_y_prefijo_de_bytes(self):
        cer, key = fiel_de_prueba()
        for sucia in (f"  {LLAVE}\n", f'"{LLAVE}"', f"'{LLAVE}'", f"b'{LLAVE}'"):
            with self.subTest(sucia=sucia), override_settings(ERP_SAT_FIEL_CIFRADO_SECRET=sucia):
                self.assertEqual(sat.guardar_credencial(cer, key, CLAVE).rfc, RFC)

    def test_llave_invalida_dice_como_arreglarla(self):
        with override_settings(ERP_SAT_FIEL_CIFRADO_SECRET='demasiado-corta'), \
                self.assertRaisesMessage(sat.ConfiguracionSATError, '44'):
            sat._fernet()

    def test_rechaza_efirma_vencida(self):
        cer, key = fiel_de_prueba(dias=-0)
        with self.assertRaises(ws.FielError):
            sat.guardar_credencial(cer, key, CLAVE)

    def test_renovar_reemplaza_en_vez_de_duplicar(self):
        for _ in range(2):
            sat.guardar_credencial(*fiel_de_prueba(), CLAVE)
        self.assertEqual(CredencialSAT.objects.count(), 1)


@override_settings(ERP_SAT_FIEL_CIFRADO_SECRET=LLAVE, STORAGES=STORAGE_MEMORIA)
class FlujoDescargaTest(TestCase):
    def setUp(self):
        self.cred = sat.guardar_credencial(*fiel_de_prueba(), CLAVE)

    def _solicitud(self, tipo='RECIBIDAS', estado='PENDIENTE', **kw):
        return SolicitudDescargaSAT.objects.create(
            credencial=self.cred, tipo=tipo, desde=date(2026, 10, 1), hasta=date(2026, 10, 10), estado=estado, **kw)

    def test_recorrido_completo_de_recibidas(self):
        s = self._solicitud()
        xml = cfdi('AAAAAAAA-0000-0000-0000-000000000001', 'PRV010101ABC', RFC)
        c = ClienteFalso(
            solicita={'id_solicitud': 'S-1', 'codigo': '5000', 'mensaje': 'ok'},
            verifica={'codigo': '5000', 'estado': 3, 'codigo_estado': '5000', 'numero_cfdis': 1,
                      'mensaje': '', 'paquetes': ['P1']},
            descarga={'P1': {'codigo': '5000', 'mensaje': '', 'zip': paquete(xml)}})
        self.assertEqual(sat.avanzar(s, c), 'ENVIADA')
        self.assertEqual(sat.avanzar(s, c), 'DESCARGADA')
        s.refresh_from_db()
        self.assertEqual(s.importados, 1)
        compra = Compra.objects.get()
        self.assertEqual(compra.uuid, 'AAAAAAAA-0000-0000-0000-000000000001')
        self.assertEqual(CfdiSAT.objects.get().resultado, 'IMPORTADO')

    def test_descargar_dos_veces_no_duplica(self):
        xml = cfdi('AAAAAAAA-0000-0000-0000-000000000002', 'PRV010101ABC', RFC)
        for _ in range(2):
            s = self._solicitud(estado='TERMINADA', paquetes=['P1'])
            sat.avanzar(s, ClienteFalso(descarga={'P1': {'codigo': '5000', 'mensaje': '', 'zip': paquete(xml)}}))
        self.assertEqual(Compra.objects.count(), 1)
        s.refresh_from_db()
        self.assertEqual(s.duplicados, 1)

    def test_recibida_que_no_es_del_negocio_se_omite(self):
        s = self._solicitud(estado='TERMINADA', paquetes=['P1'])
        xml = cfdi('AAAAAAAA-0000-0000-0000-000000000003', 'PRV010101ABC', RFC, regimen='605')
        sat.avanzar(s, ClienteFalso(descarga={'P1': {'codigo': '5000', 'mensaje': '', 'zip': paquete(xml)}}))
        self.assertEqual(Compra.objects.count(), 0)
        self.assertEqual(CfdiSAT.objects.get().resultado, 'OMITIDO')

    def test_sin_informacion(self):
        s = self._solicitud()
        sat.avanzar(s, ClienteFalso(solicita={'id_solicitud': '', 'codigo': '5004', 'mensaje': 'sin datos'}))
        self.assertEqual(s.estado, 'SIN_INFORMACION')

    def test_rechazo_del_sat_queda_en_error(self):
        s = self._solicitud()
        sat.avanzar(s, ClienteFalso(solicita={'id_solicitud': '', 'codigo': '5002', 'mensaje': 'agotada'}))
        self.assertEqual(s.estado, 'ERROR')
        self.assertEqual(s.codigo_sat, '5002')

    def test_sat_preparando_no_avanza(self):
        s = self._solicitud(estado='ENVIADA', id_solicitud_sat='S-1')
        sat.avanzar(s, ClienteFalso(verifica={'codigo': '5000', 'estado': 2, 'codigo_estado': '5000',
                                              'numero_cfdis': 0, 'mensaje': '', 'paquetes': []}))
        self.assertEqual(s.estado, 'EN_PROCESO')

    def test_falla_transitoria_conserva_el_estado(self):
        class Roto(ClienteFalso):
            def verificar(self, id_solicitud):
                raise ws.SatError("sin conexión")
        s = self._solicitud(estado='ENVIADA', id_solicitud_sat='S-1')
        sat.avanzar(s, Roto())
        s.refresh_from_db()
        self.assertEqual(s.estado, 'ENVIADA')
        self.assertIn('sin conexión', s.mensaje)

    def test_zip_con_demasiados_archivos_se_rechaza(self):
        s = self._solicitud(estado='TERMINADA', paquetes=['P1'])
        with patch.object(sat, 'MAX_ARCHIVOS_ZIP', 1):
            xml = cfdi('AAAAAAAA-0000-0000-0000-000000000004', 'PRV010101ABC', RFC)
            sat.avanzar(s, ClienteFalso(descarga={'P1': {'codigo': '5000', 'mensaje': '', 'zip': paquete(xml, xml)}}))
        self.assertEqual(s.estado, 'TERMINADA')
        self.assertIn('límites', s.mensaje)

    def test_planear_cubre_hasta_ayer_y_no_repite(self):
        hoy = timezone.localdate()
        self.assertEqual(len(sat.planear_sincronizacion(self.cred, hoy=hoy)), 4)  # 35 días = 2 tramos por tipo
        self.assertEqual(len(sat.planear_sincronizacion(self.cred, hoy=hoy)), 0)
        ultima = SolicitudDescargaSAT.objects.latest('id')
        self.assertEqual(ultima.hasta, hoy - timedelta(days=1))
        # al día siguiente solo se pide lo nuevo, con 3 días de solape
        sat.planear_sincronizacion(self.cred, hoy=hoy + timedelta(days=1))
        nueva = SolicitudDescargaSAT.objects.latest('id')
        self.assertEqual((nueva.desde, nueva.hasta), (hoy - timedelta(days=3), hoy))

    def test_historial_se_pide_por_tramos_y_poco_a_poco(self):
        hoy = timezone.localdate()
        self.cred.historial_desde = hoy - timedelta(days=400)
        self.cred.save()
        primera = sat.planear_sincronizacion(self.cred, hoy=hoy)
        self.assertEqual(len(primera), sat.MAX_SOLICITUDES_POR_CORRIDA)
        self.assertTrue(all((s.hasta - s.desde).days < sat.MAX_DIAS_TRAMO for s in primera))
        # las siguientes corridas continúan hasta cubrir todo, sin repetir
        for _ in range(10):
            sat.planear_sincronizacion(self.cred, hoy=hoy)
        self.assertEqual(sat.planear_sincronizacion(self.cred, hoy=hoy), [])
        self.assertEqual(SolicitudDescargaSAT.objects.filter(tipo='RECIBIDAS').order_by('desde').first().desde,
                         hoy - timedelta(days=400))

    def test_tramo_de_historial_fallido_se_repite(self):
        hoy = timezone.localdate()
        self.cred.historial_desde = hoy - timedelta(days=100)
        self.cred.save()
        for _ in range(3):
            sat.planear_sincronizacion(self.cred, hoy=hoy)
        viejo = SolicitudDescargaSAT.objects.filter(tipo='EMITIDAS').order_by('desde').first()
        SolicitudDescargaSAT.objects.filter(pk=viejo.pk).update(
            estado='ERROR', updated_at=timezone.now() - timedelta(days=1))
        nuevas = sat.planear_sincronizacion(self.cred, hoy=hoy)
        self.assertEqual([(n.tipo, n.desde, n.hasta) for n in nuevas], [('EMITIDAS', viejo.desde, viejo.hasta)])

    def test_verificacion_rechazada_con_5004_es_sin_informacion(self):
        s = self._solicitud(estado='ENVIADA', id_solicitud_sat='S-1')
        sat.avanzar(s, ClienteFalso(verifica={'codigo': '5000', 'estado': 5, 'codigo_estado': '5004',
                                              'numero_cfdis': 0, 'mensaje': 'Solicitud Aceptada', 'paquetes': []}))
        s.refresh_from_db()
        self.assertEqual(s.estado, 'SIN_INFORMACION')
        self.assertTrue(s.terminada)

    def test_verificacion_rechazada_con_otro_codigo_es_error(self):
        s = self._solicitud(estado='ENVIADA', id_solicitud_sat='S-1')
        sat.avanzar(s, ClienteFalso(verifica={'codigo': '5000', 'estado': 5, 'codigo_estado': '5002',
                                              'numero_cfdis': 0, 'mensaje': 'x', 'paquetes': []}))
        self.assertEqual(s.estado, 'ERROR')

    def test_sin_informacion_no_se_vuelve_a_pedir(self):
        hoy = timezone.localdate()
        sat.planear_sincronizacion(self.cred, hoy=hoy)
        SolicitudDescargaSAT.objects.update(estado='SIN_INFORMACION')
        self.assertEqual(sat.planear_sincronizacion(self.cred, hoy=hoy), [])

    def test_cuota_agotada_se_pide_con_otro_rango(self):
        hoy = timezone.localdate()
        sat.planear_sincronizacion(self.cred, hoy=hoy)
        rota = SolicitudDescargaSAT.objects.filter(tipo='RECIBIDAS').order_by('desde').first()
        sat.avanzar(rota, ClienteFalso(solicita={'id_solicitud': '', 'codigo': '5002', 'mensaje': 'agotada'}))
        self.assertEqual(rota.codigo_sat, '5002')
        SolicitudDescargaSAT.objects.filter(pk=rota.pk).update(updated_at=timezone.now() - timedelta(hours=7))
        nuevas = sat.planear_sincronizacion(self.cred, hoy=hoy)
        self.assertEqual([(n.tipo, n.desde, n.hasta) for n in nuevas],
                         [('RECIBIDAS', rota.desde - timedelta(days=1), rota.hasta)])
        # si el rango corrido también se agota, se corre otro día más; nunca el mismo
        sat.avanzar(nuevas[0], ClienteFalso(solicita={'id_solicitud': '', 'codigo': '5002', 'mensaje': 'agotada'}))
        otras = sat.planear_sincronizacion(self.cred, hoy=hoy)
        self.assertEqual([(n.desde, n.hasta) for n in otras], [(rota.desde - timedelta(days=2), rota.hasta)])

    def test_mes_cerrado_no_deja_sus_ultimos_dias_como_hueco(self):
        hoy = timezone.localdate()
        desde, hasta = hoy - timedelta(days=60), hoy - timedelta(days=30)
        # pedida hoy, cuando el SAT ya había publicado todo ese rango
        SolicitudDescargaSAT.objects.create(credencial=self.cred, tipo='RECIBIDAS', desde=desde, hasta=hasta,
                                            estado='DESCARGADA')
        self.assertEqual(sat._huecos(self.cred, 'RECIBIDAS', desde, hasta), [])

    def test_solicitud_reciente_conserva_el_solape(self):
        hoy = timezone.localdate()
        sat.planear_sincronizacion(self.cred, hoy=hoy)
        ayer = hoy - timedelta(days=1)
        self.assertEqual(sat._huecos(self.cred, 'RECIBIDAS', hoy - timedelta(days=35), ayer),
                         [(ayer - timedelta(days=sat.SOLAPE_DIAS - 1), ayer)])

    def test_tramo_con_error_definitivo_se_vuelve_a_pedir(self):
        hoy = timezone.localdate()
        sat.planear_sincronizacion(self.cred, hoy=hoy)
        rota = SolicitudDescargaSAT.objects.filter(tipo='RECIBIDAS').order_by('desde').first()
        sat.avanzar(rota, ClienteFalso(solicita={'id_solicitud': '', 'codigo': '5003', 'mensaje': 'tope'}))
        self.assertEqual(rota.estado, 'ERROR')
        # recién fallada: no se insiste (espera de 6 h)
        self.assertEqual(len(sat.planear_sincronizacion(self.cred, hoy=hoy)), 0)
        SolicitudDescargaSAT.objects.filter(pk=rota.pk).update(updated_at=timezone.now() - timedelta(hours=7))
        nuevas = sat.planear_sincronizacion(self.cred, hoy=hoy)
        self.assertEqual([(n.tipo, n.desde, n.hasta) for n in nuevas], [('RECIBIDAS', rota.desde, rota.hasta)])

    def test_deja_de_insistir_tras_muchos_errores_del_mismo_tramo(self):
        hoy = timezone.localdate()
        sat.planear_sincronizacion(self.cred, hoy=hoy)
        base = SolicitudDescargaSAT.objects.filter(tipo='RECIBIDAS').order_by('desde').first()
        SolicitudDescargaSAT.objects.filter(pk=base.pk).update(estado='ERROR')
        for _ in range(sat.MAX_REINTENTOS_TRAMO - 1):
            SolicitudDescargaSAT.objects.create(credencial=self.cred, tipo='RECIBIDAS', desde=base.desde,
                                                hasta=base.hasta, estado='ERROR')
        SolicitudDescargaSAT.objects.update(updated_at=timezone.now() - timedelta(days=2))
        self.assertEqual([n for n in sat.planear_sincronizacion(self.cred, hoy=hoy) if n.tipo == 'RECIBIDAS'], [])

    def test_falla_del_servicio_del_sat_es_transitoria(self):
        s = self._solicitud()
        sat.avanzar(s, ClienteFalso(solicita={'id_solicitud': '', 'codigo': '404', 'mensaje': 'Error no controlado'}))
        self.assertEqual(s.estado, 'PENDIENTE')
        self.assertIn('Error no controlado', s.mensaje)
        s2 = self._solicitud(estado='ENVIADA', id_solicitud_sat='S-1')
        sat.avanzar(s2, ClienteFalso(verifica={'codigo': '5006', 'estado': 0, 'codigo_estado': '', 'numero_cfdis': 0,
                                               'mensaje': 'interno', 'paquetes': []}))
        self.assertEqual(s2.estado, 'ENVIADA')

    def test_respuesta_sin_codigo_no_se_toma_por_rechazo(self):
        s = self._solicitud()
        sat.avanzar(s, ClienteFalso(solicita={'id_solicitud': '', 'codigo': '', 'mensaje': ''}))
        self.assertEqual(s.estado, 'PENDIENTE')

    def test_paquetes_vencidos_pasan_a_error_para_repedirse(self):
        s = self._solicitud(estado='TERMINADA', paquetes=['P1'], terminada_en=timezone.now() - timedelta(hours=71))
        sat.avanzar(s, ClienteFalso())
        self.assertEqual(s.estado, 'ERROR')

    def test_solicitud_que_nunca_termina_se_da_por_perdida(self):
        s = self._solicitud(estado='EN_PROCESO', id_solicitud_sat='S-1')
        SolicitudDescargaSAT.objects.filter(pk=s.pk).update(created_at=timezone.now() - timedelta(hours=73))
        s.refresh_from_db()
        sat.avanzar(s, ClienteFalso())
        self.assertEqual(s.estado, 'ERROR')

    def test_avisa_al_equipo_tras_un_dia_de_fallas(self):
        class Roto(ClienteFalso):
            def verificar(self, id_solicitud):
                raise ws.SatError("caído")
        s = self._solicitud(estado='ENVIADA', id_solicitud_sat='S-1', intentos=sat.INTENTOS_PARA_AVISAR - 1)
        with patch('comunicacion.services.alertar_equipo_email') as alerta:
            sat.avanzar(s, Roto())
        alerta.assert_called_once()

    def test_24_fallos_abren_un_pendiente_sat_urgente_para_direccion_una_sola_vez(self):
        from comunicacion.models import Pendiente

        class Roto(ClienteFalso):
            def verificar(self, id_solicitud):
                raise ws.SatError("caído")
        s = self._solicitud(estado='ENVIADA', id_solicitud_sat='S-1', intentos=sat.INTENTOS_PARA_AVISAR - 1)
        sat.avanzar(s, Roto())
        sat.avanzar(s, Roto())  # el intento 25 ya no vuelve a avisar
        p = Pendiente.objects.get(tipo='SAT')
        self.assertEqual((p.severidad, p.rol), ('URGENTE', 'DIRECCION'))
        self.assertEqual(p.clave_idempotencia, f'sat:{s.pk}:atascada')
        self.assertFalse(Pendiente.objects.filter(tipo='ALERTA', titulo__icontains='SAT').exists())

    def test_un_error_definitivo_abre_su_propio_pendiente_sat(self):
        from comunicacion.models import Pendiente
        s = self._solicitud(estado='EN_PROCESO', id_solicitud_sat='S-1')
        SolicitudDescargaSAT.objects.filter(pk=s.pk).update(created_at=timezone.now() - timedelta(hours=73))
        s.refresh_from_db()
        sat.avanzar(s, ClienteFalso())
        self.assertEqual(Pendiente.objects.get(tipo='SAT').clave_idempotencia, f'sat:{s.pk}:error')

    def test_un_error_inesperado_no_detiene_a_las_demas_solicitudes(self):
        malo = self._solicitud()
        bueno = self._solicitud(tipo='EMITIDAS')
        c = ClienteFalso(solicita={'id_solicitud': 'S-9', 'codigo': '5000', 'mensaje': ''})
        original = sat.avanzar

        def avanzar(s, cliente=None):
            if s.pk == malo.pk:
                raise RuntimeError("bug")
            return original(s, cliente)
        with patch.object(sat, 'avanzar', avanzar):
            sat.procesar_pendientes({self.cred.pk: c})
        bueno.refresh_from_db()
        self.assertEqual(bueno.estado, 'ENVIADA')

    def test_un_xml_que_truena_no_frena_el_paquete_y_se_reintenta(self):
        s = self._solicitud(estado='TERMINADA', paquetes=['P1'])
        bueno = cfdi('AAAAAAAA-0000-0000-0000-000000000010', 'PRV010101ABC', RFC)
        malo = cfdi('AAAAAAAA-0000-0000-0000-000000000011', 'PRV010101ABC', RFC)
        original = sat._procesar_recibido

        def recibido(sol, registro, datos, xml):
            if datos['uuid'].endswith('11'):
                raise RuntimeError("falla de base de datos")
            return original(sol, registro, datos, xml)
        with patch.object(sat, '_procesar_recibido', recibido):
            sat.avanzar(s, ClienteFalso(descarga={'P1': {'codigo': '5000', 'mensaje': '',
                                                         'zip': paquete(malo, bueno)}}))
        self.assertEqual(s.estado, 'DESCARGADA')
        self.assertEqual(Compra.objects.count(), 1)
        reg = CfdiSAT.objects.get(uuid__endswith='11')
        self.assertEqual(reg.resultado, 'ERROR')
        self.assertTrue(reg.archivo_xml)
        self.assertEqual(sat.reintentar_comprobantes(), 1)
        self.assertEqual(Compra.objects.count(), 2)
        reg.refresh_from_db()
        self.assertEqual(reg.resultado, 'IMPORTADO')

    def test_si_no_se_puede_guardar_el_error_el_paquete_se_reintenta_completo(self):
        s = self._solicitud(estado='TERMINADA', paquetes=['P1'])
        xml = cfdi('AAAAAAAA-0000-0000-0000-000000000012', 'PRV010101ABC', RFC)
        with patch.object(sat, '_procesar_cfdi', side_effect=RuntimeError("storage caído")), \
                patch.object(sat, '_registrar_error', side_effect=ws.SatError("sin storage")):
            sat.avanzar(s, ClienteFalso(descarga={'P1': {'codigo': '5000', 'mensaje': '', 'zip': paquete(xml)}}))
        s.refresh_from_db()
        self.assertEqual(s.estado, 'TERMINADA')
        self.assertEqual(s.paquetes_descargados, [])
        # el SAT se recupera: la siguiente corrida baja el paquete completo
        sat.avanzar(s, ClienteFalso(descarga={'P1': {'codigo': '5000', 'mensaje': '', 'zip': paquete(xml)}}))
        self.assertEqual(s.estado, 'DESCARGADA')
        self.assertEqual(Compra.objects.count(), 1)




@override_settings(ERP_SAT_FIEL_CIFRADO_SECRET=LLAVE, STORAGES=STORAGE_MEMORIA)
class EmitidasTest(TestCase):
    def setUp(self):
        self.cred = sat.guardar_credencial(*fiel_de_prueba(), CLAVE)
        self.cliente = Cliente.objects.create(nombre='Cliente Prueba')
        self.descarga = SolicitudDescargaSAT.objects.create(
            credencial=self.cred, tipo='EMITIDAS', desde=date(2026, 10, 1), hasta=date(2026, 10, 10),
            estado='TERMINADA', paquetes=['P1'])

    def _solicitud_factura(self, monto='1160.00', rfc='XEXX010101000', **kw):
        kw.setdefault('fecha_solicitud', timezone.make_aware(datetime(2026, 10, 3)))
        return SolicitudFactura.objects.create(
            cliente=self.cliente, rfc=rfc, razon_social='CLIENTE SA', codigo_postal='97390', monto=Decimal(monto),
            estado='ENVIADA', **kw)

    def _bajar(self, *xmls):
        sat.avanzar(self.descarga, ClienteFalso(
            descarga={'P1': {'codigo': '5000', 'mensaje': '', 'zip': paquete(*xmls)}}))

    def test_se_liga_a_la_unica_solicitud_que_coincide(self):
        sf = self._solicitud_factura()
        uuid = 'BBBBBBBB-0000-0000-0000-000000000001'
        self._bajar(cfdi(uuid, RFC, 'XEXX010101000'))
        sf.refresh_from_db()
        self.assertEqual(sf.uuid_factura, uuid)
        self.assertEqual(sf.estado, 'FACTURADA')
        self.assertTrue(sf.archivo_xml)
        self.assertTrue(sf.archivo_pdf)
        self.assertTrue(sf.tiene_factura)
        self.assertEqual(sf.fecha_factura, date(2026, 10, 5))
        self.assertEqual(CfdiSAT.objects.get().resultado, 'VINCULADO')

    def test_no_pisa_un_pdf_que_el_contador_ya_subio(self):
        from django.core.files.base import ContentFile
        sf = self._solicitud_factura()
        sf.archivo_pdf.save('mio.pdf', ContentFile(b'%PDF-mio'), save=True)
        nombre = sf.archivo_pdf.name
        self._bajar(cfdi('BBBBBBBB-0000-0000-0000-000000000008', RFC, 'XEXX010101000'))
        sf.refresh_from_db()
        self.assertEqual(sf.archivo_pdf.name, nombre)

    def test_con_dos_candidatas_no_adivina(self):
        self._solicitud_factura()
        self._solicitud_factura()
        self._bajar(cfdi('BBBBBBBB-0000-0000-0000-000000000002', RFC, 'XEXX010101000'))
        reg = CfdiSAT.objects.get()
        self.assertEqual(reg.resultado, 'SIN_VINCULAR')
        self.assertIn('Varias', reg.detalle)
        self.assertFalse(SolicitudFactura.objects.exclude(uuid_factura='').exists())

    def test_monto_distinto_queda_sin_vincular(self):
        self._solicitud_factura(monto='500.00')
        self._bajar(cfdi('BBBBBBBB-0000-0000-0000-000000000003', RFC, 'XEXX010101000'))
        self.assertEqual(CfdiSAT.objects.get().resultado, 'SIN_VINCULAR')

    def test_reintento_liga_cuando_la_solicitud_aparece_despues(self):
        self._bajar(cfdi('BBBBBBBB-0000-0000-0000-000000000004', RFC, 'XEXX010101000'))
        sf = self._solicitud_factura()
        self.assertEqual(sat.reintentar_comprobantes(), 1)
        sf.refresh_from_db()
        self.assertEqual(sf.estado, 'FACTURADA')

    def test_factura_ya_subida_por_el_contador_no_se_pisa(self):
        uuid = 'BBBBBBBB-0000-0000-0000-000000000005'
        sf = self._solicitud_factura(uuid_factura=uuid)
        self._bajar(cfdi(uuid, RFC, 'XEXX010101000'))
        reg = CfdiSAT.objects.get()
        self.assertEqual(reg.resultado, 'VINCULADO')
        self.assertEqual(reg.solicitud_factura, sf)

    def test_emisor_ajeno_se_omite(self):
        self._bajar(cfdi('BBBBBBBB-0000-0000-0000-000000000007', 'OTRO010101ABC', 'XEXX010101000'))
        self.assertEqual(set(CfdiSAT.objects.values_list('resultado', flat=True)), {'OMITIDO'})

    def test_otro_tipo_de_comprobante_se_omite(self):
        self._bajar(cfdi('BBBBBBBB-0000-0000-0000-000000000009', RFC, 'XEXX010101000', tipo='P'))
        self.assertEqual(CfdiSAT.objects.get().resultado, 'OMITIDO')

    def test_el_matching_respeta_el_tipo_de_comprobante(self):
        factura = self._solicitud_factura()
        nota = self._solicitud_factura(tipo_comprobante='E')
        # Un egreso solo liga con la nota de crédito, aunque RFC/monto/fecha coincidan con la factura.
        self._bajar(cfdi('BBBBBBBB-0000-0000-0000-000000000006', RFC, 'XEXX010101000', tipo='E'))
        factura.refresh_from_db()
        nota.refresh_from_db()
        self.assertEqual(nota.uuid_factura, 'BBBBBBBB-0000-0000-0000-000000000006')
        self.assertEqual(nota.estado, 'FACTURADA')
        self.assertEqual(factura.uuid_factura, '')

    def test_un_ingreso_solo_liga_con_la_factura_no_con_la_nota(self):
        factura = self._solicitud_factura()
        nota = self._solicitud_factura(tipo_comprobante='E')
        self._bajar(cfdi('BBBBBBBB-0000-0000-0000-000000000010', RFC, 'XEXX010101000'))
        factura.refresh_from_db()
        nota.refresh_from_db()
        self.assertEqual(factura.uuid_factura, 'BBBBBBBB-0000-0000-0000-000000000010')
        self.assertEqual(nota.uuid_factura, '')

    def test_egreso_sin_nota_de_credito_no_liga_con_una_factura(self):
        factura = self._solicitud_factura()
        self._bajar(cfdi('BBBBBBBB-0000-0000-0000-000000000011', RFC, 'XEXX010101000', tipo='E'))
        factura.refresh_from_db()
        self.assertEqual(factura.uuid_factura, '')
        self.assertEqual(CfdiSAT.objects.get().resultado, 'SIN_VINCULAR')


@override_settings(ERP_SAT_FIEL_CIFRADO_SECRET=LLAVE, STORAGES=STORAGE_MEMORIA)
class AdminEfirmaTest(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from core_erp.test_utils import login_superuser_con_totp
        self.usuario = User.objects.create_superuser('dir', 'd@x.com', 'x')
        login_superuser_con_totp(self.client, self.usuario)

    def test_alta_de_efirma_desde_el_admin(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        cer, key = fiel_de_prueba()
        r = self.client.post('/admin/contabilidad/credencialsat/add/', {
            'archivo_cer': SimpleUploadedFile('f.cer', cer), 'archivo_key': SimpleUploadedFile('f.key', key),
            'password': CLAVE, 'activa': 'on'})
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context['adminform'].errors)
        cred = CredencialSAT.objects.get()
        self.assertEqual(cred.rfc, RFC)
        self.assertEqual(sat.fiel_de(cred).rfc, RFC)

    def test_contrasena_mala_no_guarda(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        cer, key = fiel_de_prueba()
        r = self.client.post('/admin/contabilidad/credencialsat/add/', {
            'archivo_cer': SimpleUploadedFile('f.cer', cer), 'archivo_key': SimpleUploadedFile('f.key', key),
            'password': 'mala', 'activa': 'on'})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(CredencialSAT.objects.exists())

    def test_solo_direccion_ve_la_efirma(self):
        from django.contrib.auth.models import User
        User.objects.create_user('conta', 'c@x.com', 'x', is_staff=True)
        self.client.logout()
        self.client.force_login(User.objects.get(username='conta'))
        self.assertIn(self.client.get('/admin/contabilidad/credencialsat/').status_code, (302, 403))
