"""Cortes y pago de comisiones del personal de ventas (Issue #505, PR 4). MUEVE DINERO.

Visitas: corte semanal, se paga el domingo. Comisiones: corte quincenal (días 15 y último del mes),
se paga el domingo siguiente. Un corte nace en BORRADOR con un `Pendiente` a Dirección; aprobar no
paga; pagar (solo Dirección) deja la póliza de gasto no deducible (`contabilidad.signals`).

Compensación (solo comisiones): `descuento = min(saldo_en_contra, centavos(positivo × tope))`; el resto
se arrastra y un corte nunca queda negativo. Nunca se le pide dinero a la persona. No existe ninguna
función que compense comisiones contra efectivo de clientes (`EntregaEfectivo` es solo control).
"""
import logging
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from core_erp.impuestos import centavos
from nomina.models import CondicionesPago, Empleado
from nomina.services_expediente import condiciones_vigentes

from .models import CERO, CorteComision, MovimientoComision

logger = logging.getLogger(__name__)

TIPOS_MOVIMIENTO = {'VISITAS': ('VISITA',), 'COMISIONES': ('CONTRATACION', 'AJUSTE')}
TOPE_VISITAS = Decimal('1')  # un corte de visitas se compensa por completo: nunca queda negativo
TOPE_POR_DEFECTO = CondicionesPago._meta.get_field('tope_compensacion').default


# --- Periodos --------------------------------------------------------------------------------

def periodo_visitas(hoy: date):
    """Semana lunes–domingo que cierra `hoy` (el corte corre el domingo)."""
    return hoy - timedelta(days=hoy.weekday()), hoy


def periodo_comisiones(hoy: date, periodicidad: str = 'QUINCENAL'):
    """Periodo cerrado más reciente: del 16 a fin de mes (corte el 1) o del 1 al 15 (corte el 16).
    MENSUAL solo corta el día 1; SEMANAL se trata como quincenal (única periodicidad definida)."""
    if hoy.day >= 16:
        if periodicidad == 'MENSUAL':
            return None
        return hoy.replace(day=1), hoy.replace(day=15)
    anterior = hoy.replace(day=1) - timedelta(days=1)
    desde = anterior.replace(day=1) if periodicidad == 'MENSUAL' else anterior.replace(day=16)
    return desde, anterior.replace(day=monthrange(anterior.year, anterior.month)[1])


def fecha_pago_programada(corte) -> date:
    """Visitas: el domingo del corte. Comisiones: el domingo siguiente al cierre del periodo."""
    dias = (6 - corte.hasta.weekday()) % 7
    if dias == 0 and corte.tipo == 'COMISIONES':
        dias = 7
    return corte.hasta + timedelta(days=dias)


# --- Creación -----------------------------------------------------------------------------------

def _saldo_previo(vendedor, tipo) -> Decimal:
    ultimo = (CorteComision.objects.filter(vendedor=vendedor, tipo=tipo).exclude(estado='CANCELADO')
              .order_by('-hasta', '-id').first())
    return ultimo.saldo_arrastrado if ultimo else CERO


def _usuario(usuario):
    return usuario if getattr(usuario, 'pk', None) else None


