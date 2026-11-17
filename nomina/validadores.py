"""Validadores de datos personales del expediente del empleado (Issue #505, PR 1A).

Ningún mensaje de error incluye el dato capturado: CURP, RFC, NSS, CLABE y
número de identificación no deben llegar a logs, avisos ni mensajes de error.
"""
import re

from django.core.exceptions import ValidationError

CURP_RE = re.compile(r'^[A-Z]{4}\d{6}[HMX][A-Z]{2}[B-DF-HJ-NP-TV-Z]{3}[A-Z0-9]\d$')
RFC_PF_RE = re.compile(r'^[A-ZÑ&]{4}\d{6}[A-Z0-9]{3}$')
_PESOS_CLABE = (3, 7, 1)


def limpiar(valor) -> str:
    """Mayúsculas y sin espacios ni guiones (los datos oficiales se capturan así)."""
    return re.sub(r'[\s-]+', '', str(valor or '')).upper()


def digito_clabe(primeros_17: str) -> int:
    suma = sum((int(d) * _PESOS_CLABE[i % 3]) % 10 for i, d in enumerate(primeros_17))
    return (10 - suma % 10) % 10


def validar_curp(valor: str) -> None:
    if not CURP_RE.match(valor):
        raise ValidationError('La CURP debe tener 18 caracteres con el formato oficial.')


def validar_rfc(valor: str) -> None:
    if not RFC_PF_RE.match(valor):
        raise ValidationError('El RFC debe tener 13 caracteres (persona física).')


def validar_nss(valor: str) -> None:
    if not re.fullmatch(r'\d{11}', valor):
        raise ValidationError('El NSS debe tener 11 dígitos.')


def validar_clabe(valor: str) -> None:
    if not re.fullmatch(r'\d{18}', valor):
        raise ValidationError('La CLABE debe tener 18 dígitos.')
    if digito_clabe(valor[:17]) != int(valor[17]):
        raise ValidationError('La CLABE no es válida: el dígito verificador no coincide.')


def validar_codigo_postal(valor: str) -> None:
    if not re.fullmatch(r'\d{5}', valor):
        raise ValidationError('El código postal debe tener 5 dígitos.')
