"""
Tests del FacturamaClient (capa HTTP).

Mockean `requests.request` para no pegarle a la red real.
"""
import base64
from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase

from facturacion.services.facturama_client import (
    FacturamaClient,
    FacturamaCredentials,
    FacturamaError,
    URL_SANDBOX,
    URL_PRODUCCION,
)


def _creds(sandbox=True):
    return FacturamaCredentials(
        user="user_test",
        password="pwd_test",
        base_url=URL_SANDBOX if sandbox else URL_PRODUCCION,
        sandbox=sandbox,
    )


def _response(*, status=200, json_data=None, content=b"", text=""):
    mock = MagicMock()
    mock.status_code = status
    mock.content = content
    mock.text = text
    if json_data is not None:
        mock.json.return_value = json_data
    else:
        mock.json.side_effect = ValueError("no json")
    return mock


class CredentialsTests(SimpleTestCase):
    def test_sandbox_apunta_a_url_sandbox(self):
        creds = _creds(sandbox=True)
        self.assertEqual(creds.base_url, URL_SANDBOX)

    def test_produccion_apunta_a_url_produccion(self):
        creds = _creds(sandbox=False)
        self.assertEqual(creds.base_url, URL_PRODUCCION)


class EmitirCfdiTests(SimpleTestCase):
    @patch("facturacion.services.facturama_client.requests.request")
    def test_emitir_ok_devuelve_dict(self, mock_req):
        mock_req.return_value = _response(
            status=201,
            json_data={
                "Id": "abc-123",
                "Folio": "42",
                "Complement": {"TaxStamp": {"Uuid": "UUID-FAKE"}},
            },
            content=b"{}",
        )
        client = FacturamaClient(credentials=_creds())
        resultado = client.emitir_cfdi({"foo": "bar"})

        self.assertEqual(resultado["Id"], "abc-123")
        self.assertEqual(
            resultado["Complement"]["TaxStamp"]["Uuid"], "UUID-FAKE"
        )

        args, kwargs = mock_req.call_args
        self.assertEqual(args[0], "POST")
        self.assertIn("/api/3/cfdis", args[1])
        self.assertEqual(kwargs["auth"], ("user_test", "pwd_test"))

    @patch("facturacion.services.facturama_client.requests.request")
    def test_error_con_model_state_se_concatena(self, mock_req):
        mock_req.return_value = _response(
            status=400,
            json_data={
                "Message": "Bad Request",
                "ModelState": {"Receiver.Rfc": ["RFC inválido"]},
            },
            content=b"{}",
        )
        client = FacturamaClient(credentials=_creds())
        with self.assertRaises(FacturamaError) as ctx:
            client.emitir_cfdi({})

        self.assertIn("Bad Request", ctx.exception.message)
        self.assertIn("Receiver.Rfc", ctx.exception.message)
        self.assertEqual(ctx.exception.status_code, 400)

    @patch("facturacion.services.facturama_client.requests.request")
    def test_extrae_codigo_sat_del_mensaje(self, mock_req):
        mock_req.return_value = _response(
            status=400,
            json_data={
                "Message": "CFDI40147: El RFC del receptor no está registrado",
            },
            content=b"{}",
        )
        client = FacturamaClient(credentials=_creds())
        with self.assertRaises(FacturamaError) as ctx:
            client.emitir_cfdi({})

        self.assertEqual(ctx.exception.sat_code, "CFDI40147")

    @patch("facturacion.services.facturama_client.requests.request")
    def test_error_de_red_lanza_facturama_error(self, mock_req):
        import requests as real_requests
        mock_req.side_effect = real_requests.ConnectionError("boom")

        client = FacturamaClient(credentials=_creds())
        with self.assertRaises(FacturamaError) as ctx:
            client.emitir_cfdi({})
        self.assertIn("Error de red", ctx.exception.message)


class DescargarArchivosTests(SimpleTestCase):
    @patch("facturacion.services.facturama_client.requests.request")
    def test_descarga_pdf_directa(self, mock_req):
        mock_req.return_value = _response(
            status=200,
            content=b"%PDF-1.4 fake",
            text="binario",
        )
        client = FacturamaClient(credentials=_creds())
        pdf = client.descargar_pdf("abc-123")
        self.assertEqual(pdf, b"%PDF-1.4 fake")

    @patch("facturacion.services.facturama_client.requests.request")
    def test_descarga_pdf_base64_envuelto_en_content(self, mock_req):
        contenido_real = b"%PDF-1.4 hola"
        wrapper = {"Content": base64.b64encode(contenido_real).decode()}
        mock_req.return_value = _response(
            status=200,
            json_data=wrapper,
            content=b'{"Content": "..."}',
        )
        client = FacturamaClient(credentials=_creds())
        pdf = client.descargar_pdf("abc-123")
        self.assertEqual(pdf, contenido_real)

    @patch("facturacion.services.facturama_client.requests.request")
    def test_descarga_falla_con_404(self, mock_req):
        mock_req.return_value = _response(
            status=404,
            json_data={"Message": "CFDI no encontrado"},
            content=b"{}",
        )
        client = FacturamaClient(credentials=_creds())
        with self.assertRaises(FacturamaError) as ctx:
            client.descargar_pdf("x")
        self.assertEqual(ctx.exception.status_code, 404)


class CancelarCfdiTests(SimpleTestCase):
    @patch("facturacion.services.facturama_client.requests.request")
    def test_cancelar_incluye_motivo_en_params(self, mock_req):
        mock_req.return_value = _response(
            status=200, json_data={"Status": "canceled"}, content=b"{}"
        )
        client = FacturamaClient(credentials=_creds())
        client.cancelar_cfdi("abc-123", motivo="02")

        args, kwargs = mock_req.call_args
        self.assertEqual(args[0], "DELETE")
        self.assertEqual(kwargs["params"]["motive"], "02")
        self.assertEqual(kwargs["params"]["type"], "issued")

    @patch("facturacion.services.facturama_client.requests.request")
    def test_cancelar_con_sustituto_incluye_uuid(self, mock_req):
        mock_req.return_value = _response(
            status=200, json_data={}, content=b"{}"
        )
        client = FacturamaClient(credentials=_creds())
        client.cancelar_cfdi("abc-123", motivo="01", uuid_sustituto="UUID-NEW")

        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs["params"]["uuidReplacement"], "UUID-NEW")
