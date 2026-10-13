"""Representación impresa (PDF) de un CFDI timbrado, generada desde su XML.

El SAT solo entrega el XML en la descarga masiva; el PDF lo arma el ERP con lo
que el propio comprobante trae (emisor, receptor, conceptos, impuestos, timbre,
sellos y cadena original del complemento de certificación) más el código QR de
verificación. Todo sale del XML: ningún importe se recalcula aquí.
"""
import base64
import io
from decimal import ROUND_HALF_UP, Decimal

import defusedxml.ElementTree as ET
import qrcode

from core_erp.documentos import nombre_archivo, render_pdf

from .choices import FormaPago, MetodoPago, RegimenFiscal, UsoCFDI

NS_TFD = '{http://www.sat.gob.mx/TimbreFiscalDigital}'
URL_VERIFICACION = 'https://verificacfdi.facturaelectronica.sat.gob.mx/default.aspx'
TIPOS = {'I': 'Ingreso', 'E': 'Egreso', 'T': 'Traslado', 'N': 'Nómina', 'P': 'Pago'}
IMPUESTOS = {'001': 'ISR', '002': 'IVA', '003': 'IEPS'}


class CfdiInvalidoError(ValueError):
    """El XML no es un CFDI timbrado legible."""


def _dec(valor):
    try:
        return Decimal(str(valor or '0'))
    except ArithmeticError:
        return Decimal('0')


def _etiqueta(choices, clave):
    """«clave – descripción»; los catálogos del ERP ya antepone la clave en algunas etiquetas."""
    if not clave:
        return ''
    texto = dict(choices.choices).get(clave, '')
    if not texto:
        return clave
    return texto if texto.startswith(clave) else f'{clave} – {texto}'


def _decimal_tasa(valor):
    t = _dec(valor)
    return f'{(t * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)}%' if t else ''


