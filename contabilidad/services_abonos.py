"""
Transferencias de clientes identificadas por folio (dinero).

Un cliente que transfiere directo a la cuenta BBVA deja un abono en el estado de
cuenta y nadie registra su `Pago`. Este módulo lo atribuye a una cotización, en
tres capas de confianza decreciente (cada abono deja una fila en `AbonoIdentificado`):

1. FOLIO: el concepto trae `COT-145`. Si la cotización admite pago y el importe cabe
   en su saldo, nace el `Pago` solo y se liga el renglón a la línea de bancos de su
   póliza. Es la única capa que mueve dinero sin que una persona la apruebe.
2. NOMBRE + MONTO: el nombre del cliente aparece en el concepto y el importe es el
   saldo, el mínimo o una parcialidad del plan. Solo si hay UNA candidata: se propone.
3. MONTO: una sola cotización con saldo o mínimo igual al importe y fecha de evento
   cercana. Se propone.

Lo propuesto va a Dirección con botones (`abo:<token>:ok|no`, un solo uso, 24 h, solo
desde su teléfono activo); al confirmar se revalida todo bajo candado. Lo que no
tiene ninguna pista abre un pendiente al encargado. Nada de esto toca contabilidad
directamente: el `Pago` se crea con `full_clean()` + `save()`, así corren los signals
de póliza, solicitud de factura y confirmación por pago.
"""
import hashlib
import logging
import re
import secrets
from collections import Counter
from datetime import timedelta
from decimal import Decimal
from typing import NamedTuple, Optional

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from comercial import datos_bancarios
from comercial.models import Cotizacion, Pago
from comercial.services_ordenes_wa import direccion_activa
from comunicacion.services import normalizar_telefono_wa

from .models import (
    AbonoIdentificado,
    ConfiguracionContable,
    Poliza,
    ReglaConciliacion,
    normalizar_texto_banco,
)
from .services_reglas_banco import _vincular_linea

logger = logging.getLogger(__name__)

RE_FOLIO = re.compile(r'\bCOT[-\s]?0*(\d{1,5})\b')
RE_BOTON = re.compile(r'^abo:([A-Za-z0-9_-]{16,64}):(ok|no)$')
TOLERANCIA = Decimal('0.50')          # la misma que `Pago.clean()`
VIGENCIA_BOTON = timedelta(hours=24)
VENTANA_NOMBRE_DIAS = 120
EVENTO_DESDE_DIAS, EVENTO_HASTA_DIAS = -5, 180
MIN_PALABRAS_NOMBRE = 2
LARGO_MIN_PALABRA = 3
ETIQUETA_REGISTRAR, ETIQUETA_NO_ES = 'Registrar', 'No es'
METODO = 'TRANSFERENCIA'

BOTON_INVALIDO = 'Ese botón ya no es válido.'
BOTON_VENCIDO = 'Pasaron más de 24 horas y el botón venció. Registra el abono desde el ERP (Contabilidad, Abonos).'
YA_ATENDIDO = 'Ese abono ya se había atendido. No se creó otro pago.'
ERROR_INTERNO = 'No se pudo registrar el abono. Avisa a Dirección; no se creó ningún pago.'


class Resultado(NamedTuple):
    texto: str
    pago: Optional[Pago] = None


class _SinPoliza(Exception):
    """El `Pago` no dejó póliza ligable: se revierte todo para no dejar dinero a medias."""


def es_boton(texto: str) -> bool:
    return bool(RE_BOTON.match((texto or '').strip()))


def _hash(token: str) -> str:
    return salted_hmac('contabilidad.abonos', token, algorithm='sha256').hexdigest()


def _dinero(valor) -> str:
    return f'${valor:,.2f}'


def folio_de(cot) -> str:
    return f'COT-{cot.pk:03d}'


# ───────────────────────────── Identificación ───────────────────────────────

