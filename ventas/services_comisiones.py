"""Motor de comisiones del personal de ventas (Issue #505, PR 3). MUEVE DINERO.

Todo monto y porcentaje sale de las `CondicionesPago` vigentes en la fecha del hecho y se
sella en `ComisionContratacion`; nada de cifras fijas aquí. Decimal + ROUND_HALF_UP vía
`core_erp.impuestos.centavos`.

Idempotencia: cada cambio emite un movimiento por la DIFERENCIA entre el objetivo y lo ya
devengado, bajo `select_for_update` sobre el Empleado y con clave única por movimiento; ejecutar
dos veces (señal + cron + reintento) no duplica nada. Los movimientos nunca se editan: se
corrigen con el contrario.

Fuera del devengo: depósito en garantía (no es `Pago`), propinas (`concepto='EXTRA'`) y
condonaciones. Una baja congela todo: nada se devenga con fecha posterior a `fecha_baja`.
"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core_erp.impuestos import centavos
from nomina.models import Empleado
from nomina.services_expediente import condiciones_vigentes

from .models import CERO, ComisionContratacion, EventoVenta, MovimientoComision

logger = logging.getLogger(__name__)

ESTADOS_CONTRATADA = ('CONFIRMADA', 'EJECUTADA', 'CERRADA')
ROLES_BASE = ('BASE_EVENTO', 'BASE_PASADIA', 'BASE_PASADIA_BASICO', 'BASE_PASADIA_PREMIUM',
              'HABITACION_HOSPEDAJE')
UNO = Decimal('1')


# --- Lectura (también para el tablero del PR 5) ---------------------------------------------

def lunes_de(fecha):
    return fecha - timedelta(days=fecha.weekday())


def _rango_semana(lunes):
    from zoneinfo import ZoneInfo
    zona = ZoneInfo('America/Merida')
    inicio = datetime.combine(lunes, datetime.min.time(), tzinfo=zona)
    return inicio, inicio + timedelta(days=7)


def pagado_para_comision(cotizacion, hasta=None) -> Decimal:
    """Lo cobrado que sí devenga: ingresos de la venta menos reembolsos; sin propinas ni condonaciones.
    Con `hasta` (fecha de baja) solo cuenta lo registrado hasta ese día."""
    pagos = cotizacion.pagos.all()
    if hasta is not None:
        pagos = pagos.filter(fecha_pago__lte=hasta)
    ingresos = (pagos.filter(tipo='INGRESO', concepto='VENTA').exclude(metodo='CONDONACION')
                .aggregate(t=Sum('monto'))['t'] or CERO)
    reembolsos = pagos.filter(tipo='REEMBOLSO').aggregate(t=Sum('monto'))['t'] or CERO
    return ingresos - reembolsos


def base_extras(cotizacion) -> Decimal:
    """Valor sin IVA ni ISH, neto de descuento proporcional, de todo lo que no es la base del servicio.

    No son extras: roles base (evento, pasadía, habitación), un paquete y los componentes
    `fijo_por_evento` de un paquete. Horas y personas extra sí lo son."""
    from comercial.models import ProductoComponente

    items = list(cotizacion.items.select_related('producto'))
    subtotal = sum((i.subtotal() for i in items), CERO)
    if subtotal <= 0:
        return CERO
    paquetes = {i.producto_id for i in items if i.producto_id and i.producto.es_paquete}
    fijos = set(ProductoComponente.objects.filter(producto_padre_id__in=paquetes, fijo_por_evento=True)
                .values_list('producto_hijo_id', flat=True)) if paquetes else set()
    extras = CERO
    for i in items:
        p = i.producto
        if p is not None and (p.rol_cotizador in ROLES_BASE or p.es_paquete or p.pk in fijos):
            continue
        extras += i.subtotal()
    neto = max(subtotal - (cotizacion.descuento or CERO), CERO)
    return centavos(extras * neto / subtotal)


def devolucion_total(cotizacion) -> bool:
    """Cancelada y reembolsada por completo: deja de contar para el escalón."""
    if cotizacion.estado != 'CANCELADA':
        return False
    return (cotizacion.pagos.filter(tipo='REEMBOLSO').exists()
            and pagado_para_comision(cotizacion) <= 0)


def saldo_en_contra(vendedor) -> Decimal:
    """Lo que el vendedor 'debe' a futuras comisiones: lo arrastrado por el último corte de comisiones
    más lo revertido aún sin cortar, menos lo absorbido. Nunca negativo. Nunca se le pide dinero."""
    from .models import CorteComision

    ultimo = (CorteComision.objects.filter(vendedor=vendedor, tipo='COMISIONES').exclude(estado='CANCELADO')
              .order_by('-hasta', '-id').first())
    saldo = ultimo.saldo_arrastrado if ultimo else CERO
    sin_cortar = MovimientoComision.objects.filter(vendedor=vendedor, corte__isnull=True,
                                                   tipo__in=('CONTRATACION', 'AJUSTE'))
    negativos = sin_cortar.filter(monto__lt=0, absorcion=False).aggregate(t=Sum('monto'))['t'] or CERO
    absorbido = sin_cortar.filter(absorcion=True).aggregate(t=Sum('monto'))['t'] or CERO
    return max(saldo - negativos - absorbido, CERO)


# --- Escritura ----------------------------------------------------------------------------------

def _evento(cotizacion_id, tipo, detalle, vendedor=None, usuario=None):
    try:
        EventoVenta.objects.create(
            cotizacion_id=cotizacion_id, tipo=tipo, detalle=detalle[:300], vendedor=vendedor,
            usuario=usuario if getattr(usuario, 'pk', None) else None)
    except Exception:
        logger.exception('Ventas: no se pudo registrar el evento de COT-%s', cotizacion_id)


def _fecha_efectiva(vendedor, hoy):
    return min(hoy, vendedor.fecha_baja) if vendedor.fecha_baja else hoy


def _congelada(vendedor, hoy) -> bool:
    return bool(vendedor.fecha_baja and hoy > vendedor.fecha_baja)


def _avisar_sin_condiciones(cotizacion, vendedor):
    from comunicacion.services_pendientes import registrar_pendiente

    registrar_pendiente(
        titulo=f'COT-{cotizacion.pk} tiene vendedor sin condiciones aceptadas',
        rol='DIRECCION', tipo='OTRO', severidad='NORMAL',
        detalle=f'Cotización: COT-{cotizacion.pk}\nVendedor: {vendedor}\n'
                'No se devenga comisión hasta que haya Condiciones aceptadas vigentes.',
        objeto=cotizacion, clave_idempotencia=f'comision_sin_condiciones:{cotizacion.pk}')


def asegurar_comision(cotizacion, usuario=None):
    """Crea (una sola vez) la comisión de una contratación. Devuelve la comisión o None si no aplica.

    Aplica a una cotización no histórica, con precio > 0 y vendedor en servicio, que llegó a
    CONFIRMADA. Sin Condiciones aceptadas vigentes no se crea nada (opción conservadora) y se avisa."""
    from comercial.models import Cotizacion

    cot = Cotizacion.objects.select_related('cliente').get(pk=getattr(cotizacion, 'pk', cotizacion))
    existente = ComisionContratacion.objects.filter(cotizacion_id=cot.pk).first()
    if existente is not None:
        return existente
    if (cot.es_historica or not cot.vendedor_id or cot.precio_final <= 0
            or cot.estado not in ESTADOS_CONTRATADA):
        return None
    ahora = timezone.now()
    hoy = timezone.localdate()
    try:
        with transaction.atomic():
            vendedor = Empleado.objects.select_for_update().get(pk=cot.vendedor_id)
            existente = ComisionContratacion.objects.filter(cotizacion_id=cot.pk).first()
            if existente is not None:
                return existente
            if not vendedor.activo or _congelada(vendedor, hoy):
                return None
            condiciones = condiciones_vigentes(vendedor, hoy)
            if condiciones is None:
                _avisar_sin_condiciones(cot, vendedor)
                return None
            anio = timezone.localtime(ahora).year
            cuentan = sum(
                1 for c in ComisionContratacion.objects.filter(vendedor=vendedor, anio=anio)
                .select_related('cotizacion') if not devolucion_total(c.cotizacion))
            numero = cuentan + 1
            fijo = condiciones.monto_de_contratacion(numero) or CERO
            pct = condiciones.porcentaje_extras or Decimal('0')
            base = base_extras(cot)
            extras = centavos(base * pct)
            comision = ComisionContratacion.objects.create(
                cotizacion=cot, vendedor=vendedor, condiciones=condiciones, numero_en_periodo=numero,
                precio_final=cot.precio_final, anio=anio, monto_fijo=fijo, porcentaje_extras=pct, base_extras=base, monto_extras=extras,
                total=fijo + extras, devengado=CERO, confirmada_en=ahora,
                created_by=usuario if getattr(usuario, 'pk', None) else None)
            _evento(cot.pk, 'CONFIRMADA',
                    f'Contratación n.º {numero} del año; comisión total {comision.total}', vendedor, usuario)
            return comision
    except IntegrityError:
        return ComisionContratacion.objects.filter(cotizacion_id=cot.pk).first()


def sincronizar(cotizacion, pago=None, usuario=None):
    """Recalcula la comisión de la cotización y emite el movimiento por la diferencia. Idempotente.

    Devuelve el movimiento emitido o None."""
    from comercial.models import Cotizacion

    cot = Cotizacion.objects.select_related('cliente').get(pk=getattr(cotizacion, 'pk', cotizacion))
    if cot.es_historica or not cot.vendedor_id:
        return None
    if asegurar_comision(cot, usuario) is None:
        return None
    hoy = timezone.localdate()
    with transaction.atomic():
        vendedor = Empleado.objects.select_for_update().get(pk=cot.vendedor_id)
        comision = ComisionContratacion.objects.select_for_update().get(cotizacion_id=cot.pk)
        # La comisión es de quien la creó, aunque la cotización se reasigne después.
        if comision.vendedor_id != vendedor.pk:
            vendedor = Empleado.objects.select_for_update().get(pk=comision.vendedor_id)
        cot = Cotizacion.objects.get(pk=cot.pk)
        if not _congelada(vendedor, hoy):
            comision.base_extras = base_extras(cot)
            comision.monto_extras = centavos(comision.base_extras * comision.porcentaje_extras)
            comision.total = comision.monto_fijo + comision.monto_extras
            comision.precio_final = cot.precio_final
        pagado = pagado_para_comision(cot, vendedor.fecha_baja)
        if comision.precio_final > 0:
            proporcion = min(UNO, max(pagado, CERO) / comision.precio_final)
        else:
            proporcion = Decimal('0')
        objetivo = centavos(comision.total * proporcion)
        diferencia = objetivo - comision.devengado
        movimiento = None
        if diferencia != 0:
            n = comision.movimientos.count()
            movimiento = MovimientoComision.objects.create(
                vendedor=vendedor, tipo='CONTRATACION', monto=diferencia, fecha=_fecha_efectiva(vendedor, hoy),
                comision=comision, pago=pago if getattr(pago, 'pk', None) else None,
                detalle=f'Devengado {comision.devengado} → {objetivo} de {comision.total} '
                        f'(cobrado neto {pagado} de {comision.precio_final})',
                clave_idempotencia=f'com:{comision.pk}:{n}',
                created_by=usuario if getattr(usuario, 'pk', None) else None)
            comision.devengado = objetivo
            _evento(cot.pk, 'COMISION', f'Movimiento {diferencia:+}; devengado {objetivo}', vendedor, usuario)
        comision.save()
        return movimiento


def devengar_visitas(vendedor, lunes, usuario=None) -> int:
    """Recalcula la semana (lunes a domingo) de visitas ATENDIDA y emite las diferencias.

    Las primeras `tope_visitas_semana` visitas por `inicio` pagan `pago_visita`; las demás un
    movimiento de $0.00 «Sobre el tope semanal» (siguen atribuyendo la venta). Una visita que deja de
    estar atendida se revierte. Devuelve cuántos movimientos emitió."""
    from comunicacion.models import Visita

    lunes = lunes_de(lunes)
    inicio, fin = _rango_semana(lunes)
    emitidos = 0
    with transaction.atomic():
        vendedor = Empleado.objects.select_for_update().get(pk=getattr(vendedor, 'pk', vendedor))
        visitas = (Visita.objects.filter(vendedor=vendedor, inicio__gte=inicio, inicio__lt=fin)
                   .filter(Q(resultado='ATENDIDA') | Q(movimientos_comision__isnull=False))
                   .distinct().order_by('inicio', 'id'))
        atendidas_vistas = 0
        for visita in visitas:
            fecha_visita = timezone.localtime(visita.inicio).date()
            condiciones = condiciones_vigentes(vendedor, fecha_visita)
            objetivo = CERO
            detalle = 'Visita atendida'
            if visita.resultado == 'ATENDIDA':
                dentro_de_baja = not (vendedor.fecha_baja and fecha_visita > vendedor.fecha_baja)
                if not dentro_de_baja:
                    detalle = 'Posterior a la baja'
                elif condiciones is None or not condiciones.pago_visita:
                    detalle = 'Sin pago por visita en las Condiciones'
                elif atendidas_vistas < condiciones.tope_visitas_semana:
                    objetivo = centavos(condiciones.pago_visita)
                else:
                    detalle = 'Sobre el tope semanal'
                atendidas_vistas += 1
            else:
                detalle = 'La visita ya no está atendida'
            previos = list(visita.movimientos_comision.all())
            actual = sum((m.monto for m in previos), CERO)
            diferencia = objetivo - actual
            if previos and diferencia == 0:
                continue
            if not previos and visita.resultado != 'ATENDIDA':
                continue
            n = len(previos)
            MovimientoComision.objects.create(
                vendedor=vendedor, tipo='VISITA', monto=diferencia, visita=visita,
                fecha=min(fecha_visita, vendedor.fecha_baja) if vendedor.fecha_baja else fecha_visita,
                detalle=detalle, clave_idempotencia=f'vis:{visita.pk}' if n == 0 else f'vis:{visita.pk}:{n}',
                created_by=usuario if getattr(usuario, 'pk', None) else None)
            emitidos += 1
    return emitidos


def devengar_visita(visita, usuario=None) -> int:
    """Punto de entrada tras cerrar (o reabrir) una visita: recalcula su semana."""
    if not visita.vendedor_id:
        return 0
    return devengar_visitas(visita.vendedor_id, timezone.localtime(visita.inicio).date(), usuario)


def absorber(comision, motivo, usuario, monto=None):
    """La Quinta absorbe como costo lo revertido de esta comisión (cancelación imputable a ella).

    Emite un AJUSTE positivo marcado como absorción: cancela saldo en contra sin pagarse a nadie.
    Solo superusuario y con motivo. Devuelve el movimiento o None si no había nada que absorber."""
    if not getattr(usuario, 'is_superuser', False):
        raise PermissionDenied('Solo Dirección absorbe saldos en contra.')
    if not (motivo or '').strip():
        raise ValidationError('Indica el motivo de la absorción.')
    hoy = timezone.localdate()
    with transaction.atomic():
        comision = ComisionContratacion.objects.select_for_update().get(pk=comision.pk)
        vendedor = Empleado.objects.select_for_update().get(pk=comision.vendedor_id)
        movs = comision.movimientos.all()
        revertido = -(movs.filter(monto__lt=0, absorcion=False).aggregate(t=Sum('monto'))['t'] or CERO)
        ya = movs.filter(absorcion=True).aggregate(t=Sum('monto'))['t'] or CERO
        disponible = min(revertido - ya, saldo_en_contra(vendedor))
        monto = disponible if monto is None else min(centavos(monto), disponible)
        if monto <= 0:
            return None
        n = movs.filter(absorcion=True).count()
        movimiento = MovimientoComision.objects.create(
            vendedor=vendedor, tipo='AJUSTE', monto=monto, fecha=_fecha_efectiva(vendedor, hoy),
            comision=comision, absorcion=True, detalle=f'Absorbido por la Quinta: {motivo.strip()}'[:300],
            clave_idempotencia=f'abs:{comision.pk}:{n}', created_by=usuario)
        _evento(comision.cotizacion_id, 'ABSORCION', f'{monto}: {motivo.strip()}', vendedor, usuario)
        return movimiento


# --- Barrido de respaldo (cubre `update()` sin señal, contracargos y confirmaciones del cron) ------------

def barrido(hoy=None, semanas=6) -> dict:
    """Recalcula todo lo vivo. Cada pieza va aislada: un fallo no detiene a las demás."""
    from comercial.models import Cotizacion
    from comunicacion.models import Visita

    hoy = hoy or timezone.localdate()
    resumen = {'cotizaciones': 0, 'movimientos': 0, 'visitas': 0, 'errores': 0}
    cotizaciones = (Cotizacion.objects.filter(vendedor__isnull=False, es_historica=False)
                    .filter(Q(estado__in=ESTADOS_CONTRATADA) | Q(comision_contratacion__isnull=False))
                    .distinct())
    for cot in cotizaciones:
        try:
            if sincronizar(cot) is not None:
                resumen['movimientos'] += 1
            resumen['cotizaciones'] += 1
        except Exception:
            resumen['errores'] += 1
            logger.exception('Ventas: falló la sincronización de COT-%s', cot.pk)
    desde = lunes_de(hoy) - timedelta(weeks=semanas)
    inicio, _ = _rango_semana(desde)
    semanas_vivas = set()
    for v in Visita.objects.filter(vendedor__isnull=False, inicio__gte=inicio).filter(
            Q(resultado__in=('ATENDIDA', 'NO_LLEGO')) | Q(movimientos_comision__isnull=False)).distinct():
        semanas_vivas.add((v.vendedor_id, lunes_de(timezone.localtime(v.inicio).date())))
    for vendedor_id, lunes in sorted(semanas_vivas):
        try:
            resumen['visitas'] += devengar_visitas(vendedor_id, lunes)
        except Exception:
            resumen['errores'] += 1
            logger.exception('Ventas: falló el devengo de visitas del vendedor %s', vendedor_id)
    return resumen
