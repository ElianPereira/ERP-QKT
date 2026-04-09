"""
Mapper: SolicitudFactura → payload de Facturama (CFDI 4.0 Ingreso).

La SolicitudFactura ya viene con el desglose fiscal calculado
(subtotal, iva, retencion_isr, retencion_iva) desde
`comercial.services.calcular_desglose_proporcional`. Este mapper solo
traduce esos valores al formato JSON que espera la API de Facturama.

Reglas de negocio:
- El emisor se toma de `solicitud.emisor` (FK a EmisorFiscal). Su RFC,
  razón social, régimen, CP y serie determinan el encabezado del CFDI.
- Retención ISR 1.25% aplica cuando el emisor es Persona Física RESICO
  (régimen '626') y el receptor es Persona Moral (RFC de 12 caracteres).
  Si la SolicitudFactura ya trae retencion_isr > 0, se respeta ese valor;
  si viene en 0 pero la regla aplicaría, se calcula sobre subtotal.
- Público en General (RFC XAXX010101000) nunca tiene retención y usa S01.
- ProductCode por unidad de negocio: Eventos = 90101501 (Servicios de
  banquetes), Airbnb/Hospedaje = 90111800 (Servicios de hospedaje).
  Default: 80000000 (Servicios de gestión / profesionales) si no hay
  unidad de negocio asignada.
- UnitCode por defecto: E48 (Unidad de servicio).

Referencia: https://apisandbox.facturama.mx/guias/api-multi/cfdi/crear
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any


# ─── Catálogos SAT usados por defecto ───────────────────────────
# Claves ProdServ del SAT. Se pueden cambiar editando el emisor o
# extendiendo este mapper si a futuro hay múltiples tipos de servicio
# por unidad de negocio.
PRODUCT_CODE_EVENTOS = "90101501"   # Servicios de banquetes
PRODUCT_CODE_HOSPEDAJE = "90111800"  # Servicios de hospedaje
PRODUCT_CODE_DEFAULT = "80000000"    # Servicios de gestión organizacional

UNIT_CODE_SERVICIO = "E48"           # Unidad de servicio
UNIT_NAME_SERVICIO = "Servicio"

# TaxObject en CFDI 4.0: "02" = Sí objeto de impuesto
TAX_OBJECT_SI = "02"

# Régimen RESICO Persona Física (emisor Elian / Eventos)
REGIMEN_RESICO_PF = "626"

# Tasas
IVA_RATE = Decimal("0.16")
ISR_RESICO_RATE = Decimal("0.0125")

RFC_PUBLICO_EN_GENERAL = "XAXX010101000"


# ─── Helpers ────────────────────────────────────────────────────

def _d(value: Any) -> Decimal:
    """Convierte un valor a Decimal con 2 decimales."""
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _f(value: Decimal) -> float:
    """Convierte un Decimal a float para serializar a JSON (Facturama lo acepta)."""
    return float(value)


def _es_persona_moral(rfc: str) -> bool:
    """RFCs de Personas Morales tienen 12 caracteres, las Físicas 13."""
    if not rfc:
        return False
    return len(rfc.strip()) == 12


def _aplica_retencion_isr_resico(
    emisor_regimen: str,
    receptor_rfc: str,
) -> bool:
    """
    Retención 1.25% ISR aplica cuando el emisor es RESICO Persona Física
    (régimen 626) y el receptor es Persona Moral (no público en general).
    """
    if emisor_regimen != REGIMEN_RESICO_PF:
        return False
    if not receptor_rfc or receptor_rfc == RFC_PUBLICO_EN_GENERAL:
        return False
    return _es_persona_moral(receptor_rfc)


def _product_code_para_emisor(emisor) -> str:
    """Determina la clave ProdServ del SAT según la unidad de negocio."""
    unidad = getattr(emisor, "unidad_negocio", None)
    clave = (getattr(unidad, "clave", "") or "").upper()
    if clave == "EVENTOS":
        return PRODUCT_CODE_EVENTOS
    if clave in ("AIRBNB", "HOSPEDAJE"):
        return PRODUCT_CODE_HOSPEDAJE
    return PRODUCT_CODE_DEFAULT


def _sanitizar_nombre(nombre: str) -> str:
    """
    Facturama valida el Name del receptor contra el padrón SAT. Aquí
    solo recortamos y quitamos espacios extra; la corrección del padrón
    debe hacerla el usuario al capturar al cliente.
    """
    if not nombre:
        return ""
    return " ".join(nombre.strip().split())[:254]


# ─── Función principal ─────────────────────────────────────────

def solicitud_a_payload_facturama(solicitud) -> dict:
    """
    Construye el payload JSON para emitir un CFDI 4.0 tipo Ingreso desde
    una SolicitudFactura.

    Args:
        solicitud: instancia de facturacion.models.SolicitudFactura.
            Debe tener asignado un `emisor` activo.

    Returns:
        dict con la estructura esperada por POST /api/3/cfdis de Facturama.

    Raises:
        ValueError si la solicitud no tiene emisor o está incompleta.
    """
    emisor = solicitud.emisor
    if emisor is None:
        raise ValueError(
            f"SolicitudFactura #{solicitud.pk} no tiene emisor fiscal asignado. "
            "Asigna un EmisorFiscal antes de emitir el CFDI."
        )
    if not emisor.activo:
        raise ValueError(
            f"El emisor fiscal '{emisor.nombre_interno}' ({emisor.rfc}) "
            "está inactivo."
        )

    # ─── Montos ────────────────────────────────────────────────
    subtotal = _d(solicitud.subtotal)
    iva = _d(solicitud.iva)
    retencion_isr_guardada = _d(solicitud.retencion_isr)
    retencion_iva_guardada = _d(solicitud.retencion_iva)

    if subtotal <= 0:
        raise ValueError(
            f"SolicitudFactura #{solicitud.pk} tiene subtotal <= 0; "
            "no se puede emitir CFDI."
        )

    # ─── Retención ISR: respetar lo guardado; si viene en 0 y la
    # regla RESICO→PM aplica, calcular sobre subtotal. ─────────
    aplica_isr_rule = _aplica_retencion_isr_resico(
        emisor.regimen_fiscal, solicitud.rfc
    )
    if retencion_isr_guardada > 0:
        retencion_isr = retencion_isr_guardada
    elif aplica_isr_rule:
        retencion_isr = (subtotal * ISR_RESICO_RATE).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    else:
        retencion_isr = Decimal("0.00")

    retencion_iva = retencion_iva_guardada  # por ahora no se recalcula

    # ─── Concepto / Item ───────────────────────────────────────
    concepto = (solicitud.concepto or "Servicio").strip()
    if not concepto:
        concepto = "Servicio"

    product_code = _product_code_para_emisor(emisor)

    taxes: list[dict] = [
        {
            "Name": "IVA",
            "Rate": _f(IVA_RATE),
            "Total": _f(iva),
            "Base": _f(subtotal),
            "IsRetention": False,
            "IsFederalTax": True,
        }
    ]

    if retencion_isr > 0:
        taxes.append(
            {
                "Name": "ISR",
                "Rate": _f(ISR_RESICO_RATE),
                "Total": _f(retencion_isr),
                "Base": _f(subtotal),
                "IsRetention": True,
                "IsFederalTax": True,
            }
        )

    if retencion_iva > 0:
        taxes.append(
            {
                "Name": "IVA",
                "Rate": _f(IVA_RATE),
                "Total": _f(retencion_iva),
                "Base": _f(subtotal),
                "IsRetention": True,
                "IsFederalTax": True,
            }
        )

    # Item.Total = Subtotal + IVA trasladado (las retenciones no afectan
    # el total del item; se aplican a nivel comprobante vía IsRetention).
    item_total = subtotal + iva

    item = {
        "ProductCode": product_code,
        "IdentificationNumber": f"SF-{solicitud.pk}" if solicitud.pk else "SF",
        "Description": concepto[:1000],
        "Unit": UNIT_NAME_SERVICIO,
        "UnitCode": UNIT_CODE_SERVICIO,
        "UnitPrice": _f(subtotal),
        "Quantity": 1,
        "Subtotal": _f(subtotal),
        "Discount": 0,
        "TaxObject": TAX_OBJECT_SI,
        "Taxes": taxes,
        "Total": _f(item_total),
    }

    # ─── Receiver ──────────────────────────────────────────────
    receptor_rfc = (solicitud.rfc or "").strip().upper()
    receptor = {
        "Rfc": receptor_rfc,
        "Name": _sanitizar_nombre(solicitud.razon_social),
        "CfdiUse": solicitud.uso_cfdi,
        "FiscalRegime": solicitud.regimen_fiscal,
        "TaxZipCode": (solicitud.codigo_postal or "").strip(),
    }

    # ─── Issuer ────────────────────────────────────────────────
    issuer = {
        "FiscalRegime": emisor.regimen_fiscal,
        "Rfc": emisor.rfc.strip().upper(),
        "Name": _sanitizar_nombre(emisor.razon_social),
    }

    # ─── Payload final ─────────────────────────────────────────
    payload: dict = {
        "NameId": "1",  # 1 = Factura (CFDI de Ingreso)
        "CfdiType": "I",
        "PaymentForm": solicitud.forma_pago,
        "PaymentMethod": solicitud.metodo_pago,
        "Currency": "MXN",
        "ExpeditionPlace": emisor.lugar_expedicion_efectivo,
        "Serie": emisor.serie_folio,
        "Issuer": issuer,
        "Receiver": receptor,
        "Items": [item],
    }

    return payload
