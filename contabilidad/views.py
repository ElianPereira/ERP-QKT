"""
Vistas de reportes contables.
"""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import render

from .models import (
    EstadoCuentaBancario,
    MovimientoContable,
    MovimientoEstadoCuenta,
    UnidadNegocio,
)


def _parse_periodo(request):
    hoy = date.today()
    desde = request.GET.get('desde') or date(hoy.year, 1, 1).isoformat()
    hasta = request.GET.get('hasta') or hoy.isoformat()
    try:
        d = date.fromisoformat(desde)
        h = date.fromisoformat(hasta)
    except ValueError:
        d, h = date(hoy.year, 1, 1), hoy
    return d, h


@staff_member_required
def balanza_comprobacion(request):
    """Balanza de comprobación por período y unidad de negocio."""
    desde, hasta = _parse_periodo(request)
    unidad_id = request.GET.get('unidad') or ''

    qs = MovimientoContable.objects.filter(
        poliza__estado='APLICADA',
        poliza__fecha__gte=desde,
        poliza__fecha__lte=hasta,
    )
    if unidad_id:
        qs = qs.filter(poliza__unidad_negocio_id=unidad_id)

    agregados = (
        qs.values('cuenta__codigo_sat', 'cuenta__nombre',
                  'cuenta__naturaleza', 'cuenta__tipo')
        .annotate(total_debe=Sum('debe'), total_haber=Sum('haber'))
        .order_by('cuenta__codigo_sat')
    )

    filas = []
    total_debe = Decimal('0.00')
    total_haber = Decimal('0.00')
    for row in agregados:
        debe = row['total_debe'] or Decimal('0.00')
        haber = row['total_haber'] or Decimal('0.00')
        if row['cuenta__naturaleza'] == 'D':
            saldo = debe - haber
        else:
            saldo = haber - debe
        filas.append({
            'codigo': row['cuenta__codigo_sat'],
            'nombre': row['cuenta__nombre'],
            'naturaleza': row['cuenta__naturaleza'],
            'tipo': row['cuenta__tipo'],
            'debe': debe,
            'haber': haber,
            'saldo': saldo,
        })
        total_debe += debe
        total_haber += haber

    context = {
        **admin.site.each_context(request),
        'title': 'Balanza de comprobación',
        'filas': filas,
        'total_debe': total_debe,
        'total_haber': total_haber,
        'desde': desde,
        'hasta': hasta,
        'unidades': UnidadNegocio.objects.filter(activa=True),
        'unidad_id': unidad_id,
    }
    return render(request, 'contabilidad/balanza.html', context)


@staff_member_required
def estado_resultados(request):
    """Estado de resultados: Ingresos − Costos − Gastos = Utilidad."""
    desde, hasta = _parse_periodo(request)
    unidad_id = request.GET.get('unidad') or ''

    qs = MovimientoContable.objects.filter(
        poliza__estado='APLICADA',
        poliza__fecha__gte=desde,
        poliza__fecha__lte=hasta,
    )
    if unidad_id:
        qs = qs.filter(poliza__unidad_negocio_id=unidad_id)

    def _saldos_por_tipo(tipo):
        rows = (
            qs.filter(cuenta__tipo=tipo)
            .values('cuenta__codigo_sat', 'cuenta__nombre', 'cuenta__naturaleza')
            .annotate(d=Sum('debe'), h=Sum('haber'))
            .order_by('cuenta__codigo_sat')
        )
        out = []
        total = Decimal('0.00')
        for r in rows:
            d = r['d'] or Decimal('0.00')
            h = r['h'] or Decimal('0.00')
            saldo = (h - d) if r['cuenta__naturaleza'] == 'A' else (d - h)
            out.append({
                'codigo': r['cuenta__codigo_sat'],
                'nombre': r['cuenta__nombre'],
                'monto': saldo,
            })
            total += saldo
        return out, total

    ingresos, total_ing = _saldos_por_tipo('INGRESO')
    costos, total_cos = _saldos_por_tipo('COSTO')
    gastos, total_gas = _saldos_por_tipo('GASTO')
    utilidad = total_ing - total_cos - total_gas

    context = {
        **admin.site.each_context(request),
        'title': 'Estado de resultados',
        'ingresos': ingresos, 'total_ingresos': total_ing,
        'costos': costos, 'total_costos': total_cos,
        'gastos': gastos, 'total_gastos': total_gas,
        'utilidad': utilidad,
        'desde': desde,
        'hasta': hasta,
        'unidades': UnidadNegocio.objects.filter(activa=True),
        'unidad_id': unidad_id,
    }
    return render(request, 'contabilidad/estado_resultados.html', context)


