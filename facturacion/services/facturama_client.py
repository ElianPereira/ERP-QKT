"""
Cliente HTTP para la API de Facturama.

Soporta cuenta Multiemisor: una sola set de credenciales maneja múltiples
RFCs emisores. El emisor específico se elige por-CFDI en el payload
(campo `Issuer.Rfc`), no por credenciales.

Endpoints:
- Sandbox:    https://apisandbox.facturama.mx/
- Producción: https://api.facturama.mx/

Configuración (variables de entorno):
- FACTURAMA_USER:     Usuario de la cuenta Facturama API
- FACTURAMA_PASS:     Contraseña de la cuenta Facturama API
- FACTURAMA_SANDBOX:  'True' para sandbox, 'False' para producción

Referencia API: https://apisandbox.facturama.mx/guias
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests
from decouple import config

logger = logging.getLogger(__name__)


URL_SANDBOX = "https://apisandbox.facturama.mx"
URL_PRODUCCION = "https://api.facturama.mx"

TIMEOUT_DEFAULT = 30  # segundos


class FacturamaError(Exception):
    """
    Error devuelto por Facturama o el SAT al intentar emitir/consultar CFDI.

    Attributes:
        message: mensaje legible (ya traducido al español si es posible)
        status_code: código HTTP devuelto
        raw_response: payload crudo de Facturama
        sat_code: código de error CFDI del SAT si se pudo extraer (ej. 'CFDI33118')
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        raw_response: Any = None,
        sat_code: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.raw_response = raw_response
        self.sat_code = sat_code

    def __str__(self):
        if self.sat_code:
            return f"[{self.sat_code}] {self.message}"
        return self.message


@dataclass
class FacturamaCredentials:
    """Credenciales + endpoint resuelto a partir de env vars."""
    user: str
    password: str
    base_url: str
    sandbox: bool

    @classmethod
    def from_env(cls) -> "FacturamaCredentials":
        """Lee las credenciales desde variables de entorno."""
        sandbox = config("FACTURAMA_SANDBOX", default="True", cast=bool)
        user = config("FACTURAMA_USER", default="")
        password = config("FACTURAMA_PASS", default="")
        base_url = URL_SANDBOX if sandbox else URL_PRODUCCION
        return cls(
            user=user,
            password=password,
            base_url=base_url,
            sandbox=sandbox,
        )