def _huellas(estado_cuenta) -> list:
    """(movimiento, huella) de cada abono del estado de cuenta. Dos abonos idénticos
    (misma fecha, importe, concepto y referencia) se distinguen por su orden, así uno
    no se come al otro."""
    cuenta_id = estado_cuenta.cuenta_bancaria_id
    vistos = Counter()
    resultado = []
    for mov in estado_cuenta.movimientos.filter(abono__gt=0).order_by('fecha', 'id'):
        base = '|'.join([str(cuenta_id), mov.fecha.isoformat(), f'{mov.abono:.2f}',
                         normalizar_texto_banco(mov.descripcion), normalizar_texto_banco(mov.referencia)])
        vistos[base] += 1
        huella = hashlib.sha256(f'{base}|{vistos[base]}'.encode()).hexdigest()
        resultado.append((mov, huella))
    return resultado


def _con_saldo():
    """Cotizaciones cobrables con saldo pendiente."""
    qs = Cotizacion.anotar_pagado_neto(Cotizacion.objects.select_related('cliente'))
    return [c for c in qs.exclude(estado__in=Cotizacion.ESTADOS_SIN_COBRO).filter(precio_final__gt=0)
            if c.precio_final - c.pagado_neto > 0]


def _importes_esperados(cot, *, con_plan: bool) -> set:
    importes = {cot.saldo_pendiente(), cot.monto_minimo_pago()}
    if con_plan:
        plan = getattr(cot, 'plan_pago', None)
        if plan is not None and plan.activo:
            importes |= set(plan.parcialidades.filter(pagada=False).values_list('monto', flat=True))
    return {i for i in importes if i and i > 0}


def _palabras(texto: str) -> set:
    return {p for p in re.findall(r'[A-Z0-9]+', normalizar_texto_banco(texto)) if len(p) >= LARGO_MIN_PALABRA}


def _candidatas_nombre(mov) -> list:
    descripcion = _palabras(f'{mov.descripcion} {mov.referencia}')
    desde = timezone.now() - timedelta(days=VENTANA_NOMBRE_DIAS)
    encontradas = []
    for cot in _con_saldo():
        if cot.created_at < desde:
            continue
        nombre = _palabras(cot.cliente.nombre)
        if len(nombre & descripcion) >= MIN_PALABRAS_NOMBRE and mov.abono in _importes_esperados(cot, con_plan=True):
            encontradas.append(cot)
    return encontradas


def _candidatas_monto(mov) -> list:
    desde = mov.fecha + timedelta(days=EVENTO_DESDE_DIAS)
    hasta = mov.fecha + timedelta(days=EVENTO_HASTA_DIAS)
    return [cot for cot in _con_saldo()
            if cot.fecha_evento and desde <= cot.fecha_evento <= hasta
            and mov.abono in _importes_esperados(cot, con_plan=False)]


def _folios(mov) -> list:
    texto = normalizar_texto_banco(f'{mov.descripcion} {mov.referencia}')
    return sorted({int(n) for n in RE_FOLIO.findall(texto)})


def _regla_que_lo_cubre(mov) -> bool:
    return any(r.coincide(mov) for r in ReglaConciliacion.objects.filter(activa=True))


def _motivo_no_registrable(cot, importe) -> str:
    """'' si el abono se puede registrar como pago de `cot`; si no, el motivo."""
    admite, motivo = cot.admite_pago_detalle()
    if not admite:
        return motivo
    saldo = cot.saldo_pendiente()
    if importe <= 0:
        return 'El importe debe ser mayor a cero.'
    if importe > saldo + TOLERANCIA:
        return f'El abono ({_dinero(importe)}) es mayor al saldo de {folio_de(cot)} ({_dinero(max(saldo, Decimal("0.00")))}).'
    return ''


def _linea_de_banco(pago, cuenta_contable):
    poliza = (Poliza.objects.filter(content_type=ContentType.objects.get_for_model(Pago), object_id=pago.pk,
                                    origen='PAGO_CLIENTE').exclude(estado='CANCELADA').first())
    if poliza is None:
        return None
    return poliza.movimientos.filter(cuenta=cuenta_contable, debe__gt=0).first()


def _movimiento_de(bit):
    """El renglón del banco de este abono, aunque haya cambiado de pk al reprocesar."""
    from .models import MovimientoEstadoCuenta
    if bit.movimiento_id:
        return bit.movimiento
    return (MovimientoEstadoCuenta.objects.filter(
        estado_cuenta__cuenta_bancaria=bit.cuenta_bancaria, fecha=bit.fecha, abono=bit.importe,
        movimiento_contable__isnull=True).order_by('id').first())


