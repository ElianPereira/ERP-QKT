"""Cliente del Servicio de Descarga Masiva del SAT (SOAP + e.firma).

Capa de protocolo pura: no conoce modelos del ERP. Las cuatro operaciones del
servicio (Autentica, Solicita, Verifica, Descargar) viajan firmadas con la
e.firma (FIEL) del contribuyente; el SAT valida RSA-SHA1 + SHA-1 sobre XML
canónico exclusivo, por eso aquí SHA-1 es obligatorio del protocolo, no una
elección (no se usa para nada de seguridad propia del ERP).

Referencia de los contratos: documentación técnica del SAT v1.5 y la
implementación abierta phpcfdi/sat-ws-descarga-masiva (endpoints, SOAPAction y
estructura de las firmas).
"""
import base64
import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from urllib.parse import unquote

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from lxml import etree

URL_AUTENTICA = 'https://cfdidescargamasivasolicitud.clouda.sat.gob.mx/Autenticacion/Autenticacion.svc'
URL_SOLICITA = 'https://cfdidescargamasivasolicitud.clouda.sat.gob.mx/SolicitaDescargaService.svc'
URL_VERIFICA = 'https://cfdidescargamasivasolicitud.clouda.sat.gob.mx/VerificaSolicitudDescargaService.svc'
URL_DESCARGA = 'https://cfdidescargamasiva.clouda.sat.gob.mx/DescargaMasivaService.svc'

ACCION_AUTENTICA = 'http://DescargaMasivaTerceros.gob.mx/IAutenticacion/Autentica'
ACCION_SOLICITA = 'http://DescargaMasivaTerceros.sat.gob.mx/ISolicitaDescargaService/{}'
ACCION_VERIFICA = ('http://DescargaMasivaTerceros.sat.gob.mx/IVerificaSolicitudDescargaService/'
                   'VerificaSolicitudDescarga')
ACCION_DESCARGA = 'http://DescargaMasivaTerceros.sat.gob.mx/IDescargaMasivaTercerosService/Descargar'

NS_SOAP = 'http://schemas.xmlsoap.org/soap/envelope/'
NS_DES = 'http://DescargaMasivaTerceros.sat.gob.mx'
NS_DES_AUTH = 'http://DescargaMasivaTerceros.gob.mx'
NS_DS = 'http://www.w3.org/2000/09/xmldsig#'
NS_WSU = 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd'
NS_WSSE = 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd'
ALG_C14N = 'http://www.w3.org/2001/10/xml-exc-c14n#'
ALG_ENVELOPED = 'http://www.w3.org/2000/09/xmldsig#enveloped-signature'
ALG_RSA_SHA1 = 'http://www.w3.org/2000/09/xmldsig#rsa-sha1'
ALG_SHA1 = 'http://www.w3.org/2000/09/xmldsig#sha1'
VT_X509 = 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-x509-token-profile-1.0#X509v3'
ET_B64 = 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary'

COD_OK = '5000'
COD_SIN_INFORMACION = '5004'
COD_CUOTA_AGOTADA = '5002'  # solicitudes de por vida agotadas para ese rango exacto
# Códigos que el SAT devuelve cuando el servicio falla por su lado (no es un rechazo
# de la petición): se reintenta en la siguiente corrida.
CODIGOS_TRANSITORIOS = frozenset({'404', '5006'})
# Estados de la solicitud en el SAT (EstadoSolicitud de VerificaSolicitudDescarga).
SAT_ACEPTADA, SAT_EN_PROCESO, SAT_TERMINADA, SAT_ERROR, SAT_RECHAZADA, SAT_VENCIDA = 1, 2, 3, 4, 5, 6

TIMEOUT = 60


class SatError(Exception):
    """Fallo de comunicación o de protocolo con el SAT."""


class FielError(Exception):
    """e.firma inválida: archivos corruptos, contraseña errónea o vencida."""