class FacturamaClient:
    """
    Cliente HTTP para Facturama API REST.

    Uso:
        client = FacturamaClient()
        resultado = client.emitir_cfdi(payload_dict)
        # resultado contiene: Id, Complement.TaxStamp.Uuid, Folio, etc.
    """

    def __init__(
        self,
        credentials: FacturamaCredentials | None = None,
        timeout: int = TIMEOUT_DEFAULT,
    ):
        self.credentials = credentials or FacturamaCredentials.from_env()
        self.timeout = timeout

        if not self.credentials.user or not self.credentials.password:
            logger.warning(
                "FacturamaClient inicializado SIN credenciales. "
                "Las llamadas fallarán con error 401. "
                "Configura FACTURAMA_USER y FACTURAMA_PASS en el entorno."
            )

    # ─── Métodos públicos ─────────────────────────────────────

    def emitir_cfdi(self, payload: dict) -> dict:
        """
        Emite un CFDI 4.0 de tipo Ingreso.

        Args:
            payload: dict con la estructura de Facturama (Issuer, Receiver,
                     Items, etc). Ver facturama_mapper.py.

        Returns:
            dict con la respuesta de Facturama, incluyendo:
                - Id: identificador interno de Facturama
                - Folio: folio del CFDI
                - Complement.TaxStamp.Uuid: UUID del SAT
                - Cadena de sellos, fecha timbrado, etc.

        Raises:
            FacturamaError si la emisión falla.
        """
        return self._request("POST", "/api/3/cfdis", json=payload)

    def cancelar_cfdi(
        self,
        cfdi_id: str,
        motivo: str = "02",
        uuid_sustituto: str | None = None,
    ) -> dict:
        """
        Cancela un CFDI ya emitido.

        Args:
            cfdi_id: ID interno de Facturama del CFDI a cancelar
            motivo: código de motivo SAT (01, 02, 03, 04). Default '02'
                    (Comprobante emitido con errores sin relación).
            uuid_sustituto: UUID del CFDI que sustituye al cancelado
                            (obligatorio si motivo='01')

        Returns:
            dict con el acuse de cancelación.

        Raises:
            FacturamaError si la cancelación falla.
        """
        params = {"motive": motivo, "type": "issued"}
        if uuid_sustituto:
            params["uuidReplacement"] = uuid_sustituto
        return self._request("DELETE", f"/api/3/cfdis/{cfdi_id}", params=params)

    def descargar_pdf(self, cfdi_id: str) -> bytes:
        """
        Descarga el PDF de un CFDI ya emitido.

        Args:
            cfdi_id: ID interno de Facturama.

        Returns:
            bytes del PDF.

        Raises:
            FacturamaError si la descarga falla.
        """
        return self._request_raw(
            "GET",
            f"/api/cfdi/pdf/issued/{cfdi_id}",
            accept="application/pdf",
        )

    def descargar_xml(self, cfdi_id: str) -> bytes:
        """
        Descarga el XML de un CFDI ya emitido.

        Args:
            cfdi_id: ID interno de Facturama.

        Returns:
            bytes del XML.

        Raises:
            FacturamaError si la descarga falla.
        """
        return self._request_raw(
            "GET",
            f"/api/cfdi/xml/issued/{cfdi_id}",
            accept="application/xml",
        )

    # ─── Métodos internos ─────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """Request con respuesta JSON."""
        url = f"{self.credentials.base_url}{path}"
        try:
            resp = requests.request(
                method,
                url,
                json=json,
                params=params,
                auth=(self.credentials.user, self.credentials.password),
                timeout=self.timeout,
                headers={"Accept": "application/json"},
            )
        except requests.RequestException as exc:
            raise FacturamaError(
                f"Error de red al contactar Facturama: {exc}",
                raw_response=str(exc),
            ) from exc

        return self._handle_response(resp)

    def _request_raw(
        self,
        method: str,
        path: str,
        *,
        accept: str,
    ) -> bytes:
        """Request con respuesta binaria (PDF/XML)."""
        url = f"{self.credentials.base_url}{path}"
        try:
            resp = requests.request(
                method,
                url,
                auth=(self.credentials.user, self.credentials.password),
                timeout=self.timeout,
                headers={"Accept": accept},
            )
        except requests.RequestException as exc:
            raise FacturamaError(
                f"Error de red al contactar Facturama: {exc}",
                raw_response=str(exc),
            ) from exc

        if resp.status_code != 200:
            self._raise_from_error_response(resp)

        # Facturama devuelve el binario base64-encoded en un JSON {"Content": "..."}
        # para algunos endpoints; para otros lo devuelve directo. Cubrimos ambos.
        try:
            data = resp.json()
            if isinstance(data, dict) and "Content" in data:
                import base64
                return base64.b64decode(data["Content"])
        except ValueError:
            pass
        return resp.content

    def _handle_response(self, resp: requests.Response) -> dict:
        """Parsea la respuesta o lanza FacturamaError."""
        if 200 <= resp.status_code < 300:
            try:
                return resp.json() if resp.content else {}
            except ValueError:
                return {"raw": resp.text}

        self._raise_from_error_response(resp)

    def _raise_from_error_response(self, resp: requests.Response):
        """Lanza FacturamaError con el mejor mensaje posible."""
        sat_code = None
        message = f"HTTP {resp.status_code}"
        raw = None

        try:
            data = resp.json()
            raw = data
            # Facturama puede devolver varios formatos de error.
            # Intentamos extraer el más útil.
            if isinstance(data, dict):
                # Formato típico: {"Message": "...", "ModelState": {"campo": ["..."]}}
                if "Message" in data:
                    message = str(data["Message"])
                if "ModelState" in data and isinstance(data["ModelState"], dict):
                    errores = []
                    for campo, msgs in data["ModelState"].items():
                        if isinstance(msgs, list):
                            errores.append(f"{campo}: {'; '.join(map(str, msgs))}")
                        else:
                            errores.append(f"{campo}: {msgs}")
                    if errores:
                        message = f"{message} | {' | '.join(errores)}"
                # Formato error SAT: {"Message": "CFDI33118: Descripción..."}
                import re
                m = re.search(r"CFDI\d{5}", message)
                if m:
                    sat_code = m.group(0)
        except ValueError:
            message = resp.text or message
            raw = resp.text

        logger.error(
            "Facturama error %s: %s",
            resp.status_code,
            message,
        )

        raise FacturamaError(
            message=message,
            status_code=resp.status_code,
            raw_response=raw,
            sat_code=sat_code,
        )
