"""
Solicitud de cambio de fecha o cancelación (Fase 2c, Issue #432 · mueve dinero).

Nada cancela por sí solo. Las capas, en orden:
  1. el cliente la pide desde el portal (token) o desde Kooxi **desde el número
     registrado** de su reservación;
  2. verifica con un código de 6 dígitos al **correo registrado** (hasheado, como
     en `services_firma`): máx. 3 intentos y bloqueo temporal;
  3. Dirección recibe un análisis y tres botones. Solo vale el teléfono **activo**
     de DIRECCION, con un token de un solo uso (HMAC, 10 min) y revalidación bajo
     `select_for_update`, igual que `services_terminal`;
  4. al aprobar, el ERP cancela por `Cotizacion.cambiar_estado` y reembolsa **solo
     al medio de origen**: la tarjeta, vía el refund de Openpay sobre el cargo
     original; SPEI, efectivo o terminal quedan como pendiente de transferir a una
     cuenta a nombre del titular que verifique Dirección. Nunca a un dato que
     dicte el solicitante.

El % de penalización sale de `reglas_contrato.TABLA_CANCELACION` (fuente única).
Los textos para el cliente son fijos; el motivo que escribe el cliente solo se ve
en el admin, nunca viaja en un aviso ni lo lee un modelo.
"""
import logging
import re
import secrets
from datetime import timedelta
from decimal import Decimal
from typing import NamedTuple, Optional

from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from comunicacion.services import (
    enviar_email,
    enviar_whatsapp,
    normalizar_telefono_wa,
    remitente_por_tipo,
    reservar_comunicacion,
)
from comunicacion.services_pendientes import registrar_pendiente
from comunicacion.services_responsables import rol_de_telefono, telefono_de
from core_erp import impuestos

from . import reglas_contrato
from .disponibilidad import verificar_disponibilidad_rango
from .models import Cotizacion, OpenpayTransaccion, Pago, SolicitudCancelacion
from .services_openpay import reembolsar_monto_openpay

logger = logging.getLogger(__name__)

VIGENCIA_CODIGO = timedelta(minutes=10)
ESPERA_REENVIO = timedelta(seconds=60)
MAX_INTENTOS = 3
BLOQUEO = timedelta(minutes=30)
VIGENCIA_TOKEN = timedelta(minutes=10)
EJECUCION_ATASCADA = timedelta(minutes=5)
DIAS_ARREPENTIMIENTO = 5  # días hábiles tras la firma (Política §1, NOM-174)
METODO_TARJETA_OPENPAY = 'PLATAFORMA'  # así se registra todo cobro de Openpay

RE_BOTON = re.compile(r'^canc:([A-Za-z0-9_-]{16,64}):(ok|no|co)$')

TEXTOS = {
    'recibida': ('Recibimos tu solicitud de {que} de {folio}. Dirección la revisará y te responderá por '
                 'este medio en un máximo de 2 días hábiles. Todavía no se cambió ni se canceló nada.'),
    'cancelada': ('Aprobamos tu solicitud: {folio} quedó cancelada. {reembolso}'),
    'cambio': 'Aprobamos tu cambio de fecha: {folio} ahora es para el {fecha}. No hubo ningún cargo.',
    'rechazada': ('Revisamos tu solicitud de {que} de {folio} y por ahora no procede. Tu reservación sigue '
                  'vigente. Si tienes dudas, escríbenos.'),
    'contraoferta': ('Dirección revisará tu caso de {folio} y te contactará con una propuesta. '
                     'Todavía no se cambió ni se canceló nada.'),
    'tarjeta': ('{monto} se reembolsa a la tarjeta con la que pagaste; tu banco puede tardar de 5 a 10 días '
                'hábiles en reflejarlo.'),
    'transferencia': ('{monto} se devolverá por transferencia a una cuenta a tu nombre; te contactaremos '
                      'para verificarla. Nunca te pediremos datos bancarios por WhatsApp.'),
    'sin_reembolso': 'Conforme a la Política de Cancelación, esta cancelación no genera reembolso.',
}
QUE = {'CANCELACION': 'cancelación', 'CAMBIO_FECHA': 'cambio de fecha'}

BOTON_INVALIDO = 'Ese botón ya no es válido. Revisa la solicitud en el ERP.'
BOTON_VENCIDO = 'El botón venció (10 minutos). Revisa y resuelve la solicitud desde el ERP.'
YA_RESUELTA = 'Esa solicitud ya estaba resuelta. No se hizo nada más.'


class CancelacionError(ValueError):
    """Error con mensaje apto para mostrarse al cliente."""