def leer_cfdi_completo(xml):
    """Diccionario con todo lo que imprime el PDF. Lanza `CfdiInvalidoError`."""
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError as e:
        raise CfdiInvalidoError("XML ilegible.") from e
    ns = '{http://www.sat.gob.mx/cfd/3}' if 'cfd/3' in raiz.tag else '{http://www.sat.gob.mx/cfd/4}'
    emisor, receptor = raiz.find(f'{ns}Emisor'), raiz.find(f'{ns}Receptor')
    timbre = next((e for e in raiz.iter() if e.tag == f'{NS_TFD}TimbreFiscalDigital'), None)
    if emisor is None or receptor is None or timbre is None:
        raise CfdiInvalidoError("El XML no es un CFDI timbrado.")

    conceptos = []
    for c in raiz.findall(f'{ns}Conceptos/{ns}Concepto'):
        conceptos.append({
            'clave': c.get('ClaveProdServ', ''), 'cantidad': c.get('Cantidad', ''),
            'unidad': f"{c.get('ClaveUnidad', '')} {c.get('Unidad', '')}".strip(),
            'descripcion': c.get('Descripcion', ''), 'valor_unitario': _dec(c.get('ValorUnitario')),
            'descuento': _dec(c.get('Descuento')), 'importe': _dec(c.get('Importe')),
        })

    impuestos = []
    nodo_imp = raiz.find(f'{ns}Impuestos')
    if nodo_imp is not None:
        for tipo, ruta in (('Traslado', f'{ns}Traslados/{ns}Traslado'), ('Retención', f'{ns}Retenciones/{ns}Retencion')):
            for i in nodo_imp.findall(ruta):
                impuestos.append({
                    'tipo': tipo, 'nombre': IMPUESTOS.get(i.get('Impuesto', ''), i.get('Impuesto', '')),
                    'tasa': _decimal_tasa(i.get('TasaOCuota')), 'importe': _dec(i.get('Importe')),
                    'retencion': tipo != 'Traslado'})
    for loc in raiz.iter():  # Complemento de impuestos locales (ISH)
        if isinstance(loc.tag, str) and loc.tag.endswith('}ImpLocTrasladado'):
            impuestos.append({'tipo': 'Traslado local', 'nombre': loc.get('ImpLocTrasladado', ''),
                              'tasa': f"{loc.get('TasadeTraslado', '')}%", 'importe': _dec(loc.get('Importe')),
                              'retencion': False})

    sello_cfd, sello_sat = timbre.get('SelloCFD', ''), timbre.get('SelloSAT', '')
    cadena = '||{}|{}|{}|{}|{}|{}||'.format(
        timbre.get('Version', '1.1'), timbre.get('UUID', ''), timbre.get('FechaTimbrado', ''),
        timbre.get('RfcProvCertif', ''), sello_cfd, timbre.get('NoCertificadoSAT', ''))
    total = _dec(raiz.get('Total'))
    return {
        'uuid': timbre.get('UUID', '').upper(), 'serie_folio': f"{raiz.get('Serie', '')}{raiz.get('Folio', '')}",
        'version': raiz.get('Version', ''), 'tipo': TIPOS.get(raiz.get('TipoDeComprobante', ''), raiz.get('TipoDeComprobante', '')),
        'fecha': raiz.get('Fecha', '').replace('T', ' '), 'lugar_expedicion': raiz.get('LugarExpedicion', ''),
        'moneda': raiz.get('Moneda', 'MXN'), 'exportacion': raiz.get('Exportacion', ''),
        'no_certificado': raiz.get('NoCertificado', ''),
        'forma_pago': _etiqueta(FormaPago, raiz.get('FormaPago', '')),
        'metodo_pago': _etiqueta(MetodoPago, raiz.get('MetodoPago', '')),
        'condiciones': raiz.get('CondicionesDePago', ''),
        'emisor': {'rfc': emisor.get('Rfc', ''), 'nombre': emisor.get('Nombre', ''),
                   'regimen': _etiqueta(RegimenFiscal, emisor.get('RegimenFiscal', ''))},
        'receptor': {'rfc': receptor.get('Rfc', ''), 'nombre': receptor.get('Nombre', ''),
                     'regimen': _etiqueta(RegimenFiscal, receptor.get('RegimenFiscalReceptor', '')),
                     'uso': _etiqueta(UsoCFDI, receptor.get('UsoCFDI', '')),
                     'cp': receptor.get('DomicilioFiscalReceptor', '')},
        'conceptos': conceptos, 'impuestos': impuestos,
        'subtotal': _dec(raiz.get('SubTotal')), 'descuento': _dec(raiz.get('Descuento')), 'total': total,
        'fecha_timbrado': timbre.get('FechaTimbrado', '').replace('T', ' '),
        'no_certificado_sat': timbre.get('NoCertificadoSAT', ''), 'rfc_prov_certif': timbre.get('RfcProvCertif', ''),
        'sello_cfd': sello_cfd, 'sello_sat': sello_sat, 'cadena_original': cadena,
        'qr': _qr_data_uri(timbre.get('UUID', ''), emisor.get('Rfc', ''), receptor.get('Rfc', ''), total, sello_cfd),
    }


def _qr_data_uri(uuid, rfc_emisor, rfc_receptor, total, sello_cfd):
    """QR de verificación del SAT: id, re, rr, tt (18 dígitos, 6 decimales) y fe (8 últimos del sello)."""
    tt = f'{total:017.6f}'
    url = f'{URL_VERIFICACION}?id={uuid}&re={rfc_emisor}&rr={rfc_receptor}&tt={tt}&fe={sello_cfd[-8:]}'
    buf = io.BytesIO()
    qrcode.make(url, box_size=4, border=1).save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def generar_pdf_cfdi(xml):
    """Bytes del PDF y nombre de archivo (`QKT_Factura_<serie-folio|uuid>.pdf`)."""
    datos = leer_cfdi_completo(xml)
    pdf = render_pdf('facturacion/cfdi_pdf.html', {'titulo': 'Factura (CFDI)', 'cfdi': datos})
    return pdf, nombre_archivo('Factura', datos['serie_folio'] or datos['uuid'][:8])
