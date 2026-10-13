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


@override_settings(SAT_FIEL_ENCRYPTION_KEY=LLAVE, STORAGES=STORAGE_MEMORIA)
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


@override_settings(SAT_FIEL_ENCRYPTION_KEY=LLAVE, STORAGES=STORAGE_MEMORIA)
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
        with override_settings(SAT_FIEL_ENCRYPTION_KEY=''), self.assertRaises(sat.ConfiguracionSATError):
            sat.guardar_credencial(cer, key, CLAVE)

    def test_rechaza_efirma_vencida(self):
        cer, key = fiel_de_prueba(dias=-0)
        with self.assertRaises(ws.FielError):
            sat.guardar_credencial(cer, key, CLAVE)

    def test_renovar_reemplaza_en_vez_de_duplicar(self):
        for _ in range(2):
            sat.guardar_credencial(*fiel_de_prueba(), CLAVE)
        self.assertEqual(CredencialSAT.objects.count(), 1)


@override_settings(SAT_FIEL_ENCRYPTION_KEY=LLAVE, STORAGES=STORAGE_MEMORIA)
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

    def test_planear_no_repite_y_avanza_la_cobertura(self):
        hoy = date(2026, 10, 13)
        self.assertEqual(len(sat.planear_sincronizacion(self.cred, hoy=hoy)), 2)
        self.cred.refresh_from_db()
        self.assertEqual(self.cred.ultima_fecha_cubierta, date(2026, 10, 12))
        self.assertEqual(len(sat.planear_sincronizacion(self.cred, hoy=hoy)), 0)
        self.assertEqual(len(sat.planear_sincronizacion(self.cred, hoy=date(2026, 10, 14))), 2)
        ultima = SolicitudDescargaSAT.objects.latest('id')
        self.assertEqual(ultima.desde, date(2026, 10, 9))  # solape de 3 días


@override_settings(SAT_FIEL_ENCRYPTION_KEY=LLAVE, STORAGES=STORAGE_MEMORIA)
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
        self.assertEqual(sf.fecha_factura, date(2026, 10, 5))
        self.assertEqual(CfdiSAT.objects.get().resultado, 'VINCULADO')

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
        self.assertEqual(sat.reintentar_vinculos(), 1)
        sf.refresh_from_db()
        self.assertEqual(sf.estado, 'FACTURADA')

    def test_factura_ya_subida_por_el_contador_no_se_pisa(self):
        uuid = 'BBBBBBBB-0000-0000-0000-000000000005'
        sf = self._solicitud_factura(uuid_factura=uuid)
        self._bajar(cfdi(uuid, RFC, 'XEXX010101000'))
        reg = CfdiSAT.objects.get()
        self.assertEqual(reg.resultado, 'VINCULADO')
        self.assertEqual(reg.solicitud_factura, sf)

    def test_egreso_y_emisor_ajeno_se_omiten(self):
        self._bajar(cfdi('BBBBBBBB-0000-0000-0000-000000000006', RFC, 'XEXX010101000', tipo='E'),
                    cfdi('BBBBBBBB-0000-0000-0000-000000000007', 'OTRO010101ABC', 'XEXX010101000'))
        self.assertEqual(set(CfdiSAT.objects.values_list('resultado', flat=True)), {'OMITIDO'})


@override_settings(SAT_FIEL_ENCRYPTION_KEY=LLAVE, STORAGES=STORAGE_MEMORIA)
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