class Fiel:
    """Certificado + llave privada de la e.firma, ya validados entre sí."""

    def __init__(self, cer_der, key_der, password):
        if isinstance(password, str):
            password = password.encode()
        try:
            self.cert = x509.load_der_x509_certificate(bytes(cer_der))
        except ValueError as e:
            raise FielError("El archivo .cer no es un certificado válido.") from e
        try:
            self.key = serialization.load_der_private_key(bytes(key_der), password=password or None)
        except (ValueError, TypeError) as e:
            raise FielError("No se pudo abrir el archivo .key: revisa la contraseña de la e.firma.") from e
        pub_cert = self.cert.public_key().public_numbers()
        pub_key = self.key.public_key().public_numbers()
        if pub_cert != pub_key:
            raise FielError("El .cer y el .key no son pareja (no pertenecen a la misma e.firma).")

    @property
    def rfc(self):
        """RFC del titular: el subject trae `RFC / CURP` en x500UniqueIdentifier."""
        valor = ''
        for attr in self.cert.subject:
            if attr.oid.dotted_string == '2.5.4.45':
                valor = attr.value
        return valor.split('/')[0].strip().upper()

    @property
    def vigente_hasta(self):
        return self.cert.not_valid_after_utc.date()

    @property
    def vencida(self):
        return self.cert.not_valid_after_utc <= datetime.now(dt_timezone.utc)

    @property
    def certificado_b64(self):
        return base64.b64encode(self.cert.public_bytes(serialization.Encoding.DER)).decode()

    def firmar(self, datos):
        firma = self.key.sign(datos, padding.PKCS1v15(), hashes.SHA1())  # noqa: S303 - exigido por el SAT
        return base64.b64encode(firma).decode()


# ─── XML / firma ─────────────────────────────────────────────

def _c14n(elemento):
    return etree.tostring(elemento, method='c14n', exclusive=True, with_comments=False)


def _sha1_b64(datos):
    h = hashes.Hash(hashes.SHA1())  # noqa: S303 - exigido por el SAT
    h.update(datos)
    return base64.b64encode(h.finalize()).decode()


def _firma(fiel, referenciado, uri, transformaciones, key_info):
    """Arma `<Signature>` sobre `referenciado` (aún sin la firma dentro)."""
    sig = etree.Element(f'{{{NS_DS}}}Signature', nsmap={None: NS_DS})
    info = etree.SubElement(sig, f'{{{NS_DS}}}SignedInfo')
    etree.SubElement(info, f'{{{NS_DS}}}CanonicalizationMethod', Algorithm=ALG_C14N)
    etree.SubElement(info, f'{{{NS_DS}}}SignatureMethod', Algorithm=ALG_RSA_SHA1)
    ref = etree.SubElement(info, f'{{{NS_DS}}}Reference', URI=uri)
    trans = etree.SubElement(ref, f'{{{NS_DS}}}Transforms')
    for alg in transformaciones:
        etree.SubElement(trans, f'{{{NS_DS}}}Transform', Algorithm=alg)
    etree.SubElement(ref, f'{{{NS_DS}}}DigestMethod', Algorithm=ALG_SHA1)
    etree.SubElement(ref, f'{{{NS_DS}}}DigestValue').text = _sha1_b64(_c14n(referenciado))
    valor = etree.SubElement(sig, f'{{{NS_DS}}}SignatureValue')
    valor.text = fiel.firmar(_c14n(info))
    sig.append(key_info)
    return sig


def _key_info_x509(fiel):
    ki = etree.Element(f'{{{NS_DS}}}KeyInfo')
    x509d = etree.SubElement(ki, f'{{{NS_DS}}}X509Data')
    isr = etree.SubElement(x509d, f'{{{NS_DS}}}X509IssuerSerial')
    etree.SubElement(isr, f'{{{NS_DS}}}X509IssuerName').text = fiel.cert.issuer.rfc4514_string()
    etree.SubElement(isr, f'{{{NS_DS}}}X509SerialNumber').text = str(fiel.cert.serial_number)
    etree.SubElement(x509d, f'{{{NS_DS}}}X509Certificate').text = fiel.certificado_b64
    return ki