def crear_corte(vendedor, tipo, desde, hasta, usuario=None):
    """Corta lo devengado y aún sin cortar hasta `hasta`. Idempotente por (vendedor, tipo, desde, hasta):
    una segunda corrida devuelve el corte vivo. None si no hay movimientos que cortar."""
    corte = None
    with transaction.atomic():
        vendedor = Empleado.objects.select_for_update().get(pk=getattr(vendedor, 'pk', vendedor))
        vivo = CorteComision.objects.filter(vendedor=vendedor, tipo=tipo, desde=desde, hasta=hasta).exclude(
            estado='CANCELADO').first()
        if vivo is not None:
            return vivo
        movs = list(MovimientoComision.objects.filter(
            vendedor=vendedor, corte__isnull=True, fecha__lte=hasta, tipo__in=TIPOS_MOVIMIENTO[tipo]))
        if not movs:
            return None
        positivos = sum((m.monto for m in movs if m.monto > 0 and not m.absorcion), CERO)
        negativos = -sum((m.monto for m in movs if m.monto < 0 and not m.absorcion), CERO)
        absorciones = sum((m.monto for m in movs if m.absorcion), CERO)
        saldo = max(_saldo_previo(vendedor, tipo) + negativos - absorciones, CERO)
        if tipo == 'COMISIONES':
            condiciones = condiciones_vigentes(vendedor, hasta)
            tope = condiciones.tope_compensacion if condiciones else TOPE_POR_DEFECTO
        else:
            tope = TOPE_VISITAS
        descuento = min(saldo, centavos(positivos * tope))
        arrastrado = saldo - descuento
        absorbido = CERO
        if vendedor.fecha_baja is not None and vendedor.fecha_baja <= hasta:
            absorbido, arrastrado = arrastrado, CERO  # baja: el saldo en contra se absorbe como costo
        try:
            corte = CorteComision.objects.create(
                vendedor=vendedor, tipo=tipo, desde=min([desde] + [m.fecha for m in movs]), hasta=hasta,
                bruto=positivos, compensado=descuento, total=positivos - descuento,
                saldo_arrastrado=arrastrado, absorbido=absorbido, created_by=_usuario(usuario))
        except IntegrityError:
            return CorteComision.objects.filter(vendedor=vendedor, tipo=tipo, desde=desde, hasta=hasta).exclude(
                estado='CANCELADO').first()
        MovimientoComision.objects.filter(pk__in=[m.pk for m in movs]).update(corte=corte)
        if corte.total <= 0:
            # Nada que pagar: se cierra solo para liberar el periodo (sin póliza ni aviso de pago).
            CorteComision.objects.filter(pk=corte.pk).update(
                estado='PAGADO', pagado_en=timezone.now(), referencia='Sin pago: total $0.00')
            corte.refresh_from_db()
            return corte
    _avisar_a_direccion(corte)
    return corte


def _avisar_a_direccion(corte):
    from comunicacion.services_pendientes import registrar_pendiente

    manuales = _visitas_manuales(corte)
    lineas = [f'Persona: {corte.vendedor.nombre}', f'Periodo: {corte.desde:%d/%m/%Y} al {corte.hasta:%d/%m/%Y}',
              f'Total a pagar: ${corte.total:,.2f}',
              f'Pago programado: {fecha_pago_programada(corte):%d/%m/%Y}']
    if corte.compensado:
        lineas.append(f'Compensado de saldo en contra: ${corte.compensado:,.2f}')
    if manuales:
        lineas.append(f'Visitas capturadas a mano: {len(manuales)} (revísalas antes de aprobar)')
    registrar_pendiente(
        titulo=f'Aprobar corte de {corte.get_tipo_display().lower()} de {corte.vendedor.nombre}',
        rol='DIRECCION', tipo='OTRO', severidad='URGENTE', detalle='\n'.join(lineas), objeto=corte,
        clave_idempotencia=f'corte:{corte.pk}', botones=[(f'cor:{corte.pk}:ok', 'Aprobar')],
        etiqueta_ok='Aprobado')


def generar_cortes(tipo, hoy=None):
    """Corte de todos los vendedores con movimientos sin cortar (tarea del programador)."""
    hoy = hoy or timezone.localdate()
    creados = []
    vendedores = Empleado.objects.filter(
        movimientos_comision__corte__isnull=True, movimientos_comision__tipo__in=TIPOS_MOVIMIENTO[tipo]).distinct()
    for vendedor in vendedores:
        try:
            if tipo == 'VISITAS':
                periodo = periodo_visitas(hoy)
            else:
                condiciones = condiciones_vigentes(vendedor, hoy)
                periodo = periodo_comisiones(hoy, condiciones.periodicidad_comisiones if condiciones else 'QUINCENAL')
            if periodo is None:
                continue
            corte = crear_corte(vendedor, tipo, *periodo)
            if corte is not None:
                creados.append(corte)
        except Exception:
            logger.exception('Ventas: no se pudo crear el corte de %s del vendedor %s', tipo, vendedor.pk)
    return creados