class Resultado(NamedTuple):
    texto: str
    solicitud: Optional[SolicitudCancelacion] = None


def _dinero(valor) -> str:
    return f'${valor:,.2f}'


def _folio(cot) -> str:
    return f'COT-{cot.pk:03d}'


def _hash(token: str) -> str:
    return salted_hmac('comercial.cancelacion', token, algorithm='sha256').hexdigest()


def id_boton(token: str, accion: str) -> str:
    return f'canc:{token}:{accion}'


def es_boton(texto: str) -> bool:
    return bool(RE_BOTON.match((texto or '').strip()))


def _es_direccion(telefono: str) -> bool:
    """Solo el teléfono **activo** de DIRECCION; un número aún pendiente de
    «ACEPTO» no decide sobre dinero."""
    tel = normalizar_telefono_wa(telefono)
    return rol_de_telefono(tel) == 'DIRECCION' and tel == telefono_de('DIRECCION')


def es_titular(cotizacion: Cotizacion, telefono: str) -> bool:
    """El número que escribe es el registrado en la reservación (últimos 10
    dígitos, como `cotizaciones_del_telefono`)."""
    mio = ''.join(filter(str.isdigit, telefono or ''))[-10:]
    suyo = ''.join(filter(str.isdigit, cotizacion.cliente.telefono or ''))[-10:]
    return len(mio) == 10 and mio == suyo


# ───────────────────────────── 1. Pedir y verificar ─────────────────────────────

def _validar_cambio(cotizacion: Cotizacion, fecha_nueva) -> None:
    hoy = timezone.localdate()
    if fecha_nueva is None:
        raise CancelacionError('Indica la fecha a la que quieres cambiar.')
    if fecha_nueva <= hoy or fecha_nueva == cotizacion.fecha_evento:
        raise CancelacionError('Elige una fecha futura distinta a la actual.')
    fin = fecha_nueva + (cotizacion.fecha_salida - cotizacion.fecha_evento
                         if cotizacion.fecha_salida else timedelta(days=1))
    libre, mensaje = verificar_disponibilidad_rango(fecha_nueva, fin, cotizacion.pk)
    if not libre:
        raise CancelacionError(mensaje or 'Esa fecha no está disponible.')


def iniciar(cotizacion: Cotizacion, *, canal: str, tipo: str, telefono: str = '', motivo: str = '',
            fecha_nueva=None, ahora=None) -> SolicitudCancelacion:
    """Crea la solicitud y manda el código al correo registrado. No toca la
    reservación. Lanza `CancelacionError` con texto para el cliente."""
    ahora = ahora or timezone.now()
    if tipo not in QUE:
        raise CancelacionError('Indica si quieres cambiar la fecha o cancelar.')
    if cotizacion.estado != 'CONFIRMADA':
        raise CancelacionError('Solo se pueden cambiar o cancelar reservaciones confirmadas.')
    if cotizacion.fecha_evento < timezone.localdate():
        raise CancelacionError('La fecha de esta reservación ya pasó.')
    if canal == 'WHATSAPP' and not es_titular(cotizacion, telefono):
        raise CancelacionError('Por seguridad, esta solicitud solo se puede iniciar desde el número '
                               'registrado en la reservación.')
    if tipo == 'CAMBIO_FECHA':
        _validar_cambio(cotizacion, fecha_nueva)
    if not (cotizacion.cliente.email or '').strip():
        raise CancelacionError('No tenemos un correo registrado para enviarte el código. Escríbenos para agregarlo.')

    with transaction.atomic():
        abierta = (SolicitudCancelacion.objects.select_for_update()
                   .filter(cotizacion=cotizacion, estado__in=SolicitudCancelacion.ESTADOS_ABIERTOS).first())
        if abierta is not None:
            if abierta.bloqueada_hasta and abierta.bloqueada_hasta > ahora:
                raise CancelacionError('Por seguridad esta solicitud está bloqueada unos minutos. Intenta más tarde.')
            if abierta.estado != 'VERIFICANDO':
                raise CancelacionError('Ya hay una solicitud en revisión para esta reservación. '
                                       'Te responderemos en cuanto Dirección la revise.')
            abierta.tipo, abierta.canal, abierta.fecha_nueva = tipo, canal, fecha_nueva
            abierta.telefono = normalizar_telefono_wa(telefono) if telefono else abierta.telefono
            abierta.motivo = (motivo or '')[:500]
            abierta.save(update_fields=['tipo', 'canal', 'fecha_nueva', 'telefono', 'motivo', 'updated_at'])
            solicitud = abierta
        else:
            try:
                solicitud = SolicitudCancelacion.objects.create(
                    cotizacion=cotizacion, tipo=tipo, canal=canal,
                    telefono=normalizar_telefono_wa(telefono) if telefono else '',
                    motivo=(motivo or '')[:500], fecha_nueva=fecha_nueva)
            except IntegrityError as e:
                raise CancelacionError('Ya hay una solicitud en curso para esta reservación.') from e
    enviar_codigo(solicitud, ahora=ahora)
    solicitud.refresh_from_db()
    return solicitud