def _sobre_firmado(fiel, operacion, nodo, atributos):
    """Sobre SOAP `<des:operacion><des:nodo attrs><Signature/></des:nodo>`."""
    env = etree.Element(f'{{{NS_SOAP}}}Envelope', nsmap={'s': NS_SOAP, 'des': NS_DES})  # sin prefijo para DS: lxml lo reutilizaría y cambiaría el XML firmado
    etree.SubElement(env, f'{{{NS_SOAP}}}Header')
    cuerpo = etree.SubElement(env, f'{{{NS_SOAP}}}Body')
    op = etree.SubElement(cuerpo, f'{{{NS_DES}}}{operacion}')
    el = etree.SubElement(op, f'{{{NS_DES}}}{nodo}')
    for k in sorted(atributos):
        if atributos[k] not in (None, ''):
            el.set(k, str(atributos[k]))
    el.append(_firma(fiel, el, '', [ALG_ENVELOPED], _key_info_x509(fiel)))
    return etree.tostring(env, xml_declaration=False, encoding='utf-8')


def sobre_autenticacion(fiel, ahora=None):
    ahora = ahora or datetime.now(dt_timezone.utc)
    creado = ahora.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    expira = (ahora + timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    id_token = f'uuid-{uuid.uuid4()}-1'
    env = etree.Element(f'{{{NS_SOAP}}}Envelope', nsmap={'s': NS_SOAP, 'u': NS_WSU})
    cab = etree.SubElement(env, f'{{{NS_SOAP}}}Header')
    seg = etree.SubElement(cab, f'{{{NS_WSSE}}}Security', nsmap={'o': NS_WSSE})
    seg.set(f'{{{NS_SOAP}}}mustUnderstand', '1')
    ts = etree.SubElement(seg, f'{{{NS_WSU}}}Timestamp')
    ts.set(f'{{{NS_WSU}}}Id', '_0')
    etree.SubElement(ts, f'{{{NS_WSU}}}Created').text = creado
    etree.SubElement(ts, f'{{{NS_WSU}}}Expires').text = expira
    bst = etree.SubElement(seg, f'{{{NS_WSSE}}}BinarySecurityToken', ValueType=VT_X509, EncodingType=ET_B64)
    bst.set(f'{{{NS_WSU}}}Id', id_token)
    bst.text = fiel.certificado_b64
    ki = etree.Element(f'{{{NS_DS}}}KeyInfo')
    etree.SubElement(etree.SubElement(ki, f'{{{NS_WSSE}}}SecurityTokenReference'),
                     f'{{{NS_WSSE}}}Reference', URI=f'#{id_token}', ValueType=VT_X509)
    seg.append(_firma(fiel, ts, '#_0', [ALG_C14N], ki))
    cuerpo = etree.SubElement(env, f'{{{NS_SOAP}}}Body')
    etree.SubElement(cuerpo, f'{{{NS_DES_AUTH}}}Autentica', nsmap={None: NS_DES_AUTH})
    return etree.tostring(env, xml_declaration=False, encoding='utf-8')


def _analizar(contenido):
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        return etree.fromstring(contenido, parser)
    except etree.XMLSyntaxError as e:
        raise SatError("Respuesta del SAT ilegible.") from e


def _buscar(raiz, nombre):
    for el in raiz.iter():
        if isinstance(el.tag, str) and etree.QName(el).localname.lower() == nombre.lower():
            return el
    return None


def _atributos(el):
    return {} if el is None else {etree.QName(k).localname.lower(): v for k, v in el.attrib.items()}


def _falla(raiz):
    f = _buscar(raiz, 'faultstring')
    return (f.text or '').strip() if f is not None else ''


def _fecha_sat(valor):
    return valor.strftime('%Y-%m-%dT%H:%M:%S')


class ClienteSAT:
    """Una sesión del servicio: autentica una vez y reutiliza el token."""

    def __init__(self, fiel, http=None):
        self.fiel = fiel
        self.http = http or requests
        self._token = None
        self._token_hasta = None

    def _post(self, url, accion, cuerpo, token=None):
        cabeceras = {'Content-Type': 'text/xml;charset=UTF-8', 'Accept': 'text/xml', 'SOAPAction': accion,
                     'Cache-Control': 'no-cache'}
        if token:
            cabeceras['Authorization'] = f'WRAP access_token="{unquote(token)}"'
        try:
            r = self.http.post(url, data=cuerpo, headers=cabeceras, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise SatError(f"No se pudo contactar al SAT: {e}") from e
        raiz = _analizar(r.content)
        falla = _falla(raiz)
        if falla:
            raise SatError(f"El SAT rechazó la petición: {falla}")
        if r.status_code >= 400:
            raise SatError(f"El SAT respondió HTTP {r.status_code}.")
        return raiz

    def autenticar(self):
        ahora = datetime.now(dt_timezone.utc)
        if self._token and self._token_hasta and ahora < self._token_hasta:
            return self._token
        raiz = self._post(URL_AUTENTICA, ACCION_AUTENTICA, sobre_autenticacion(self.fiel, ahora))
        token = _buscar(raiz, 'AutenticaResult')
        if token is None or not (token.text or '').strip():
            raise SatError("El SAT no devolvió token de autenticación.")
        self._token = token.text.strip()
        self._token_hasta = ahora + timedelta(minutes=4)
        return self._token

    def solicitar(self, tipo, desde, hasta):
        """`tipo`: 'EMITIDAS' o 'RECIBIDAS'. Solo CFDI vigentes (el SAT no entrega
        XML de recibidos cancelados). Devuelve dict(id_solicitud, codigo, mensaje)."""
        rfc = self.fiel.rfc
        atributos = {
            'RfcSolicitante': rfc,
            'FechaInicial': _fecha_sat(desde),
            'FechaFinal': _fecha_sat(hasta),
            'TipoSolicitud': 'CFDI',
            'EstadoComprobante': 'Vigente',
        }
        if tipo == 'EMITIDAS':
            atributos['RfcEmisor'] = rfc
            operacion = 'SolicitaDescargaEmitidos'
        else:
            atributos['RfcReceptor'] = rfc
            operacion = 'SolicitaDescargaRecibidos'
        cuerpo = _sobre_firmado(self.fiel, operacion, 'solicitud', atributos)
        raiz = self._post(URL_SOLICITA, ACCION_SOLICITA.format(operacion), cuerpo, self.autenticar())
        r = _atributos(_buscar(raiz, f'{operacion}Result'))
        return {'id_solicitud': r.get('idsolicitud', ''), 'codigo': r.get('codestatus', ''),
                'mensaje': r.get('mensaje', '')}

    def verificar(self, id_solicitud):
        cuerpo = _sobre_firmado(self.fiel, 'VerificaSolicitudDescarga', 'solicitud',
                                {'IdSolicitud': id_solicitud, 'RfcSolicitante': self.fiel.rfc})
        raiz = self._post(URL_VERIFICA, ACCION_VERIFICA, cuerpo, self.autenticar())
        el = _buscar(raiz, 'VerificaSolicitudDescargaResult')
        r = _atributos(el)
        paquetes = [(p.text or '').strip() for p in (el.iter() if el is not None else [])
                    if isinstance(p.tag, str) and etree.QName(p).localname == 'IdsPaquetes' and (p.text or '').strip()]
        try:
            estado = int(r.get('estadosolicitud', '0'))
        except ValueError:
            estado = 0
        return {'codigo': r.get('codestatus', ''), 'estado': estado,
                'codigo_estado': r.get('codigoestadosolicitud', ''), 'numero_cfdis': int(r.get('numerocfdis', '0') or 0),
                'mensaje': r.get('mensaje', ''), 'paquetes': paquetes}

    def descargar(self, id_paquete):
        cuerpo = _sobre_firmado(self.fiel, 'PeticionDescargaMasivaTercerosEntrada', 'peticionDescarga',
                                {'IdPaquete': id_paquete, 'RfcSolicitante': self.fiel.rfc})
        raiz = self._post(URL_DESCARGA, ACCION_DESCARGA, cuerpo, self.autenticar())
        r = _atributos(_buscar(raiz, 'respuesta'))
        paquete = _buscar(raiz, 'Paquete')
        zip_bytes = base64.b64decode(paquete.text) if paquete is not None and paquete.text else b''
        return {'codigo': r.get('codestatus', ''), 'mensaje': r.get('mensaje', ''), 'zip': zip_bytes}