# --- Aprobación, pago y cancelación ----------------------------------------------------------------

def aprobar_corte(corte, usuario=None, telefono='') -> bool:
    """BORRADOR → APROBADO. Aprobar no paga. Solo un superusuario o el teléfono de Dirección
    (validado por quien llama). Devuelve False si ya no estaba en borrador."""
    if usuario is not None and not getattr(usuario, 'is_superuser', False):
        raise PermissionDenied('Solo Dirección aprueba cortes.')
    with transaction.atomic():
        corte = CorteComision.objects.select_for_update().get(pk=corte.pk)
        if corte.estado != 'BORRADOR':
            return False
        corte.estado = 'APROBADO'
        corte.aprobado_en = timezone.now()
        corte.aprobado_por = _usuario(usuario)
        corte.aprobado_por_telefono = telefono or ''
        corte.updated_by = _usuario(usuario)
        corte.save()
    _resolver_pendiente(corte, usuario)
    return True


def _resolver_pendiente(corte, usuario=None):
    from comunicacion.models import Pendiente
    from comunicacion.services_pendientes import resolver

    pendiente = Pendiente.objects.filter(clave_idempotencia=f'corte:{corte.pk}').first()
    if pendiente is not None:
        try:
            resolver(pendiente, usuario)
        except Exception:
            logger.exception('Ventas: no se pudo resolver el pendiente del corte %s', corte.pk)


def marcar_pagado(corte, usuario, metodo, cuenta_bancaria=None, referencia=''):
    """APROBADO → PAGADO (solo Dirección). La póliza se genera en la misma transacción; si no puede
    asentarse, el pago no queda registrado (nunca dinero sin rastro)."""
    from contabilidad.signals import poliza_de_corte

    if not getattr(usuario, 'is_superuser', False):
        raise PermissionDenied('Solo Dirección marca pagos de comisiones.')
    if metodo not in dict(CorteComision.METODOS):
        raise ValidationError('Elige cómo se pagó: efectivo o transferencia.')
    if metodo == 'EFECTIVO' and cuenta_bancaria is not None:
        raise ValidationError('Un pago en efectivo no lleva cuenta bancaria.')
    if cuenta_bancaria is not None and (not cuenta_bancaria.activa or not cuenta_bancaria.cuenta_contable_id):
        raise ValidationError('La cuenta debe estar activa y ligada a una cuenta contable.')
    with transaction.atomic():
        corte = CorteComision.objects.select_for_update().get(pk=corte.pk)
        if corte.estado != 'APROBADO':
            raise ValidationError('Solo se paga un corte aprobado.')
        corte.estado = 'PAGADO'
        corte.pagado_en = timezone.now()
        corte.pagado_por = usuario
        corte.metodo = metodo
        corte.cuenta_bancaria = cuenta_bancaria
        corte.referencia = (referencia or '')[:100]
        corte.updated_by = usuario
        corte.save()  # el signal de contabilidad asienta la póliza
        if corte.total > 0 and poliza_de_corte(corte) is None:
            raise ValidationError('No se pudo generar la póliza (revisa Caja/Bancos y la operación '
                                  'COMISIONES_VENTA en la Configuración contable). El pago no se registró.')
        guardar_pdf(corte)
    notificar_pago(corte)
    return corte


def cancelar_pago(corte, usuario):
    """PAGADO → APROBADO (solo Dirección): cancela la póliza; los movimientos siguen ligados al corte."""
    if not getattr(usuario, 'is_superuser', False):
        raise PermissionDenied('Solo Dirección cancela pagos.')
    with transaction.atomic():
        corte = CorteComision.objects.select_for_update().get(pk=corte.pk)
        if corte.estado != 'PAGADO' or corte.total <= 0:
            raise ValidationError('Solo se cancela el pago de un corte pagado.')
        corte.estado = 'APROBADO'
        corte.pagado_en = None
        corte.pagado_por = None
        corte.updated_by = usuario
        corte.save()  # el signal cancela la póliza
    return corte