def _cuenta_inconsistente(cuenta_bancaria) -> str:
    """El `Pago` por transferencia asienta contra BANCO_PRINCIPAL. Si la cuenta que recibió el
    abono es otra, el asiento quedaría en una cuenta contable ajena y el renglón sin ligar."""
    principal = ConfiguracionContable.obtener_cuenta('BANCO_PRINCIPAL')
    if principal is None or cuenta_bancaria.cuenta_contable_id != principal.pk:
        return ('La cuenta bancaria del estado no es la configurada como Banco principal en contabilidad: '
                'no se registra el pago hasta corregirlo.')
    return ''


def _crear_pago(bit, cot, nota) -> Optional[Pago]:
    """Crea el `Pago`, lo liga al renglón del banco y marca el abono REGISTRADO.
    Debe correr dentro de `transaction.atomic()` con `cot` bloqueada. Lanza
    `ValidationError` o `_SinPoliza` (el llamador revierte)."""
    cuenta_bancaria = bit.cuenta_bancaria
    mov = _movimiento_de(bit)
    pago = Pago(
        cotizacion=cot, tipo='INGRESO', concepto='VENTA', metodo=METODO, monto=bit.importe,
        fecha_pago=bit.fecha, referencia=f'BANCO-{bit.huella[:12]}', notas=nota[:255])
    pago.full_clean()
    pago.save()
    linea = _linea_de_banco(pago, cuenta_bancaria.cuenta_contable)
    if linea is None:
        raise _SinPoliza(f'El pago {pago.pk} no generó póliza contra la cuenta de bancos.')
    if mov is not None and not mov.movimiento_contable_id:
        _vincular_linea(mov, linea)
    bit.estado, bit.pago, bit.cotizacion, bit.movimiento = 'REGISTRADO', pago, cot, mov
    bit.save(update_fields=['estado', 'capa', 'pago', 'cotizacion', 'movimiento', 'updated_at'])
    return pago


def _marcar_error(bit_id, cot, motivo) -> AbonoIdentificado:
    AbonoIdentificado.objects.filter(pk=bit_id).update(
        estado='ERROR', motivo=motivo[:300], cotizacion=cot, updated_at=timezone.now())
    return AbonoIdentificado.objects.get(pk=bit_id)


def _proponer(bit, cot, capa, motivo='', *, registrable) -> Optional[str]:
    """Deja el abono PROPUESTO. Devuelve el token en claro si lleva botones (solo cuando se
    puede registrar de verdad); el token nunca se guarda sin hash."""
    token = secrets.token_urlsafe(24) if registrable else ''
    bit.estado, bit.capa, bit.cotizacion, bit.motivo = 'PROPUESTO', capa, cot, motivo[:300]
    bit.token_hash = _hash(token) if token else ''
    bit.token_vence = timezone.now() + VIGENCIA_BOTON if token else None
    bit.save(update_fields=['estado', 'capa', 'cotizacion', 'motivo', 'token_hash', 'token_vence', 'updated_at'])
    return token or None


