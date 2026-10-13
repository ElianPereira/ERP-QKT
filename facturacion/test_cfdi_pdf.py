"""PDF de la factura emitida, armado solo con lo que trae el XML timbrado."""
import io

import pdfplumber
from django.test import SimpleTestCase

from .services_cfdi_pdf import CfdiInvalidoError, generar_pdf_cfdi, leer_cfdi_completo

SELLO = 'A' * 340 + 'ZZZZ1234'
XML = f'''<?xml version="1.0" encoding="utf-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" Version="4.0" Serie="A" Folio="15"
  Fecha="2026-10-05T10:00:00" FormaPago="03" MetodoPago="PUE" SubTotal="1000.00" Moneda="MXN" Total="1160.00"
  TipoDeComprobante="I" LugarExpedicion="97390" NoCertificado="30001000000500003416" Exportacion="01">
  <cfdi:Emisor Rfc="PECE010202IA0" Nombre="QUINTA KOOX TANIL" RegimenFiscal="626"/>
  <cfdi:Receptor Rfc="XEXX010101000" Nombre="CLIENTE PRUEBA SA" DomicilioFiscalReceptor="97000"
    RegimenFiscalReceptor="601" UsoCFDI="G03"/>
  <cfdi:Conceptos>
    <cfdi:Concepto ClaveProdServ="90111500" Cantidad="1" ClaveUnidad="E48" Unidad="Servicio"
      Descripcion="Renta de salón para evento" ValorUnitario="1000.00" Importe="1000.00"/>
  </cfdi:Conceptos>
  <cfdi:Impuestos TotalImpuestosTrasladados="160.00">
    <cfdi:Traslados><cfdi:Traslado Base="1000.00" Impuesto="002" TipoFactor="Tasa" TasaOCuota="0.160000" Importe="160.00"/></cfdi:Traslados>
  </cfdi:Impuestos>
  <cfdi:Complemento>
    <tfd:TimbreFiscalDigital xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="1.1"
      UUID="cccccccc-0000-0000-0000-000000000001" FechaTimbrado="2026-10-05T10:00:05" RfcProvCertif="SPR190613I52"
      SelloCFD="{SELLO}" NoCertificadoSAT="30001000000500003456" SelloSAT="{'B' * 340}"/>
  </cfdi:Complemento>
</cfdi:Comprobante>'''.encode()


class CfdiPdfTest(SimpleTestCase):
    def test_lee_lo_que_imprime(self):
        d = leer_cfdi_completo(XML)
        self.assertEqual(d['uuid'], 'CCCCCCCC-0000-0000-0000-000000000001')
        self.assertEqual(d['serie_folio'], 'A15')
        self.assertEqual(str(d['total']), '1160.00')
        self.assertEqual(d['impuestos'][0]['nombre'], 'IVA')
        self.assertEqual(d['impuestos'][0]['tasa'], '16.00%')
        self.assertTrue(d['forma_pago'].startswith('03 -'), d['forma_pago'])
        self.assertTrue(d['cadena_original'].startswith('||1.1|cccccccc'))
        self.assertTrue(d['qr'].startswith('data:image/png;base64,'))

    def test_pdf_trae_los_elementos_obligatorios(self):
        pdf, nombre = generar_pdf_cfdi(XML)
        self.assertEqual(nombre, 'QKT_Factura_A15.pdf')
        self.assertTrue(pdf.startswith(b'%PDF'))
        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            texto = ' '.join(p.extract_text() or '' for p in doc.pages)
            self.assertTrue(any(p.images for p in doc.pages), "falta el QR")
        for esperado in ('CCCCCCCC-0000-0000-0000-000000000001', 'PECE010202IA0', 'CLIENTE PRUEBA SA',
                         'Renta de salón para evento', '$1,160.00', 'IVA 16.00%', 'Sello digital del SAT',
                         'representación impresa de un CFDI'):
            self.assertIn(esperado, texto)

    def test_xml_sin_timbre_se_rechaza(self):
        with self.assertRaises(CfdiInvalidoError):
            generar_pdf_cfdi(XML.replace(b'TimbreFiscalDigital', b'Otro'))
        with self.assertRaises(CfdiInvalidoError):
            generar_pdf_cfdi(b'no es xml')