def enviar_codigo(solicitud: SolicitudCancelacion, ahora=None) -> str:
    """Genera y manda el código de 6 dígitos. El código nunca se guarda en claro
    ni en la bitácora de comunicaciones. Devuelve el correo destino."""
    ahora = ahora or timezone.now()
    cot = solicitud.cotizacion
    email = (cot.cliente.email or '').strip()
    with transaction.atomic():
        s = SolicitudCancelacion.objects.select_for_update().get(pk=solicitud.pk)
        if s.estado != 'VERIFICANDO':
            raise CancelacionError('Esta solicitud ya no necesita código.')
        if s.bloqueada_hasta and s.bloqueada_hasta > ahora:
            raise CancelacionError('Por seguridad esta solicitud está bloqueada unos minutos. Intenta más tarde.')
        if s.codigo_enviado_en and ahora - s.codigo_enviado_en < ESPERA_REENVIO:
            raise CancelacionError('Ya te enviamos un código. Espera un minuto para pedir otro.')
        codigo = f'{secrets.randbelow(10**6):06d}'
        s.codigo_hash = make_password(codigo)
        s.codigo_enviado_en = ahora
        s.codigo_destino = email
        s.intentos = 0
        s.save(update_fields=['codigo_hash', 'codigo_enviado_en', 'codigo_destino', 'intentos', 'updated_at'])
    asunto = f'Código para tu solicitud de {QUE[solicitud.tipo]} · {_folio(cot)}'
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.utils.html import strip_tags
    html = render_to_string('comunicacion/email/codigo_cancelacion.html', {
        'cotizacion': cot, 'codigo': codigo, 'tipo_texto': QUE[solicitud.tipo],
        'minutos': int(VIGENCIA_CODIGO.total_seconds() // 60)})
    comm = reservar_comunicacion(
        cotizacion=cot, canal='EMAIL', tipo='OTRO', trigger='MANUAL', destinatario=email, asunto=asunto,
        estado='PENDIENTE', cuerpo='Código de verificación de una solicitud de cancelación (no se guarda).')
    try:
        msg = EmailMultiAlternatives(subject=asunto, body=strip_tags(html),
                                     from_email=remitente_por_tipo('COTIZACION'), to=[email])
        msg.attach_alternative(html, 'text/html')
        msg.send(fail_silently=False)
        if comm is not None:
            comm.estado, comm.fecha_envio = 'ENVIADO', timezone.now()
            comm.save(update_fields=['estado', 'fecha_envio'])
    except Exception as e:
        logger.exception("No se pudo enviar el código de la solicitud %s", solicitud.pk)
        if comm is not None:
            comm.estado, comm.error = 'FALLIDO', str(e)[:1000]
            comm.save(update_fields=['estado', 'error'])
        raise CancelacionError('No pudimos enviar el código. Intenta de nuevo en unos minutos.') from e
    return email


def verificar(solicitud_pk: int, codigo: str, ahora=None) -> SolicitudCancelacion:
    """Valida el código (máx. 3 intentos; al tercero fallido, bloqueo). Con
    código correcto manda la solicitud a Dirección. Lanza `CancelacionError`."""
    ahora = ahora or timezone.now()
    error = None
    with transaction.atomic():
        s = SolicitudCancelacion.objects.select_for_update().select_related('cotizacion__cliente').get(pk=solicitud_pk)
        if s.estado != 'VERIFICANDO':
            error = 'Esta solicitud ya no necesita código.'
        elif s.bloqueada_hasta and s.bloqueada_hasta > ahora:
            error = 'Demasiados intentos. Por seguridad la solicitud está bloqueada unos minutos.'
        elif not s.codigo_hash or not s.codigo_enviado_en or ahora - s.codigo_enviado_en > VIGENCIA_CODIGO:
            error = 'El código venció. Pide uno nuevo.'
        elif not check_password((codigo or '').strip(), s.codigo_hash):
            s.intentos += 1
            if s.intentos >= MAX_INTENTOS:
                s.bloqueada_hasta, s.codigo_hash = ahora + BLOQUEO, ''
                error = 'Demasiados intentos. Por seguridad la solicitud se bloqueó unos minutos.'
            else:
                error = f'Código incorrecto. Te quedan {MAX_INTENTOS - s.intentos} intento(s).'
            s.save(update_fields=['intentos', 'bloqueada_hasta', 'codigo_hash', 'updated_at'])
        else:
            s.verificada_en, s.codigo_hash, s.intentos = ahora, '', 0
            s.save(update_fields=['verificada_en', 'codigo_hash', 'intentos', 'updated_at'])
    # El error se lanza fuera del `atomic()`: si no, el rollback deshace el conteo.
    if error:
        raise CancelacionError(error)
    proponer(s)
    _avisar_cliente(s, 'recibida', TEXTOS['recibida'].format(que=QUE[s.tipo], folio=_folio(s.cotizacion)),
                    asunto=f'Recibimos tu solicitud · {_folio(s.cotizacion)}')
    return SolicitudCancelacion.objects.get(pk=s.pk)


# ───────────────────────────── 2. Análisis para Dirección ───────────────────────

def _dias_habiles_desde(fecha, hasta) -> int:
    n, d = 0, fecha
    while d < hasta:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def _transacciones_tarjeta(cot: Cotizacion) -> list:
    """Cargos con tarjeta de Openpay de esta cotización (el más reciente primero)."""
    return list(OpenpayTransaccion.objects.select_related('pago').filter(
        pago__cotizacion=cot, pago__tipo='INGRESO', pago__concepto='VENTA', metodo='card',
        destino='SERVICIO').order_by('-pago__fecha_pago', '-pago_id'))


def _pool_tarjeta(cot: Cotizacion, transacciones) -> Decimal:
    """Lo cobrado con tarjeta Openpay que aún no se reembolsó."""
    cobrado = sum((t.pago.monto for t in transacciones), Decimal('0.00'))
    devuelto = sum((p.monto for p in cot.pagos.filter(tipo='REEMBOLSO', metodo=METODO_TARJETA_OPENPAY)),
                   Decimal('0.00'))
    return max(cobrado - devuelto, Decimal('0.00'))


def calcular(cot: Cotizacion, solicitud: SolicitudCancelacion, hoy=None) -> dict:
    """Análisis de una cancelación. Todo `Decimal`/ROUND_HALF_UP; los importes
    van como texto para guardarse en JSON."""
    hoy = hoy or timezone.localdate()
    dias = (cot.fecha_evento - hoy).days
    pagado = impuestos.centavos(cot.total_pagado_neto())
    contrato = cot.contratos.exclude(archivo='').order_by('-generado_en').first()
    firma = getattr(contrato, 'firma', None) if contrato else None
    arrepentimiento = bool(firma and firma.firmado_en and
                           _dias_habiles_desde(timezone.localtime(firma.firmado_en).date(), hoy)
                           <= DIAS_ARREPENTIMIENTO)
    tramo, pct_pen, pct_reem = reglas_contrato.condicion_cancelacion(cot.tipo_servicio, dias, solicitud.fuerza_mayor)
    if arrepentimiento:
        tramo, pct_pen, pct_reem = 'Dentro de los 5 días hábiles posteriores a la firma', Decimal('0'), Decimal('100')
    if cot.tipo_servicio == 'HOSPEDAJE' or arrepentimiento:
        penalizacion = impuestos.centavos(pagado * pct_pen / 100)
        reembolso = impuestos.centavos(pagado * pct_reem / 100)
    else:
        penalizacion = impuestos.centavos((cot.precio_final or Decimal('0')) * pct_pen / 100)
        reembolso = max(impuestos.centavos(pagado - penalizacion), Decimal('0.00'))
    reembolso = min(reembolso, pagado)
    transacciones = _transacciones_tarjeta(cot)
    a_tarjeta = min(reembolso, _pool_tarjeta(cot, transacciones))
    otras = OpenpayTransaccion.objects.filter(pago__cotizacion=cot).exclude(metodo='card').count()
    return {
        'dias_al_evento': dias, 'tramo': tramo, 'penalizacion_pct': str(pct_pen), 'reembolso_pct': str(pct_reem),
        'penalizacion': str(penalizacion), 'precio_final': str(cot.precio_final), 'pagado': str(pagado),
        'reembolso': str(reembolso), 'a_tarjeta': str(a_tarjeta), 'a_transferir': str(reembolso - a_tarjeta),
        'arrepentimiento_5_dias': arrepentimiento, 'fuerza_mayor': solicitud.fuerza_mayor,
        'pagos': cot.pagos.filter(tipo='INGRESO', concepto='VENTA').count(),
        'pagos_no_tarjeta_openpay': otras,
        'reembolsos_previos': str(cot.total_reembolsado()),
        'solicitudes_previas': SolicitudCancelacion.objects.filter(cotizacion=cot).exclude(pk=solicitud.pk).count(),
        'demanda_misma_fecha': Cotizacion.objects.filter(
            fecha_evento=cot.fecha_evento, estado__in=('BORRADOR', 'COTIZADA')).exclude(pk=cot.pk).count(),
        'revendible': 'alta' if dias >= 30 else 'media' if dias >= 15 else 'baja',
    }


def _titulo(s: SolicitudCancelacion) -> str:
    return f'{s.get_tipo_display()} {_folio(s.cotizacion)}'


def proponer(solicitud: SolicitudCancelacion) -> None:
    """Calcula, deja la solicitud en ANALISIS con un token nuevo y abre el
    pendiente URGENTE con los botones para Dirección. Nunca lanza."""
    try:
        with transaction.atomic():
            s = SolicitudCancelacion.objects.select_for_update().select_related('cotizacion__cliente').get(
                pk=solicitud.pk)
            cot = s.cotizacion
            token = secrets.token_urlsafe(24)
            if s.tipo == 'CANCELACION':
                s.analisis = calcular(cot, s)
                s.monto_reembolso = Decimal(s.analisis['reembolso'])
            else:
                s.analisis = {'dias_al_evento': (cot.fecha_evento - timezone.localdate()).days,
                              'fecha_actual': cot.fecha_evento.isoformat(), 'fecha_nueva': s.fecha_nueva.isoformat()}
                s.monto_reembolso = Decimal('0.00')
            s.estado, s.token_hash, s.token_vence_en = 'ANALISIS', _hash(token), timezone.now() + VIGENCIA_TOKEN
            s.save(update_fields=['analisis', 'monto_reembolso', 'estado', 'token_hash', 'token_vence_en', 'updated_at'])
    except Exception:
        logger.exception("Solicitud de cancelación %s: no se pudo preparar el análisis", solicitud.pk)
        return
    a = s.analisis
    if s.tipo == 'CANCELACION':
        detalle = (f'{cot.cliente}. Faltan {a["dias_al_evento"]} días. Pagado {_dinero(Decimal(a["pagado"]))}, '
                   f'penalización {_dinero(Decimal(a["penalizacion"]))}. '
                   f'Reembolso propuesto {_dinero(s.monto_reembolso)}.')
        botones = [(id_boton(token, 'ok'), f'Aprobar {_dinero(s.monto_reembolso)}'[:20]),
                   (id_boton(token, 'no'), 'Rechazar'), (id_boton(token, 'co'), 'Contraoferta')]
    else:
        detalle = f'{cot.cliente}. Pide pasar del {a["fecha_actual"]} al {a["fecha_nueva"]}. Sin cargo.'
        botones = [(id_boton(token, 'ok'), 'Aprobar cambio'), (id_boton(token, 'no'), 'Rechazar'),
                   (id_boton(token, 'co'), 'Contraoferta')]
    registrar_pendiente(
        titulo=f'Solicitud de {QUE[s.tipo]} {_folio(cot)}', rol='DIRECCION', tipo='OTRO', severidad='URGENTE',
        detalle=detalle, objeto=s, clave_idempotencia=f'cancelacion:{s.pk}', botones=botones)


# ───────────────────────────── 3. Decisión de Dirección ─────────────────────────

def atender_boton(telefono: str, texto: str) -> Optional[Resultado]:
    """Botón `canc:<token>:<ok|no|co>`. None si no es un botón de cancelación o
    el teléfono no es el activo de DIRECCION (se descarta sin contestar)."""
    coincide = RE_BOTON.match((texto or '').strip())
    if not coincide or not _es_direccion(telefono):
        return None
    tel = normalizar_telefono_wa(telefono)
    pk = (SolicitudCancelacion.objects.filter(token_hash=_hash(coincide.group(1)))
          .values_list('pk', flat=True).first())
    if pk is None:
        return Resultado(BOTON_INVALIDO)
    return decidir(pk, coincide.group(2), quien=f'DIRECCION {tel}', token=coincide.group(1))


def decidir(solicitud_pk: int, accion: str, *, quien: str, token: Optional[str] = None, usuario=None) -> Resultado:
    """Aplica la decisión. Con `token` (WhatsApp) exige que sea el vigente; sin
    él (admin) el llamador ya comprobó que es superusuario. Idempotente: una
    solicitud que ya no está en ANALISIS no vuelve a ejecutarse."""
    revalidada = None
    with transaction.atomic():
        s = SolicitudCancelacion.objects.select_for_update().select_related('cotizacion__cliente').get(pk=solicitud_pk)
        if s.estado != 'ANALISIS':
            return Resultado(YA_RESUELTA, s)
        if token is not None:
            if not s.token_hash or s.token_hash != _hash(token):
                return Resultado(BOTON_INVALIDO, s)
            if s.token_vence_en and s.token_vence_en < timezone.now():
                return Resultado(BOTON_VENCIDO, s)
        cot = Cotizacion.objects.select_for_update().get(pk=s.cotizacion_id)
        if accion == 'ok':
            motivo = _no_procede(cot, s)
            if motivo:
                return Resultado(motivo, s)
            if s.tipo == 'CANCELACION':
                nuevo = calcular(cot, s)
                if Decimal(nuevo['reembolso']) != s.monto_reembolso:
                    revalidada = nuevo
        if revalidada is None:
            nuevo_estado = {'ok': 'APROBADA', 'no': 'RECHAZADA', 'co': 'CONTRAOFERTA'}[accion]
            s.estado, s.token_hash, s.token_vence_en = nuevo_estado, '', None
            s.resuelta_en, s.resuelta_por, s.updated_by = timezone.now(), quien[:60], (
                usuario if getattr(usuario, 'pk', None) else None)
            s.save(update_fields=['estado', 'token_hash', 'token_vence_en', 'resuelta_en', 'resuelta_por',
                                  'updated_by', 'updated_at'])
    if revalidada is not None:
        # Cambió el saldo entre la propuesta y el botón: no se ejecuta con una
        # cifra vieja; se manda una propuesta nueva con otro token.
        proponer(s)
        return Resultado('El saldo cambió desde la propuesta. Te mando una propuesta nueva con el monto actualizado.', s)
    folio = _folio(s.cotizacion)
    if accion == 'no':
        _avisar_cliente(s, 'rechazada', TEXTOS['rechazada'].format(que=QUE[s.tipo], folio=folio),
                        asunto=f'Tu solicitud · {folio}')
        return Resultado(f'Listo: rechazaste la solicitud de {folio}. Se avisó al cliente.', s)
    if accion == 'co':
        _avisar_cliente(s, 'contraoferta', TEXTOS['contraoferta'].format(folio=folio), asunto=f'Tu solicitud · {folio}')
        registrar_pendiente(
            titulo=f'Contraoferta pendiente {folio}', rol='DIRECCION', tipo='OTRO', severidad='NORMAL',
            detalle='Contacta al cliente con una propuesta (otra fecha, crédito o ajuste). No se movió dinero.',
            objeto=s, clave_idempotencia=f'cancelacion:{s.pk}:contraoferta')
        return Resultado(f'Quedó como contraoferta {folio}: contacta al cliente. No se movió dinero.', s)
    return ejecutar(s.pk, usuario=usuario)


def _no_procede(cot: Cotizacion, s: SolicitudCancelacion) -> Optional[str]:
    if cot.estado != 'CONFIRMADA':
        return f'{_folio(cot)} ya está {cot.get_estado_display().lower()}: no se ejecutó nada.'
    if s.tipo == 'CAMBIO_FECHA':
        try:
            _validar_cambio(cot, s.fecha_nueva)
        except CancelacionError as e:
            return f'El cambio ya no procede: {e}'
    return None


# ───────────────────────────── 4. Ejecución ─────────────────────────────────────

def ejecutar(solicitud_pk: int, usuario=None) -> Resultado:
    """Ejecuta una solicitud APROBADA (o reintenta una con error). Idempotente:
    el estado EJECUTANDO se toma bajo `select_for_update` y cada reembolso de
    tarjeta lleva una referencia propia; si Openpay pudo haber devuelto el dinero
    pero el ERP no lo registró, **no** reintenta solo (evita devolver dos veces)."""
    ahora = timezone.now()
    with transaction.atomic():
        s = SolicitudCancelacion.objects.select_for_update().select_related('cotizacion__cliente').get(pk=solicitud_pk)
        atascada = s.estado == 'EJECUTANDO' and s.updated_at < ahora - EJECUCION_ATASCADA
        if s.estado not in ('APROBADA', 'ERROR') and not atascada:
            return Resultado(YA_RESUELTA, s)
        s.estado = 'EJECUTANDO'
        s.updated_by = usuario if getattr(usuario, 'pk', None) else s.updated_by
        s.save(update_fields=['estado', 'updated_by', 'updated_at'])
    try:
        if s.tipo == 'CAMBIO_FECHA':
            return _ejecutar_cambio(s)
        return _ejecutar_cancelacion(s, usuario)
    except Exception as e:
        logger.exception("Solicitud de cancelación %s: falló la ejecución", s.pk)
        return _fallo(s, f'Error inesperado: {e}')


def _fallo(s: SolicitudCancelacion, detalle: str) -> Resultado:
    s.refresh_from_db()
    s.estado = 'ERROR'
    s.detalle_ejecucion = (s.detalle_ejecucion + '\n' + detalle).strip()[:3000]
    s.save(update_fields=['estado', 'detalle_ejecucion', 'updated_at'])
    registrar_pendiente(
        titulo=f'Revisar ejecución de {_folio(s.cotizacion)}', rol='DIRECCION', tipo='OTRO', severidad='URGENTE',
        detalle=f'La ejecución quedó a medias: {detalle[:200]} Revisa en el ERP: {url_pendiente_solicitud(s)}',
        objeto=s, clave_idempotencia=f'cancelacion:{s.pk}:error:{s.updated_at:%Y%m%d%H%M}')
    return Resultado(f'No se pudo completar {_folio(s.cotizacion)}: {detalle[:160]} Quedó para revisión.', s)


def url_pendiente_solicitud(s: SolicitudCancelacion) -> str:
    from django.urls import reverse

    from comunicacion.services_pendientes import _base_url
    return _base_url() + reverse('admin:comercial_solicitudcancelacion_change', args=[s.pk])


def _ejecutar_cambio(s: SolicitudCancelacion) -> Resultado:
    with transaction.atomic():
        cot = Cotizacion.objects.select_for_update().get(pk=s.cotizacion_id)
        motivo = _no_procede(cot, s)
        if motivo:
            return _fallo(s, motivo)
        delta = (cot.fecha_salida - cot.fecha_evento) if cot.fecha_salida else None
        campos = {'fecha_evento': s.fecha_nueva, 'updated_at': timezone.now()}
        if delta is not None:
            campos['fecha_salida'] = s.fecha_nueva + delta
        # `update()` y no `save()`: mover la fecha no debe recotizar nada, el
        # precio ya está pactado.
        Cotizacion.objects.filter(pk=cot.pk).update(**campos)
        s.estado, s.detalle_ejecucion = 'EJECUTADA', f'Fecha movida de {cot.fecha_evento} a {s.fecha_nueva}.'
        s.save(update_fields=['estado', 'detalle_ejecucion', 'updated_at'])
    cot.refresh_from_db()
    _avisar_cliente(s, 'cambio', TEXTOS['cambio'].format(folio=_folio(cot), fecha=s.fecha_nueva.strftime('%d/%m/%Y')),
                    asunto=f'Cambio de fecha aprobado · {_folio(cot)}')
    return Resultado(f'Listo: {_folio(cot)} se movió al {s.fecha_nueva:%d/%m/%Y}. Se avisó al cliente.', s)


def _ejecutar_cancelacion(s: SolicitudCancelacion, usuario) -> Resultado:
    cot = Cotizacion.objects.get(pk=s.cotizacion_id)
    folio = _folio(cot)
    registro = dict(s.analisis.get('ejecucion', {}))
    restante = s.monto_reembolso
    reembolsado_tarjeta = Decimal('0.00')
    if cot.estado == 'CONFIRMADA' and restante > 0:
        transacciones = _transacciones_tarjeta(cot)
        pool = _pool_tarjeta(cot, transacciones)
        # Lo devuelto por esta solicitud en intentos anteriores ya no está en el pool.
        for t in transacciones:
            ref = f'CANC-{s.pk}-{t.pago_id}'
            previo = cot.pagos.filter(tipo='REEMBOLSO', referencia=ref).first()
            if previo is not None:
                restante -= previo.monto
                reembolsado_tarjeta += previo.monto
                continue
            if restante <= 0 or pool <= 0:
                continue
            if registro.get(ref, {}).get('estado') == 'iniciado':
                return _fallo(s, f'El reembolso {ref} se mandó a Openpay pero no quedó registrado. '
                                 'Verifica en Openpay si salió antes de reintentar; no se repite solo.')
            monto = impuestos.centavos(min(restante, t.pago.monto, pool))
            registro[ref] = {'estado': 'iniciado', 'monto': str(monto), 'cargo': t.openpay_id}
            _guardar_registro(s, registro)
            respuesta = reembolsar_monto_openpay(t, monto, descripcion=f'Cancelación {folio}')
            if not respuesta['ok']:
                registro[ref]['estado'] = 'rechazado'
                _guardar_registro(s, registro)
                return _fallo(s, f'Openpay rechazó el reembolso de {_dinero(monto)}: {respuesta["mensaje"]}')
            Pago.objects.create(
                cotizacion=cot, tipo='REEMBOLSO', concepto='VENTA', metodo=t.pago.metodo, monto=monto,
                fecha_pago=timezone.localdate(), referencia=ref,
                notas=f'Reembolso por cancelación aprobada ({s.resuelta_por}) a la tarjeta de origen {t.openpay_id}',
                usuario=usuario if getattr(usuario, 'pk', None) else None)
            registro[ref]['estado'] = 'ok'
            _guardar_registro(s, registro)
            restante -= monto
            pool -= monto
            reembolsado_tarjeta += monto
    transferir = max(restante, Decimal('0.00'))
    if cot.estado != 'CANCELADA':
        ok, mensaje = cot.cambiar_estado(
            'CANCELADA', usuario=usuario if getattr(usuario, 'pk', None) else None,
            motivo=f'Solicitud de cancelación #{s.pk} aprobada por Dirección')
        if not ok:
            return _fallo(s, f'No se pudo cancelar la cotización: {mensaje}')
    detalle = [f'Cancelada. Reembolsado a la tarjeta de origen: {_dinero(reembolsado_tarjeta)}.']
    if transferir > 0:
        detalle.append(f'Pendiente de transferir: {_dinero(transferir)} (cuenta del titular verificada por Dirección).')
        registrar_pendiente(
            titulo=f'Transferir reembolso {_dinero(transferir)} de {folio}', rol='DIRECCION', tipo='OTRO',
            severidad='URGENTE',
            detalle=('Verifica que la cuenta esté a nombre del titular (no uses datos dictados por WhatsApp), '
                     'transfiere y registra el reembolso en el pago. '
                     f'{url_pendiente_solicitud(s)}'),
            objeto=s, clave_idempotencia=f'cancelacion:{s.pk}:transferir')
    s.refresh_from_db()
    s.estado = 'EJECUTADA'
    s.detalle_ejecucion = (s.detalle_ejecucion + '\n' + ' '.join(detalle)).strip()
    s.save(update_fields=['estado', 'detalle_ejecucion', 'updated_at'])
    partes = []
    if reembolsado_tarjeta > 0:
        partes.append(TEXTOS['tarjeta'].format(monto=_dinero(reembolsado_tarjeta)))
    if transferir > 0:
        partes.append(TEXTOS['transferencia'].format(monto=_dinero(transferir)))
    if not partes:
        partes.append(TEXTOS['sin_reembolso'])
    _avisar_cliente(s, 'cancelada', TEXTOS['cancelada'].format(folio=folio, reembolso=' '.join(partes)),
                    asunto=f'Cancelación aprobada · {folio}')
    return Resultado(f'Listo: {folio} cancelada. ' + ' '.join(detalle), s)


def _guardar_registro(s: SolicitudCancelacion, registro: dict) -> None:
    """Guarda el avance de los reembolsos de tarjeta **antes y después** de cada
    llamada a Openpay: es lo que impide devolver dos veces en un reintento."""
    analisis = dict(s.analisis)
    analisis['ejecucion'] = registro
    SolicitudCancelacion.objects.filter(pk=s.pk).update(analisis=analisis, updated_at=timezone.now())
    s.analisis = analisis


# ───────────────────────────── Avisos al cliente ────────────────────────────────

def _avisar_cliente(s: SolicitudCancelacion, clave: str, texto: str, *, asunto: str) -> None:
    """Texto fijo por correo y, si hay número, por WhatsApp. Nunca lanza."""
    cot = s.cotizacion
    try:
        enviar_email(
            cotizacion=cot, tipo='CANCELACION', destinatario=cot.cliente.email, asunto=asunto,
            template='comunicacion/email/aviso_solicitud.html',
            context={'nombre': cot.cliente.nombre, 'titulo': asunto, 'texto': texto, 'asunto': asunto},
            clave_idempotencia=f'cancelacion:{s.pk}:{clave}:email')
        telefono = s.telefono or cot.cliente.telefono
        if telefono:
            enviar_whatsapp(cotizacion=cot, tipo='CANCELACION', telefono=telefono, mensaje=texto,
                            clave_idempotencia=f'cancelacion:{s.pk}:{clave}:wa')
    except Exception:
        logger.exception("Solicitud de cancelación %s: no se pudo avisar al cliente", s.pk)
