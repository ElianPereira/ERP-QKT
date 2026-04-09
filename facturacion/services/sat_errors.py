"""
Traducción de códigos de error del SAT (CFDIxxxxx) a mensajes
accionables en español para el operador del ERP.

Cuando Facturama rechaza un CFDI, devuelve un mensaje del tipo:
    "CFDI33118: La suma de los importes del nodo Conceptos..."

Este módulo mapea los códigos más comunes a un texto explicativo
y una sugerencia concreta sobre cómo corregirlo en el ERP.

Fuente: Anexo 20 del SAT + guías de Facturama. Mantener la lista
actualizada a medida que encontremos errores nuevos en producción.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


_SAT_CODE_RE = re.compile(r"CFDI\d{5}")


@dataclass(frozen=True)
class SatError:
    code: str
    titulo: str
    descripcion: str
    sugerencia: str

    def mensaje_corto(self) -> str:
        return f"{self.titulo} → {self.sugerencia}"


# Diccionario de errores más frecuentes al emitir desde el ERP.
# Priorizamos los que dependen de datos del cliente o del emisor,
# porque son los que el operador puede corregir sin soporte.
SAT_ERRORS: dict[str, SatError] = {
    "CFDI40147": SatError(
        code="CFDI40147",
        titulo="Receptor con RFC inválido o no registrado en el SAT",
        descripcion=(
            "El RFC del cliente no existe en la lista de contribuyentes "
            "del SAT o tiene formato incorrecto."
        ),
        sugerencia=(
            "Verifica en la Constancia de Situación Fiscal del cliente "
            "que el RFC esté bien escrito (sin espacios)."
        ),
    ),
    "CFDI40149": SatError(
        code="CFDI40149",
        titulo="Nombre del receptor no coincide con el padrón del SAT",
        descripcion=(
            "La razón social capturada no coincide EXACTAMENTE con la "
            "que tiene el SAT para ese RFC."
        ),
        sugerencia=(
            "Copia la razón social tal como aparece en la Constancia de "
            "Situación Fiscal del cliente (respetando mayúsculas y "
            "acentos)."
        ),
    ),
    "CFDI40150": SatError(
        code="CFDI40150",
        titulo="Código postal del receptor inválido",
        descripcion=(
            "El código postal fiscal del receptor no coincide con el "
            "registrado en el SAT o tiene formato incorrecto."
        ),
        sugerencia=(
            "Verifica el C.P. en la Constancia de Situación Fiscal del "
            "cliente y actualízalo en su ficha."
        ),
    ),
    "CFDI40157": SatError(
        code="CFDI40157",
        titulo="Régimen fiscal del receptor no válido",
        descripcion=(
            "El régimen fiscal declarado para el receptor no corresponde "
            "al que tiene registrado en el SAT."
        ),
        sugerencia=(
            "Pide al cliente su régimen actual (viene en la Constancia "
            "de Situación Fiscal) y actualízalo en su ficha."
        ),
    ),
    "CFDI40158": SatError(
        code="CFDI40158",
        titulo="Uso del CFDI no permitido para el régimen del receptor",
        descripcion=(
            "El Uso CFDI seleccionado no es compatible con el régimen "
            "fiscal del receptor."
        ),
        sugerencia=(
            "Cambia el Uso CFDI en la solicitud (prueba con G03 - Gastos "
            "en general) o verifica el régimen del cliente."
        ),
    ),
    "CFDI33118": SatError(
        code="CFDI33118",
        titulo="Descuadre en los totales de conceptos",
        descripcion=(
            "La suma de los importes de los conceptos no coincide con el "
            "subtotal del comprobante."
        ),
        sugerencia=(
            "Revisa el desglose fiscal de la solicitud: subtotal + IVA "
            "- retenciones debe ser igual al monto."
        ),
    ),
    "CFDI33110": SatError(
        code="CFDI33110",
        titulo="Importe del impuesto trasladado incorrecto",
        descripcion=(
            "El importe de IVA declarado no corresponde al resultado de "
            "multiplicar la base por la tasa."
        ),
        sugerencia=(
            "Verifica que el IVA de la solicitud sea exactamente el 16% "
            "del subtotal."
        ),
    ),
    "CFDI40115": SatError(
        code="CFDI40115",
        titulo="Forma de pago no válida",
        descripcion=(
            "La forma de pago declarada no corresponde al catálogo SAT "
            "o no es compatible con el método de pago."
        ),
        sugerencia=(
            "Si el método de pago es PPD (parcialidades), la forma de "
            "pago debe ser '99 - Por definir'."
        ),
    ),
    "CFDI40161": SatError(
        code="CFDI40161",
        titulo="Código postal del lugar de expedición inválido",
        descripcion=(
            "El C.P. del lugar de expedición no existe en el catálogo "
            "o no corresponde al domicilio fiscal del emisor."
        ),
        sugerencia=(
            "Edita el Emisor Fiscal en el admin y corrige el campo "
            "'Lugar de expedición' o 'C.P. Fiscal'."
        ),
    ),
}


def extraer_codigo_sat(mensaje: str | None) -> str | None:
    """Busca un patrón CFDIxxxxx en el mensaje de error y lo devuelve."""
    if not mensaje:
        return None
    m = _SAT_CODE_RE.search(mensaje)
    return m.group(0) if m else None


def traducir_error(mensaje: str | None) -> str:
    """
    Convierte un mensaje de Facturama en un texto accionable en español.

    Si el mensaje contiene un código CFDIxxxxx conocido, devuelve la
    traducción. En caso contrario, regresa el mensaje tal cual.
    """
    if not mensaje:
        return "Error desconocido al emitir CFDI."

    codigo = extraer_codigo_sat(mensaje)
    if codigo and codigo in SAT_ERRORS:
        err = SAT_ERRORS[codigo]
        return f"[{codigo}] {err.titulo}. {err.sugerencia}"

    if codigo:
        # Código desconocido: al menos lo marcamos claramente
        return f"[{codigo}] {mensaje}"

    return mensaje