def _decidir(bit, mov) -> dict:
    """Analiza un abono nuevo y deja su fila en el estado que corresponda. Corre dentro de
    `transaction.atomic()`; devuelve qué avisar (los avisos salen después del commit)."""
    folios = _folios(mov)
    cot_folio = Cotizacion.objects.select_for_update().select_related('cliente').filter(pk=folios[0]).first() \
        if len(folios) == 1 else None

    # Capa 1 — folio en el concepto.
    if cot_folio is not None:
        bit.capa = 'FOLIO'
        motivo = _motivo_no_registrable(cot_folio, mov.abono)
        if not motivo:
            motivo = _cuenta_inconsistente(bit.cuenta_bancaria)
            if motivo:
                bit.estado, bit.cotizacion, bit.motivo = 'ERROR', cot_folio, motivo
                bit.save(update_fields=['capa', 'estado', 'cotizacion', 'motivo', 'updated_at'])
                return {'aviso': 'error', 'bit': bit, 'cot': cot_folio}
            try:
                with transaction.atomic():
                    _crear_pago(bit, cot_folio, 'Identificado en el estado de cuenta por folio')
            except (ValidationError, _SinPoliza) as e:
                motivo = '; '.join(e.messages) if isinstance(e, ValidationError) else str(e)
                bit.refresh_from_db()
                bit.estado, bit.cotizacion, bit.motivo = 'ERROR', cot_folio, motivo[:300]
                bit.save(update_fields=['estado', 'cotizacion', 'motivo', 'updated_at'])
                return {'aviso': 'error', 'bit': bit, 'cot': cot_folio}
            return {'aviso': None, 'bit': bit}
        _proponer(bit, cot_folio, 'FOLIO', motivo, registrable=False)
        return {'aviso': 'propuesto', 'bit': bit, 'cot': cot_folio, 'token': None}

    # Sin folio útil: lo que una regla de conciliación ya cubre (depósito de efectivo, intereses,
    # aportaciones) no es un pago de cliente y lo atiende `aplicar_reglas`.
    if _regla_que_lo_cubre(mov):
        return {'aviso': 'cubierto'}

    for capa, candidatas in (('NOMBRE', _candidatas_nombre), ('MONTO', _candidatas_monto)):
        encontradas = candidatas(mov)
        if len(encontradas) == 1:
            cot = encontradas[0]
            motivo = _motivo_no_registrable(cot, mov.abono)
            token = _proponer(bit, cot, capa, motivo, registrable=not motivo)
            return {'aviso': 'propuesto', 'bit': bit, 'cot': cot, 'token': token}

    bit.estado, bit.capa = 'SIN_IDENTIFICAR', 'NINGUNA'
    bit.save(update_fields=['estado', 'capa', 'updated_at'])
    return {'aviso': 'sin_identificar', 'bit': bit}


def _avisar(resultado) -> None:
    """Pendientes y avisos de un abono ya decidido. Nunca lanza."""
    from comunicacion.services_pendientes import registrar_pendiente
    bit, aviso = resultado['bit'], resultado['aviso']
    clave = f'abono:{bit.huella}'
    datos = [f'Abono de {_dinero(bit.importe)} del {bit.fecha:%d/%m/%Y}', f'Banco: {bit.descripcion[:100]}']
    try:
        if aviso == 'sin_identificar':
            registrar_pendiente(
                titulo=f'Abono sin identificar: {_dinero(bit.importe)}', rol='ENCARGADO', tipo='OTRO',
                detalle='\n'.join(datos + ['Sin folio ni cliente reconocible. Si es de un cliente, registra el pago en su cotización.']),
                objeto=bit, clave_idempotencia=clave)
        elif aviso == 'error':
            registrar_pendiente(
                titulo=f'Abono por registrar con error: {_dinero(bit.importe)}', rol='DIRECCION', tipo='OTRO',
                severidad='URGENTE', detalle='\n'.join(datos + [f'Motivo: {bit.motivo}']),
                objeto=bit, clave_idempotencia=clave)
        elif aviso == 'propuesto':
            cot = resultado['cot']
            lineas = datos + [f'Posible pago de {folio_de(cot)} ({cot.cliente.nombre})',
                              f'Pista: {bit.get_capa_display().lower()}']
            if bit.motivo:
                lineas.append(f'No se puede registrar solo: {bit.motivo}')
            token = resultado.get('token')
            botones = [(f'abo:{token}:ok', ETIQUETA_REGISTRAR), (f'abo:{token}:no', ETIQUETA_NO_ES)] if token else None
            registrar_pendiente(
                titulo=f'Abono por confirmar: {_dinero(bit.importe)}', rol='DIRECCION', tipo='OTRO',
                severidad='URGENTE', detalle='\n'.join(lineas), objeto=bit, clave_idempotencia=clave,
                botones=botones, etiqueta_ok='Resuelto')
    except Exception:
        logger.exception('Abonos: no se pudo avisar del abono %s', bit.pk)


