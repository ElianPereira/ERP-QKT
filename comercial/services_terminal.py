"""
Cobro con terminal reportado por WhatsApp (Issue #428, punto 3 · dinero).

El candado es triple y ninguna capa sola crea un `Pago`:
  1. el teléfono debe ser el activo de ENCARGADO o DIRECCION en el directorio;
  2. el mensaje solo deja una propuesta; el `Pago` nace con el botón que lleva
     un token de un solo uso, válido 10 min y solo para el mismo teléfono;
  3. el monto debe ser > 0 y ≤ al saldo de la cotización, al proponer y otra
     vez al confirmar (el saldo pudo cambiar entre los dos pasos).

El `Pago` se crea por la misma vía que el admin (`full_clean()` + `save()`), así
corren los signals de póliza, solicitud de factura y confirmación por pago.
No se toca `comision_tpv`: la terminal BBVA cobra su comisión aparte.
"""
import logging
import re
import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import NamedTuple, Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from comunicacion.services import normalizar_telefono_wa
from comunicacion.services_responsables import rol_de_telefono, telefono_de
from core_erp import impuestos

from .models import Cotizacion, Pago, ReporteTerminal

logger = logging.getLogger(__name__)

RE_REPORTE = re.compile(r'^\s*terminal\s+\$?([\d,]+(?:\.\d{1,2})?)\s+(?:cot-?\s*)?(\d+)\s*$', re.IGNORECASE)
RE_BOTON = re.compile(r'^term:([A-Za-z0-9_-]{16,64}):ok$')
ROLES_AUTORIZADOS = ('ENCARGADO', 'DIRECCION')
VIGENCIA = timedelta(minutes=10)
ESTADOS_NO_COBRABLES = ('CANCELADA', 'EXPIRADA')
METODO = 'TARJETA_DEBITO'
ETIQUETA_BOTON = 'Confirmar'

BOTON_INVALIDO = 'Ese botón ya no es válido. Si el cobro sigue pendiente, vuelve a enviar el reporte.'
BOTON_VENCIDO = 'Pasaron más de 10 minutos y el botón venció. Vuelve a enviar el reporte si el cobro sigue pendiente.'
YA_REGISTRADO = 'Ese cobro ya se había registrado. No se creó otro pago.'
ERROR_INTERNO = 'No se pudo registrar el cobro. Avisa a Dirección; no se creó ningún pago.'


class Resultado(NamedTuple):
    texto: str
    boton_id: Optional[str] = None   # si hay botón, el texto es la propuesta a confirmar
    pago: Optional[Pago] = None      # solo al confirmar con éxito
    reporte: Optional[ReporteTerminal] = None


def es_reporte(texto: str) -> bool:
    return bool(RE_REPORTE.match(texto or ''))


def es_boton(texto: str) -> bool:
    return bool(RE_BOTON.match((texto or '').strip()))


def id_boton(token: str) -> str:
    return f'term:{token}:ok'


def _hash(token: str) -> str:
    return salted_hmac('comercial.terminal', token, algorithm='sha256').hexdigest()


def rol_autorizado(telefono: str) -> Optional[str]:
    """ENCARGADO o DIRECCION con ese número ya **activo**. Un número que aún no
    dijo «ACEPTO» (pendiente) no mueve dinero."""
    tel = normalizar_telefono_wa(telefono)
    rol = rol_de_telefono(tel)
    if rol in ROLES_AUTORIZADOS and tel == telefono_de(rol):
        return rol
    return None


def _dinero(valor) -> str:
    return f'${valor:,.2f}'


def _monto(texto: str) -> Optional[Decimal]:
    try:
        return impuestos.centavos(Decimal(texto.replace(',', '')))
    except (InvalidOperation, ValueError):
        return None


def _rechazo(cot: Optional[Cotizacion], monto: Optional[Decimal]) -> Optional[str]:
    """Motivo fijo por el que no se puede registrar ese cobro, o None si procede."""
    if cot is None:
        return 'No encontré esa cotización. Revisa el folio.'
    if cot.estado in ESTADOS_NO_COBRABLES:
        return f'COT-{cot.pk:03d} está {cot.get_estado_display().lower()}: no se le pueden registrar cobros.'
    if monto is None or monto <= 0:
        return 'El monto debe ser mayor a cero.'
    saldo = cot.saldo_pendiente()
    if monto > saldo:
        return (f'El monto {_dinero(monto)} es mayor al saldo de COT-{cot.pk:03d} ({_dinero(max(saldo, Decimal("0.00")))}). '
                'No se registró nada.')
    return None