# ==========================================
# AUTOCOMPLETE ACOTADO DE ASIENTOS BANCARIOS
# ==========================================

def _candidatos_asiento_bancario(estado_cuenta, tolerancia_dias=5):
    """
    Asientos que legítimamente pueden emparejarse con un movimiento del estado
    de cuenta: los que tocan la cuenta de bancos de esa cuenta bancaria, en una
    póliza APLICADA, dentro del periodo del estado de cuenta con holgura, y que
    no estén ya emparejados con otro movimiento bancario de la misma cuenta.

    El autocomplete estándar del admin ofrecía TODOS los MovimientoContable —de
    cualquier cuenta (incluidas las de gastos), cualquier fecha y cualquier
    estado de póliza—, así que permitía enlazar un movimiento del banco a un
    asiento que ni siquiera toca la cuenta de bancos. Eso rompe en silencio la
    premisa del cálculo de partidas de la conciliación.
    """
    cuenta_contable = estado_cuenta.cuenta_bancaria.cuenta_contable
    if not cuenta_contable:
        return MovimientoContable.objects.none()

    movimientos = estado_cuenta.movimientos.order_by('fecha')
    primero = movimientos.first()
    ultimo = movimientos.last()
    desde = (primero.fecha if primero else estado_cuenta.fecha_corte_real) - timedelta(days=tolerancia_dias)
    hasta = (estado_cuenta.fecha_corte_real or (ultimo.fecha if ultimo else desde)) + timedelta(days=tolerancia_dias)

    ya_emparejados = MovimientoEstadoCuenta.objects.filter(
        estado_cuenta__cuenta_bancaria=estado_cuenta.cuenta_bancaria,
        movimiento_contable__isnull=False,
    ).values_list('movimiento_contable_id', flat=True)

    return (
        MovimientoContable.objects
        .filter(
            cuenta=cuenta_contable,
            poliza__estado='APLICADA',
            poliza__fecha__gte=desde,
            poliza__fecha__lte=hasta,
        )
        .exclude(id__in=ya_emparejados)
        .select_related('poliza', 'cuenta')
        .order_by('poliza__fecha', 'id')
    )


@staff_member_required
def autocomplete_asiento_bancario(request):
    """
    Fuente del buscador de asientos del inline de movimientos del estado de
    cuenta. Devuelve el mismo formato que espera select2 en el admin.

    Mantiene el control de permisos de `AutocompleteJsonView`: quien no puede
    ver movimientos contables no puede listarlos por aquí.
    """
    if not request.user.has_perm('contabilidad.view_movimientocontable'):
        raise PermissionDenied("No tienes permiso para consultar movimientos contables.")

    try:
        estado_cuenta = EstadoCuentaBancario.objects.select_related(
            'cuenta_bancaria__cuenta_contable'
        ).get(pk=request.GET.get('estado_cuenta'))
    except (EstadoCuentaBancario.DoesNotExist, ValueError, TypeError):
        return JsonResponse({'results': [], 'pagination': {'more': False}})

    qs = _candidatos_asiento_bancario(estado_cuenta)

    termino = (request.GET.get('term') or '').strip()
    if termino:
        filtro = (
            Q(concepto__icontains=termino)
            | Q(poliza__concepto__icontains=termino)
            | Q(referencia__icontains=termino)
        )
        if termino.isdigit():
            filtro |= Q(poliza__folio=int(termino))
        importe = _a_decimal(termino)
        if importe is not None:
            filtro |= Q(debe=importe) | Q(haber=importe)
        qs = qs.filter(filtro)

    pagina = max(1, int(request.GET.get('page') or 1))
    tam = 20
    inicio = (pagina - 1) * tam
    lote = list(qs[inicio:inicio + tam + 1])
    hay_mas = len(lote) > tam

    return JsonResponse({
        'results': [{'id': str(m.pk), 'text': str(m)} for m in lote[:tam]],
        'pagination': {'more': hay_mas},
    })


def _a_decimal(texto):
    """Convierte un término de búsqueda a Decimal si parece un importe."""
    limpio = texto.replace(',', '').replace('$', '').strip()
    try:
        return Decimal(limpio)
    except (InvalidOperation, ValueError):
        return None