def identificar_abonos(estado_cuenta) -> dict:
    """Atribuye a una cotización cada abono del estado de cuenta que aún no tiene asiento.
    Idempotente por huella. Devuelve {'registrados', 'propuestos', 'sin_identificar', 'errores'}
    con lo hecho en esta corrida."""
    resumen = Counter(registrados=0, propuestos=0, sin_identificar=0, errores=0)
    cuenta_bancaria = estado_cuenta.cuenta_bancaria
    for mov, huella in _huellas(estado_cuenta):
        existente = AbonoIdentificado.objects.filter(huella=huella).first()
        if existente is not None:
            _religar(existente, mov)
            continue
        if mov.movimiento_contable_id:
            continue
        try:
            with transaction.atomic():
                bit = AbonoIdentificado.objects.create(
                    cuenta_bancaria=cuenta_bancaria, fecha=mov.fecha, importe=mov.abono,
                    descripcion=mov.descripcion[:300], huella=huella, movimiento=mov)
                resultado = _decidir(bit, mov)
                if resultado['aviso'] == 'cubierto':
                    raise _Cubierto()
        except _Cubierto:
            continue
        except IntegrityError:
            continue  # otro proceso ya lo atendió
        except Exception:
            logger.exception('Abonos: falló el abono %s del estado %s', huella[:12], estado_cuenta.pk)
            continue
        _avisar(resultado)
        estado = resultado['bit'].estado
        if estado == 'REGISTRADO':
            resumen['registrados'] += 1
        elif estado == 'PROPUESTO':
            resumen['propuestos'] += 1
        elif estado == 'ERROR':
            resumen['errores'] += 1
        else:
            resumen['sin_identificar'] += 1
    return dict(resumen)


class _Cubierto(Exception):
    """El abono lo atiende una regla de conciliación: no deja bitácora."""


def _religar(bit, mov) -> None:
    """Los renglones del estado de cuenta cambian de pk al reprocesarse: la bitácora los sigue por
    huella. Un abono ya registrado cuyo renglón quedó sin asiento se vuelve a ligar a su pago."""
    if bit.movimiento_id != mov.pk:
        bit.movimiento = mov
        bit.save(update_fields=['movimiento', 'updated_at'])
    if bit.estado == 'REGISTRADO' and bit.pago_id and not mov.movimiento_contable_id:
        linea = _linea_de_banco(bit.pago, bit.cuenta_bancaria.cuenta_contable)
        if linea is not None:
            _vincular_linea(mov, linea)


def ids_movimientos_en_bitacora() -> set:
    """Renglones que `aplicar_reglas` no debe tocar: propuestos a Dirección o ya registrados."""
    return set(AbonoIdentificado.objects.filter(estado__in=('PROPUESTO', 'REGISTRADO'), movimiento__isnull=False)
               .values_list('movimiento_id', flat=True))


def resumen_de_estado(estado_cuenta) -> dict:
    """Conteos para el aviso del estado de cuenta cargado."""
    qs = AbonoIdentificado.objects.filter(movimiento__estado_cuenta=estado_cuenta)
    return {'identificados': qs.filter(estado='REGISTRADO').count(), 'por_confirmar': qs.filter(estado='PROPUESTO').count()}


# ───────────────────────────── Decisión de Dirección ────────────────────────

def _cerrar_pendientes(bit) -> None:
    from comunicacion.models import Pendiente
    from comunicacion.services_pendientes import resolver
    for pendiente in Pendiente.objects.filter(clave_idempotencia=f'abono:{bit.huella}', estado='ABIERTO'):
        resolver(pendiente)


