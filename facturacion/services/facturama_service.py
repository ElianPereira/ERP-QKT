"""
Orquestador de emisión de CFDI vía Facturama.

Esta capa es la única que debería usarse desde views, admin actions o
signals. Se encarga de:

1. Validar que la SolicitudFactura esté lista para facturar.
2. Construir el payload con `facturama_mapper.solicitud_a_payload_facturama`.
3. Llamar a `FacturamaClient.emitir_cfdi`.
4. Descargar PDF y XML.
5. Guardarlos en los FileFields de la solicitud (Cloudinary).
6. Actualizar campos derivados (UUID, fecha factura, estado).
7. Traducir errores del SAT a mensajes accionables.

Todo dentro de una transacción atómica para evitar estados inconsistentes
(por ejemplo: CFDI emitido en Facturama pero ERP con estado 'PENDIENTE').

Uso:
    from facturacion.services.facturama_service import emitir_cfdi_desde_solicitud

    try:
        emitir_cfdi_desde_solicitud(solicitud)
    except FacturamaError as exc:
        messages.error(request, exc.message)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from facturacion.services.facturama_client import (
    FacturamaClient,
    FacturamaError,
)
from facturacion.services.facturama_mapper import solicitud_a_payload_facturama
from facturacion.services.sat_errors import traducir_error

logger = logging.getLogger(__name__)


@dataclass
class ResultadoEmision:
    """Respuesta enriquecida tras emitir un CFDI."""
    solicitud_id: int
    cfdi_id: str           # ID interno de Facturama (para cancelar)
    uuid: str              # Folio fiscal del SAT
    folio: str | None      # Folio asignado por Facturama
    serie: str | None


class SolicitudNoFacturableError(FacturamaError):
    """
    La solicitud no está en un estado válido para facturar (ya facturada,
    cancelada, sin emisor, etc.).
    """


# ─── API pública ───────────────────────────────────────────────

def emitir_cfdi_desde_solicitud(
    solicitud,
    *,
    client: FacturamaClient | None = None,
) -> ResultadoEmision:
    """
    Emite un CFDI ante el SAT (vía Facturama) a partir de una
    SolicitudFactura y guarda los archivos resultantes en la solicitud.

    Args:
        solicitud: instancia de SolicitudFactura ya persistida.
        client: cliente Facturama (inyectable para tests). Por defecto
            se instancia uno leyendo credenciales del entorno.

    Returns:
        ResultadoEmision con el UUID y datos del CFDI emitido.

    Raises:
        SolicitudNoFacturableError: si la solicitud no puede facturarse.
        FacturamaError: si Facturama/SAT rechaza la emisión.
    """
    _validar_solicitud(solicitud)

    client = client or FacturamaClient()

    # 1. Construir payload (esto puede lanzar ValueError si el mapper
    #    detecta datos faltantes — lo traducimos a FacturamaError).
    try:
        payload = solicitud_a_payload_facturama(solicitud)
    except ValueError as exc:
        raise SolicitudNoFacturableError(str(exc)) from exc

    logger.info(
        "Emitiendo CFDI para SolicitudFactura #%s (emisor=%s, receptor=%s)",
        solicitud.pk,
        solicitud.emisor.rfc,
        solicitud.rfc,
    )

    # 2. Llamar a Facturama (fuera de transacción: es I/O externo).
    try:
        respuesta = client.emitir_cfdi(payload)
    except FacturamaError as exc:
        # Traducir el mensaje a algo accionable antes de re-lanzar
        exc.message = traducir_error(exc.message)
        logger.error(
            "Fallo al emitir CFDI SolicitudFactura #%s: %s",
            solicitud.pk,
            exc.message,
        )
        raise

    cfdi_id = respuesta.get("Id")
    if not cfdi_id:
        raise FacturamaError(
            "Facturama no devolvió Id del CFDI emitido.",
            raw_response=respuesta,
        )

    uuid = _extraer_uuid(respuesta)
    folio = respuesta.get("Folio")
    serie = respuesta.get("Serie")

    # 3. Descargar PDF y XML (también I/O, fuera de transacción).
    try:
        pdf_bytes = client.descargar_pdf(cfdi_id)
        xml_bytes = client.descargar_xml(cfdi_id)
    except FacturamaError as exc:
        # El CFDI ya fue emitido, pero no pudimos descargar los archivos.
        # Guardamos el UUID al menos para poder descargarlos luego a mano.
        logger.warning(
            "CFDI emitido (UUID=%s) pero falló la descarga de PDF/XML: %s",
            uuid,
            exc.message,
        )
        _marcar_emitida_sin_archivos(solicitud, cfdi_id, uuid, folio, serie)
        raise FacturamaError(
            f"CFDI emitido correctamente (UUID {uuid}) pero no pudimos "
            f"descargar los archivos automáticamente: {exc.message}. "
            "Descárgalos manualmente desde el panel de Facturama.",
            status_code=exc.status_code,
            raw_response=exc.raw_response,
        ) from exc

    # 4. Guardar archivos y actualizar campos (esto sí en transacción).
    _persistir_resultado(
        solicitud,
        cfdi_id=cfdi_id,
        uuid=uuid,
        folio=folio,
        serie=serie,
        pdf_bytes=pdf_bytes,
        xml_bytes=xml_bytes,
    )

    logger.info(
        "CFDI emitido OK SolicitudFactura #%s: UUID=%s folio=%s",
        solicitud.pk,
        uuid,
        folio,
    )

    return ResultadoEmision(
        solicitud_id=solicitud.pk,
        cfdi_id=cfdi_id,
        uuid=uuid,
        folio=folio,
        serie=serie,
    )


# ─── Helpers internos ──────────────────────────────────────────

def _validar_solicitud(solicitud) -> None:
    """Validaciones previas antes de siquiera contactar Facturama."""
    if solicitud.pk is None:
        raise SolicitudNoFacturableError(
            "La solicitud no ha sido guardada en base de datos."
        )

    if solicitud.estado == "CANCELADA":
        raise SolicitudNoFacturableError(
            f"SolicitudFactura #{solicitud.pk} está cancelada."
        )

    if solicitud.uuid_factura:
        raise SolicitudNoFacturableError(
            f"SolicitudFactura #{solicitud.pk} ya tiene UUID "
            f"({solicitud.uuid_factura}); no se puede re-emitir. "
            "Si necesitas corregirla, cancélala primero."
        )

    if solicitud.emisor is None:
        raise SolicitudNoFacturableError(
            f"SolicitudFactura #{solicitud.pk} no tiene emisor fiscal asignado."
        )

    if not solicitud.emisor.activo:
        raise SolicitudNoFacturableError(
            f"El emisor fiscal '{solicitud.emisor.nombre_interno}' "
            "está inactivo."
        )

    # Sanity check de datos mínimos del receptor
    campos_receptor = {
        "rfc": solicitud.rfc,
        "razon_social": solicitud.razon_social,
        "codigo_postal": solicitud.codigo_postal,
        "regimen_fiscal": solicitud.regimen_fiscal,
        "uso_cfdi": solicitud.uso_cfdi,
    }
    faltantes = [k for k, v in campos_receptor.items() if not v]
    if faltantes:
        raise SolicitudNoFacturableError(
            f"SolicitudFactura #{solicitud.pk} tiene datos fiscales "
            f"incompletos: faltan {', '.join(faltantes)}."
        )


def _extraer_uuid(respuesta: dict) -> str:
    """Extrae el UUID (folio fiscal) de la respuesta de Facturama."""
    try:
        return respuesta["Complement"]["TaxStamp"]["Uuid"]
    except (KeyError, TypeError):
        # Algunas respuestas usan camelCase distinto
        return respuesta.get("Uuid", "") or ""


def _marcar_emitida_sin_archivos(
    solicitud,
    cfdi_id: str,
    uuid: str,
    folio: str | None,
    serie: str | None,
) -> None:
    """
    Guarda UUID/cfdi_id cuando Facturama emitió el CFDI pero no pudimos
    descargar los archivos. Evita que el usuario intente re-emitir.
    """
    campos = {"uuid_factura": uuid or "", "fecha_factura": timezone.now().date()}
    # No tocamos estado para que el usuario sepa que falta completar.
    type(solicitud).objects.filter(pk=solicitud.pk).update(**campos)


def _persistir_resultado(
    solicitud,
    *,
    cfdi_id: str,
    uuid: str,
    folio: str | None,
    serie: str | None,
    pdf_bytes: bytes,
    xml_bytes: bytes,
) -> None:
    """
    Guarda PDF/XML en los FileField y actualiza los campos derivados
    de la solicitud. Todo dentro de transacción.
    """
    folio_nombre = folio or f"SOL{solicitud.pk}"
    nombre_pdf = f"factura_{folio_nombre}.pdf"
    nombre_xml = f"factura_{folio_nombre}.xml"

    with transaction.atomic():
        # Refrescar por si algo cambió mientras hablábamos con Facturama
        solicitud.refresh_from_db()

        solicitud.archivo_pdf.save(nombre_pdf, ContentFile(pdf_bytes), save=False)
        solicitud.archivo_xml.save(nombre_xml, ContentFile(xml_bytes), save=False)

        solicitud.uuid_factura = uuid
        solicitud.fecha_factura = timezone.now().date()
        # El save() del modelo cambia el estado a FACTURADA cuando detecta
        # tiene_factura=True, así que no lo tocamos explícitamente.
        solicitud.save()
