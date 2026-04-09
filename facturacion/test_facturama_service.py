"""
Tests del orquestador facturama_service.

Trabajamos con un SolicitudFactura mockeado (SimpleNamespace) y un
FacturamaClient mockeado, para aislar la lógica del orquestador sin
tocar BD ni Cloudinary. La parte de persistencia (_persistir_resultado)
se testea aparte con mocks puntuales.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase

from facturacion.services import facturama_service
from facturacion.services.facturama_client import FacturamaError
from facturacion.services.facturama_service import (
    emitir_cfdi_desde_solicitud,
    SolicitudNoFacturableError,
    _extraer_uuid,
    _validar_solicitud,
)


def _solicitud_valida(**overrides):
    defaults = dict(
        pk=1,
        estado="PENDIENTE",
        uuid_factura="",
        emisor=SimpleNamespace(
            pk=1,
            rfc="PECE010202IA0",
            nombre_interno="Eventos Elian",
            razon_social="ELIAN DE JESUS PEREIRA CEH",
            regimen_fiscal="626",
            codigo_postal="97238",
            lugar_expedicion="",
            lugar_expedicion_efectivo="97238",
            serie_folio="A",
            activo=True,
            unidad_negocio=SimpleNamespace(clave="EVENTOS"),
        ),
        rfc="CACX7605101P8",
        razon_social="XOCHITL CASAS HERNANDEZ",
        codigo_postal="06600",
        regimen_fiscal="612",
        uso_cfdi="G03",
        forma_pago="03",
        metodo_pago="PUE",
        subtotal=Decimal("1000.00"),
        iva=Decimal("160.00"),
        retencion_isr=Decimal("0.00"),
        retencion_iva=Decimal("0.00"),
        concepto="Servicio de evento",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class ValidarSolicitudTests(SimpleTestCase):
    def test_solicitud_valida_no_lanza(self):
        _validar_solicitud(_solicitud_valida())

    def test_sin_pk_lanza(self):
        with self.assertRaises(SolicitudNoFacturableError):
            _validar_solicitud(_solicitud_valida(pk=None))

    def test_cancelada_lanza(self):
        with self.assertRaises(SolicitudNoFacturableError):
            _validar_solicitud(_solicitud_valida(estado="CANCELADA"))

    def test_ya_timbrada_lanza(self):
        with self.assertRaises(SolicitudNoFacturableError):
            _validar_solicitud(_solicitud_valida(uuid_factura="UUID-YA"))

    def test_sin_emisor_lanza(self):
        with self.assertRaises(SolicitudNoFacturableError):
            _validar_solicitud(_solicitud_valida(emisor=None))

    def test_emisor_inactivo_lanza(self):
        sol = _solicitud_valida()
        sol.emisor.activo = False
        with self.assertRaises(SolicitudNoFacturableError):
            _validar_solicitud(sol)

    def test_campos_receptor_faltantes(self):
        with self.assertRaises(SolicitudNoFacturableError) as ctx:
            _validar_solicitud(_solicitud_valida(rfc=""))
        self.assertIn("rfc", ctx.exception.message.lower())


class ExtraerUuidTests(SimpleTestCase):
    def test_uuid_anidado(self):
        resp = {"Complement": {"TaxStamp": {"Uuid": "X-Y-Z"}}}
        self.assertEqual(_extraer_uuid(resp), "X-Y-Z")

    def test_uuid_plano(self):
        self.assertEqual(_extraer_uuid({"Uuid": "PLANO"}), "PLANO")

    def test_sin_uuid_devuelve_cadena_vacia(self):
        self.assertEqual(_extraer_uuid({}), "")


class EmitirCfdiDesdeSolicitudTests(SimpleTestCase):

    def _mock_client(self, *, emit_response=None, pdf=b"PDF", xml=b"<xml/>"):
        client = MagicMock()
        client.emitir_cfdi.return_value = emit_response or {
            "Id": "abc-123",
            "Folio": "42",
            "Serie": "A",
            "Complement": {"TaxStamp": {"Uuid": "UUID-OK"}},
        }
        client.descargar_pdf.return_value = pdf
        client.descargar_xml.return_value = xml
        return client

    @patch.object(facturama_service, "_persistir_resultado")
    def test_emision_exitosa_persiste_y_devuelve_resultado(self, mock_persist):
        sol = _solicitud_valida()
        client = self._mock_client()

        resultado = emitir_cfdi_desde_solicitud(sol, client=client)

        self.assertEqual(resultado.cfdi_id, "abc-123")
        self.assertEqual(resultado.uuid, "UUID-OK")
        self.assertEqual(resultado.folio, "42")

        client.emitir_cfdi.assert_called_once()
        payload = client.emitir_cfdi.call_args[0][0]
        self.assertEqual(payload["Issuer"]["Rfc"], "PECE010202IA0")
        self.assertEqual(payload["Receiver"]["Rfc"], "CACX7605101P8")

        client.descargar_pdf.assert_called_once_with("abc-123")
        client.descargar_xml.assert_called_once_with("abc-123")

        mock_persist.assert_called_once()
        kwargs = mock_persist.call_args.kwargs
        self.assertEqual(kwargs["uuid"], "UUID-OK")
        self.assertEqual(kwargs["pdf_bytes"], b"PDF")
        self.assertEqual(kwargs["xml_bytes"], b"<xml/>")

    @patch.object(facturama_service, "_persistir_resultado")
    def test_facturama_error_traduce_mensaje(self, mock_persist):
        sol = _solicitud_valida()
        client = MagicMock()
        client.emitir_cfdi.side_effect = FacturamaError(
            "CFDI40147: RFC no válido", status_code=400
        )

        with self.assertRaises(FacturamaError) as ctx:
            emitir_cfdi_desde_solicitud(sol, client=client)

        # El mensaje fue traducido por sat_errors
        self.assertIn("CFDI40147", ctx.exception.message)
        self.assertIn("Constancia", ctx.exception.message)
        mock_persist.assert_not_called()

    @patch.object(facturama_service, "_marcar_emitida_sin_archivos")
    def test_error_descargando_archivos_marca_emisiion_parcial(self, mock_marcar):
        sol = _solicitud_valida()
        client = self._mock_client()
        client.descargar_pdf.side_effect = FacturamaError(
            "500: Internal error", status_code=500
        )

        with self.assertRaises(FacturamaError) as ctx:
            emitir_cfdi_desde_solicitud(sol, client=client)

        self.assertIn("UUID-OK", ctx.exception.message)
        mock_marcar.assert_called_once()

    def test_sin_id_en_respuesta_lanza(self):
        sol = _solicitud_valida()
        client = MagicMock()
        client.emitir_cfdi.return_value = {"Folio": "42"}  # sin Id

        with self.assertRaises(FacturamaError):
            emitir_cfdi_desde_solicitud(sol, client=client)

    def test_solicitud_no_facturable_no_llama_client(self):
        sol = _solicitud_valida(uuid_factura="YA-TIMBRADA")
        client = MagicMock()
        with self.assertRaises(SolicitudNoFacturableError):
            emitir_cfdi_desde_solicitud(sol, client=client)
        client.emitir_cfdi.assert_not_called()

    def test_datos_receptor_incompletos_no_llama_client(self):
        sol = _solicitud_valida(codigo_postal="")
        client = MagicMock()
        with self.assertRaises(SolicitudNoFacturableError):
            emitir_cfdi_desde_solicitud(sol, client=client)
        client.emitir_cfdi.assert_not_called()