def cancelar_corte(corte, usuario=None):
    """Cancela un corte aún no pagado y libera sus movimientos para el siguiente."""
    if usuario is not None and not getattr(usuario, 'is_superuser', False):
        raise PermissionDenied('Solo Dirección cancela cortes.')
    with transaction.atomic():
        corte = CorteComision.objects.select_for_update().get(pk=corte.pk)
        if corte.estado not in ('BORRADOR', 'APROBADO'):
            raise ValidationError('Solo se cancela un corte en borrador o aprobado.')
        corte.estado = 'CANCELADO'
        corte.updated_by = _usuario(usuario)
        corte.save()
        MovimientoComision.objects.filter(corte=corte).update(corte=None)
    _resolver_pendiente(corte, usuario)
    return corte


# --- Estado de comisiones (PDF y lectura para el PR 5) -----------------------------------------------

def _visitas_manuales(corte):
    return [m for m in corte.movimientos.select_related('visita') if m.visita_id and m.visita.origen == 'MANUAL']


def filas_del_corte(corte):
    filas = []
    for m in corte.movimientos.select_related('visita', 'comision').order_by('fecha', 'id'):
        if m.visita_id:
            visita = m.visita
            hora = timezone.localtime(visita.inicio)
            concepto = f'Visita de {visita.nombre} ({hora:%H:%M})'
        elif m.comision_id:
            concepto = f'COT-{m.comision.cotizacion_id:03d}' + (' · absorción' if m.absorcion else '')
        else:
            concepto = m.get_tipo_display()
        filas.append({'fecha': m.fecha, 'concepto': concepto, 'detalle': m.detalle, 'monto': m.monto,
                      'manual': bool(m.visita_id and m.visita.origen == 'MANUAL')})
    return filas


def generar_pdf(corte) -> bytes:
    from core_erp.documentos import render_pdf

    return render_pdf('ventas/estado_comisiones.html', {
        'corte': corte, 'vendedor': corte.vendedor, 'filas': filas_del_corte(corte),
        'fecha_pago': fecha_pago_programada(corte)})


def nombre_pdf(corte) -> str:
    from core_erp.documentos import nombre_archivo

    return nombre_archivo('Comisiones', corte.vendedor.nombre, f'{corte.desde:%Y%m%d}-{corte.hasta:%Y%m%d}')


def guardar_pdf(corte):
    corte.archivo_pdf.save(nombre_pdf(corte), ContentFile(generar_pdf(corte)), save=False)
    CorteComision.objects.filter(pk=corte.pk).update(archivo_pdf=corte.archivo_pdf.name)


def notificar_pago(corte):
    """WhatsApp con el total y PDF por correo a la persona. Nunca lanza."""
    try:
        from comunicacion.formato_wa import Aviso
        from comunicacion.services import enviar_email
        from comunicacion.services_pendientes import _enviar_aviso

        vendedor = corte.vendedor
        if corte.total <= 0:
            return
        if vendedor.telefono:
            aviso = Aviso(titulo=f'Te pagamos tu {corte.get_tipo_display().lower()}', categoria='Pago',
                          lineas=[f'Periodo: {corte.desde:%d/%m/%Y} al {corte.hasta:%d/%m/%Y}',
                                  f'Total: ${corte.total:,.2f}'],
                          cierre='Te llega tu estado de comisiones por correo.')
            _enviar_aviso(vendedor.telefono, aviso, clave=f'corte:{corte.pk}:pago:wa')
        if vendedor.email:
            pdf = corte.archivo_pdf.read() if corte.archivo_pdf else generar_pdf(corte)
            enviar_email(
                tipo='OTRO', trigger='MANUAL', destinatario=vendedor.email,
                asunto=f'Tu estado de {corte.get_tipo_display().lower()} ({corte.desde:%d/%m} al {corte.hasta:%d/%m})',
                template='comunicacion/email/estado_comisiones.html',
                context={'corte': corte, 'vendedor': vendedor},
                adjuntos=[(nombre_pdf(corte), pdf, 'application/pdf')],
                clave_idempotencia=f'corte:{corte.pk}:pago:email')
    except Exception:
        logger.exception('Ventas: no se pudo avisar el pago del corte %s', corte.pk)