def proponer(telefono: str, texto: str) -> Optional[Resultado]:
    """«terminal 5000 cot 145» → resumen con botón. None si el teléfono no está
    autorizado o el texto no es un reporte (sigue el flujo normal del agente)."""
    coincide = RE_REPORTE.match(texto or '')
    rol = rol_autorizado(telefono)
    if not coincide or rol is None:
        return None
    tel = normalizar_telefono_wa(telefono)
    monto = _monto(coincide.group(1))
    cot = Cotizacion.objects.select_related('cliente').filter(pk=int(coincide.group(2))).first()
    motivo = _rechazo(cot, monto)
    if motivo:
        return Resultado(motivo)
    token = secrets.token_urlsafe(24)
    with transaction.atomic():
        # Un reporte idéntico anterior sin confirmar queda sin efecto: dos
        # propuestas vivas del mismo cobro podrían confirmarse las dos.
        ReporteTerminal.objects.filter(telefono=tel, cotizacion=cot, monto=monto, estado='PROPUESTO').update(
            estado='VENCIDO', updated_at=timezone.now())
        reporte = ReporteTerminal.objects.create(
            telefono=tel, rol=rol, cotizacion=cot, monto=monto, token_hash=_hash(token),
            vence_en=timezone.now() + VIGENCIA)
    despues = impuestos.centavos(cot.saldo_pendiente() - monto)
    resumen = (f'Registrar {_dinero(monto)} con terminal a COT-{cot.pk:03d} ({cot.cliente}). '
               f'Saldo después: {_dinero(despues)}. Toca Confirmar en los próximos 10 minutos.')
    return Resultado(resumen, boton_id=id_boton(token), reporte=reporte)


def confirmar(telefono: str, texto: str) -> Optional[Resultado]:
    """Botón `term:<token>:ok` → crea **un** `Pago`. None si no es un botón de
    terminal o el teléfono no está autorizado (no hace nada)."""
    coincide = RE_BOTON.match((texto or '').strip())
    if not coincide or rol_autorizado(telefono) is None:
        return None
    tel = normalizar_telefono_wa(telefono)
    try:
        with transaction.atomic():
            reporte = (ReporteTerminal.objects.select_for_update()
                       .filter(token_hash=_hash(coincide.group(1))).first())
            if reporte is None or reporte.telefono != tel:
                return Resultado(BOTON_INVALIDO)
            if reporte.estado == 'CONFIRMADO':
                return Resultado(YA_REGISTRADO, reporte=reporte)
            if reporte.estado != 'PROPUESTO':
                return Resultado(BOTON_VENCIDO)
            ahora = timezone.now()
            if reporte.vence_en < ahora:
                ReporteTerminal.objects.filter(pk=reporte.pk).update(estado='VENCIDO', updated_at=ahora)
                return Resultado(BOTON_VENCIDO)
            cot = Cotizacion.objects.select_for_update().select_related('cliente').get(pk=reporte.cotizacion_id)
            motivo = _rechazo(cot, reporte.monto)
            if motivo:
                return Resultado(motivo)
            pago = Pago(
                cotizacion=cot, tipo='INGRESO', concepto='VENTA', metodo=METODO, monto=reporte.monto,
                fecha_pago=timezone.localdate(), referencia=f'TERMINAL-WA-{reporte.pk}',
                notas=f'Reportado por WhatsApp desde {reporte.rol} {reporte.telefono}')
            pago.full_clean()
            pago.save()
            reporte.estado, reporte.pago, reporte.confirmado_en = 'CONFIRMADO', pago, ahora
            reporte.save(update_fields=['estado', 'pago', 'confirmado_en', 'updated_at'])
    except ValidationError as e:
        return Resultado(f'No se registró el cobro: {"; ".join(e.messages)}')
    except Exception:
        logger.exception("Terminal por WhatsApp: no se pudo registrar el cobro")
        return Resultado(ERROR_INTERNO)
    saldo = max(cot.saldo_pendiente(), Decimal('0.00'))
    return Resultado(f'Listo: se registró {_dinero(pago.monto)} con terminal en COT-{cot.pk:03d}. '
                     f'Saldo pendiente: {_dinero(saldo)}.', pago=pago, reporte=reporte)
