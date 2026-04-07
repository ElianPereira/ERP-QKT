"""
Vistas de reportes contables.
"""
from datetime import date
from decimal import Decimal

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import admin
from django.db.models import Sum
from django.shortcuts import render

from .models import MovimientoContable, UnidadNegocio


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
