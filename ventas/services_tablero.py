"""Lectura para el tablero «Ventas», «Mis comisiones», reportes y demás módulos (Issue #505, PR 5).

SOLO LECTURA sobre las fases 1 y 2: aquí no se devenga, no se corta ni se paga nada.
Todo monto es `Decimal` (`centavos` = ROUND_HALF_UP); ningún porcentaje ni importe vive en este archivo.

Definiciones (periodo = [desde, hasta], días de Mérida):
- **Devengado**: suma de los movimientos del periodo que no son absorción (neto de reversos).
- **Pagado**: total de los cortes PAGADO cuya fecha de pago cae en el periodo.
- **Por pagar** (al día de hoy): movimientos que no están en un corte pagado ni cancelado, más lo ya
  cortado pero sin pagar; nunca negativo (lo negativo es «saldo en contra»).
- **Costo de ventas**: suma de TODOS los movimientos del periodo (incluida la absorción: la Quinta la
  asume), es decir lo que la fuerza de ventas le cuesta a la Quinta por hecho devengado.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from core_erp.impuestos import centavos

from . import services_efectivo
from .models import CERO, ComisionContratacion, CorteComision, EventoVenta, MovimientoComision
from .services_comisiones import devolucion_total, lunes_de, saldo_en_contra

UNO = Decimal('1')
SOBRE_TOPE = 'Sobre el tope semanal'


def _suma(qs, campo='monto') -> Decimal:
    return qs.aggregate(t=Sum(campo))['t'] or CERO


def _hoy():
    return timezone.localdate()


def _porcentaje(parte, total):
    """Porcentaje con un decimal, o None si no hay base."""
    if not total:
        return None
    return centavos(Decimal(parte) * 100 / Decimal(total)).quantize(Decimal('0.1'))


# --- Costo de ventas (Indicadores y Marketing) ---------------------------------------------------

def costo_de_ventas(desde, hasta) -> Decimal:
    """Lo que la fuerza de ventas costó en [desde, hasta]: todos los movimientos, absorciones incluidas."""
    return _suma(MovimientoComision.objects.filter(fecha__gte=desde, fecha__lte=hasta))


def comision_devengada_por_cotizacion(cotizaciones_ids) -> dict:
    """{cotización: comisión total sellada} de las cotizaciones dadas (para el costo variable por venta)."""
    return dict(ComisionContratacion.objects.filter(cotizacion_id__in=list(cotizaciones_ids))
                .values_list('cotizacion_id', 'total'))


# --- Una vendedora, un periodo ------------------------------------------------------------------------

def _visitas(vendedor, desde, hasta) -> dict:
    from comunicacion.models import Visita

    base = Visita.objects.filter(vendedor=vendedor, inicio__date__gte=desde, inicio__date__lte=hasta)
    atendidas = base.filter(resultado='ATENDIDA')
    ids = list(atendidas.values_list('pk', flat=True))
    pagadas = MovimientoComision.objects.filter(tipo='VISITA', visita_id__in=ids).values('visita_id').annotate(
        t=Sum('monto')).filter(t__gt=0).count()
    return {
        'agendadas': base.filter(estado__in=Visita.ACTIVAS).count(),
        'atendidas': len(ids),
        'no_llego': base.filter(resultado='NO_LLEGO').count(),
        'pagadas': pagadas,
        'sobre_tope': max(len(ids) - pagadas, 0),
        'manuales': base.filter(origen='MANUAL').count(),
    }


def _escalon(vendedor, anio, hoy) -> dict:
    """Contrataciones que cuentan en el año y qué escalón sigue (según las Condiciones vigentes hoy)."""
    from nomina.services_expediente import condiciones_vigentes

    del_anio = ComisionContratacion.objects.filter(vendedor=vendedor, anio=anio).select_related('cotizacion')
    cuentan = sum(1 for c in del_anio if not devolucion_total(c.cotizacion))
    condiciones = condiciones_vigentes(vendedor, hoy)
    siguiente = proximo_cambio = faltan = None
    if condiciones is not None:
        siguiente = condiciones.monto_de_contratacion(cuentan + 1)
        for e in condiciones.escalones_ordenados():
            if e.desde_contratacion > cuentan + 1:
                proximo_cambio, faltan = e.monto, e.desde_contratacion - cuentan
                break
    return {'anio': anio, 'contrataciones_anio': cuentan, 'monto_siguiente': siguiente,
            'monto_proximo_escalon': proximo_cambio, 'faltan_para_escalon': faltan,
            'sin_condiciones': condiciones is None}


def por_pagar(vendedor) -> Decimal:
    """Devengado que aún no se paga: sin cortar (neto, sin absorciones) más cortes borrador/aprobados."""
    sin_cortar = _suma(MovimientoComision.objects.filter(vendedor=vendedor, corte__isnull=True, absorcion=False))
    en_corte = _suma(CorteComision.objects.filter(vendedor=vendedor, estado__in=('BORRADOR', 'APROBADO')), 'total')
    return max(sin_cortar + en_corte, CERO)


def contrataciones_de(vendedor, desde, hasta) -> list:
    """Una fila por contratación confirmada en el periodo (la tabla del tablero)."""
    filas = []
    comisiones = (ComisionContratacion.objects.filter(vendedor=vendedor, confirmada_en__date__gte=desde,
                                                      confirmada_en__date__lte=hasta)
                  .select_related('cotizacion__cliente').order_by('confirmada_en'))
    for c in comisiones:
        cot = c.cotizacion
        filas.append({
            'folio': f'COT-{cot.pk:03d}', 'cotizacion_id': cot.pk, 'cliente': cot.cliente.nombre,
            'servicio': cot.get_tipo_servicio_display(), 'confirmada': timezone.localtime(c.confirmada_en).date(),
            'numero': c.numero_en_periodo, 'venta_sin_iva': max(cot.subtotal - cot.descuento, CERO),
            'total': c.total, 'devengado': c.devengado, 'por_devengar': c.por_devengar,
            'estado': cot.get_estado_display(), 'origen': cot.get_vendedor_origen_display() or '',
        })
    return filas


def resumen_vendedor(vendedor, desde, hasta, hoy=None) -> dict:
    """Todas las cifras de una persona en [desde, hasta]. Incluye costos de la Quinta: solo Dirección."""
    from comercial.models import Cotizacion

    hoy = hoy or _hoy()
    visitas = _visitas(vendedor, desde, hasta)
    cotizaciones = Cotizacion.objects.filter(vendedor=vendedor, es_historica=False, created_at__date__gte=desde,
                                             created_at__date__lte=hasta).exclude(precio_final=0).count()
    contrataciones = contrataciones_de(vendedor, desde, hasta)
    movs = MovimientoComision.objects.filter(vendedor=vendedor, fecha__gte=desde, fecha__lte=hasta)
    sin_absorcion = movs.filter(absorcion=False)
    visitas_dev = _suma(sin_absorcion.filter(tipo='VISITA'))
    comisiones_dev = _suma(sin_absorcion.exclude(tipo='VISITA'))
    pagado = _suma(CorteComision.objects.filter(vendedor=vendedor, estado='PAGADO', pagado_en__date__gte=desde,
                                                pagado_en__date__lte=hasta), 'total')
    no_devengado_por_baja = CERO
    if vendedor.fecha_baja:
        no_devengado_por_baja = sum((c.por_devengar for c in ComisionContratacion.objects.filter(vendedor=vendedor)
                                     if c.por_devengar > 0), CERO)
    venta = sum((f['venta_sin_iva'] for f in contrataciones), CERO)
    return {
        'vendedor': vendedor, 'desde': desde, 'hasta': hasta, 'visitas': visitas,
        'cotizaciones': cotizaciones, 'contrataciones': len(contrataciones), 'filas': contrataciones,
        'tasa_cierre': _porcentaje(len(contrataciones), cotizaciones),
        'visita_a_contratacion': _porcentaje(
            sum(1 for f in contrataciones if f['origen'] == 'Visita'), visitas['atendidas']),
        'venta_sin_iva': venta, 'escalon': _escalon(vendedor, hasta.year, hoy),
        'visitas_devengado': visitas_dev, 'comisiones_devengado': comisiones_dev,
        'devengado': visitas_dev + comisiones_dev, 'pagado': pagado, 'por_pagar': por_pagar(vendedor),
        'saldo_en_contra': saldo_en_contra(vendedor), 'absorbido': _suma(movs.filter(absorcion=True)),
        'no_devengado_por_baja': no_devengado_por_baja,
        'efectivo_por_entregar': services_efectivo.saldo_por_entregar(vendedor),
        'costo_de_ventas': _suma(movs),
    }


def vendedores_con_actividad(desde, hasta):
    """Empleados de Ventas con alta (o con algo en el periodo), para listar en el tablero."""
    from nomina.models import Empleado

    con_movimientos = MovimientoComision.objects.filter(fecha__gte=desde, fecha__lte=hasta).values('vendedor_id')
    return (Empleado.objects.filter(puesto='VENTAS').filter(Q(activo=True) | Q(pk__in=con_movimientos))
            .order_by('nombre'))


def tablero(desde, hasta, hoy=None) -> dict:
    """Pantalla «Ventas» (solo Dirección): una tarjeta por persona y los totales del periodo."""
    hoy = hoy or _hoy()
    personas = [resumen_vendedor(v, desde, hasta, hoy) for v in vendedores_con_actividad(desde, hasta)]
    totales = {k: sum((p[k] for p in personas), CERO) for k in (
        'venta_sin_iva', 'devengado', 'pagado', 'por_pagar', 'saldo_en_contra', 'absorbido',
        'efectivo_por_entregar', 'costo_de_ventas')}
    for k in ('cotizaciones', 'contrataciones'):
        totales[k] = sum(p[k] for p in personas)
    for k in ('agendadas', 'atendidas', 'no_llego', 'sobre_tope'):
        totales['visitas_' + k] = sum(p['visitas'][k] for p in personas)
    totales['tasa_cierre'] = _porcentaje(totales['contrataciones'], totales['cotizaciones'])
    return {'desde': desde, 'hasta': hasta, 'personas': personas, 'totales': totales,
            'efectivo': services_efectivo.saldos_pendientes()}


# --- «Mis comisiones»: SOLO lo de la persona; nunca costos, márgenes ni datos de otra --------------------

def mis_comisiones(vendedor, hoy=None) -> dict:
    """Lo que ve la vendedora de sí misma. No incluye costo de la venta, margen ni cifras de otras personas.

    Recibe el Empleado ya resuelto desde `request.user` (nunca de un parámetro de la petición)."""
    hoy = hoy or _hoy()
    inicio_mes = hoy.replace(day=1)
    inicio_anio = hoy.replace(month=1, day=1)
    mes = resumen_vendedor(vendedor, inicio_mes, hoy, hoy)
    anio = resumen_vendedor(vendedor, inicio_anio, hoy, hoy)
    proximas = (vendedor.visitas.filter(inicio__gte=timezone.now(), estado='CONFIRMADA')
                .order_by('inicio')[:10])
    cortes = (CorteComision.objects.filter(vendedor=vendedor).exclude(estado='CANCELADO').order_by('-hasta', '-id')[:24])
    mias = [{'folio': f['folio'], 'cliente': f['cliente'], 'servicio': f['servicio'], 'confirmada': f['confirmada'],
             'numero': f['numero'], 'comision': f['total'], 'ganado': f['devengado'], 'por_ganar': f['por_devengar'],
             'estado': f['estado']} for f in anio['filas']]
    return {
        'vendedor': vendedor, 'hoy': hoy,
        'mes': _vista_personal(mes), 'anio': _vista_personal(anio), 'escalon': anio['escalon'],
        'proximas_visitas': list(proximas), 'contrataciones': mias,
        'cortes': [{'pk': c.pk, 'tipo': c.get_tipo_display(), 'desde': c.desde, 'hasta': c.hasta,
                    'total': c.total, 'estado': c.get_estado_display(), 'pagado_en': c.pagado_en,
                    'tiene_pdf': bool(c.archivo_pdf)} for c in cortes],
        'saldo_en_contra': saldo_en_contra(vendedor), 'efectivo_por_entregar': services_efectivo.saldo_por_entregar(vendedor),
    }


def _vista_personal(r) -> dict:
    return {k: r[k] for k in ('desde', 'hasta', 'visitas', 'cotizaciones', 'contrataciones', 'visitas_devengado',
                              'comisiones_devengado', 'devengado', 'pagado', 'por_pagar')}


# --- Seguimiento de una venta (panel en la cotización) ----------------------------------------------------

def linea_de_tiempo(cotizacion, incluir_comision=False) -> list:
    """Qué le ha pasado a la venta, en orden: [{'cuando', 'tipo', 'detalle', 'quien'}].

    Se arma leyendo los registros que ya existen (nada se duplica en la bitácora). Lo de comisión solo
    sale con `incluir_comision=True` (Dirección): la vendedora no ve cifras de comisión en la cotización."""
    from comercial.models import ContratoServicio, FirmaContrato, Pago
    from comunicacion.models import ComunicacionCliente

    cot = cotizacion
    eventos = []

    def agregar(cuando, tipo, detalle, quien=''):
        if cuando is not None:
            eventos.append({'cuando': cuando, 'tipo': tipo, 'detalle': detalle, 'quien': quien})

    def _quien(usuario):
        return (usuario.get_full_name() or usuario.get_username()) if usuario else ''

    agregar(cot.created_at, 'Creada', f'COT-{cot.pk:03d}', _quien(cot.usuario))
    if cot.vendedor_id and cot.vendedor_asignado_en:
        agregar(cot.vendedor_asignado_en, 'Atribuida',
                f'{cot.vendedor.nombre} ({cot.get_vendedor_origen_display() or "sin origen"})',
                _quien(cot.vendedor_asignado_por))
    for c in ComunicacionCliente.objects.filter(cotizacion=cot, tipo='COTIZACION', estado='ENVIADO'):
        agregar(c.created_at, 'Enviada', f'Por {c.get_trigger_display().lower()}')
    if cot.vendedor_id and cot.cliente.telefono:
        digitos = ''.join(ch for ch in cot.cliente.telefono if ch.isdigit())[-10:]
        if digitos:
            from comunicacion.models import Visita

            for v in Visita.objects.filter(vendedor=cot.vendedor, resultado='ATENDIDA'):
                if ''.join(ch for ch in v.telefono if ch.isdigit())[-10:] == digitos:
                    agregar(v.inicio, 'Visita', 'Atendida', v.vendedor.nombre)
    for p in Pago.objects.filter(cotizacion=cot).select_related('usuario').order_by('fecha_pago', 'id'):
        tipo = 'Reembolso' if p.tipo == 'REEMBOLSO' else 'Pago'
        cuando = timezone.make_aware(timezone.datetime.combine(p.fecha_pago, timezone.datetime.min.time()))
        agregar(cuando, tipo, f'${p.monto:,.2f} · {p.get_metodo_display()}', _quien(p.usuario))
    for c in ContratoServicio.objects.filter(cotizacion=cot):
        agregar(c.generado_en, 'Contrato', c.numero, _quien(c.generado_por))
        firma = FirmaContrato.objects.filter(contrato=c, firmado_en__isnull=False).first()
        if firma:
            agregar(firma.firmado_en, 'Firma', f'Firmado por {firma.nombre_firmante}')
    if cot.fecha_cancelacion:
        agregar(cot.fecha_cancelacion, 'Cancelada', (cot.motivo_cancelacion or '')[:120], _quien(cot.cancelada_por))
    tipos = ('CONFIRMADA', 'COMISION', 'ABSORCION') if incluir_comision else ('CONFIRMADA',)
    for e in EventoVenta.objects.filter(cotizacion=cot, tipo__in=tipos).select_related('usuario'):
        detalle = e.detalle if e.tipo != 'CONFIRMADA' or incluir_comision else 'Confirmada'
        agregar(e.created_at, e.get_tipo_display(), detalle, _quien(e.usuario))
    eventos.sort(key=lambda x: x['cuando'])
    return eventos


# --- Semana de la vendedora (resumen por WhatsApp) -----------------------------------------------------

def resumen_semanal(vendedor, hoy=None) -> dict:
    """La semana lunes–domingo que acaba de cerrar (si hoy es lunes) o la en curso: para el aviso a Ventas."""
    hoy = hoy or _hoy()
    lunes = lunes_de(hoy - timedelta(days=1)) if hoy.weekday() == 0 else lunes_de(hoy)
    domingo = lunes + timedelta(days=6)
    r = resumen_vendedor(vendedor, lunes, domingo, hoy)
    cobrar = por_pagar(vendedor)
    return {'desde': lunes, 'hasta': domingo, 'visitas_atendidas': r['visitas']['atendidas'],
            'contrataciones': r['contrataciones'], 'escalon': r['escalon'], 'por_pagar': cobrar,
            'devengado': r['devengado']}