def registrar_propuesto(bit_id, *, telefono='', nota=None) -> Resultado:
    """Registra como `Pago` un abono PROPUESTO o con ERROR, revalidando todo bajo candado.
    Lo usan el botón de Dirección y la acción de respaldo del admin."""
    nota = nota or f'Confirmado por Dirección ({telefono or "admin"}) sobre un abono del estado de cuenta'
    try:
        with transaction.atomic():
            bit = AbonoIdentificado.objects.select_for_update().select_related('cuenta_bancaria').get(pk=bit_id)
            if bit.estado not in ('PROPUESTO', 'ERROR'):
                return Resultado(YA_ATENDIDO)
            if bit.cotizacion_id is None:
                return Resultado('Ese abono no tiene cotización propuesta; registra el pago a mano en su cotización.')
            cot = Cotizacion.objects.select_for_update().select_related('cliente').get(pk=bit.cotizacion_id)
            motivo = _motivo_no_registrable(cot, bit.importe) or _cuenta_inconsistente(bit.cuenta_bancaria)
            if motivo:
                AbonoIdentificado.objects.filter(pk=bit.pk).update(motivo=motivo[:300], updated_at=timezone.now())
                return Resultado(f'No se registró el abono: {motivo}')
            bit.decidido_por_telefono = normalizar_telefono_wa(telefono) if telefono else ''
            bit.decidido_en = timezone.now()
            bit.token_hash, bit.token_vence = '', None
            bit.save(update_fields=['decidido_por_telefono', 'decidido_en', 'token_hash', 'token_vence', 'updated_at'])
            pago = _crear_pago(bit, cot, nota)
    except ValidationError as e:
        return Resultado(f'No se registró el abono: {"; ".join(e.messages)}')
    except _SinPoliza:
        logger.exception('Abonos: el pago no generó póliza (abono %s)', bit_id)
        return Resultado(ERROR_INTERNO)
    except Exception:
        logger.exception('Abonos: no se pudo registrar el abono %s', bit_id)
        return Resultado(ERROR_INTERNO)
    _cerrar_pendientes(bit)
    saldo = max(cot.saldo_pendiente(), Decimal('0.00'))
    return Resultado(f'Listo: se registró {_dinero(pago.monto)} como pago de {folio_de(cot)}. '
                     f'Saldo pendiente: {_dinero(saldo)}.', pago=pago)


def descartar_propuesto(bit_id, *, telefono='') -> Resultado:
    """«No es»: el abono no es de ese cliente. Queda DESCARTADO y el encargado lo revisa."""
    with transaction.atomic():
        bit = AbonoIdentificado.objects.select_for_update().get(pk=bit_id)
        if bit.estado not in ('PROPUESTO', 'ERROR'):
            return Resultado(YA_ATENDIDO)
        bit.estado = 'DESCARTADO'
        bit.decidido_por_telefono = normalizar_telefono_wa(telefono) if telefono else ''
        bit.decidido_en = timezone.now()
        bit.token_hash, bit.token_vence = '', None
        bit.save(update_fields=['estado', 'decidido_por_telefono', 'decidido_en', 'token_hash', 'token_vence', 'updated_at'])
    _cerrar_pendientes(bit)
    from comunicacion.services_pendientes import registrar_pendiente
    registrar_pendiente(
        titulo=f'Abono descartado, revisar: {_dinero(bit.importe)}', rol='ENCARGADO', tipo='OTRO',
        detalle='\n'.join([f'Abono de {_dinero(bit.importe)} del {bit.fecha:%d/%m/%Y}', f'Banco: {bit.descripcion[:100]}',
                           'Dirección indicó que no es del cliente propuesto. Identifica de quién es y regístralo.']),
        objeto=bit, clave_idempotencia=f'abono_descartado:{bit.huella}')
    return Resultado('Listo: el abono quedó descartado y se avisó al encargado para identificarlo.')


def atender_boton(telefono: str, texto: str) -> Optional[Resultado]:
    """Botón `abo:<token>:ok|no`. None si no es un botón de abonos o el teléfono no es el activo de
    Dirección (se descarta sin contestar y no llega al agente)."""
    coincide = RE_BOTON.match((texto or '').strip())
    if not coincide or not direccion_activa(telefono):
        return None
    token, accion = coincide.groups()
    bit = AbonoIdentificado.objects.filter(token_hash=_hash(token)).first()
    if bit is None:
        return Resultado(BOTON_INVALIDO)
    if bit.estado != 'PROPUESTO':
        return Resultado(YA_ATENDIDO)
    if bit.token_vence is None or bit.token_vence < timezone.now():
        return Resultado(BOTON_VENCIDO)
    if accion == 'no':
        return descartar_propuesto(bit.pk, telefono=telefono)
    return registrar_propuesto(bit.pk, telefono=telefono)
