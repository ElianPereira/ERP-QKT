"""
Vistas del módulo Airbnb
========================
Vistas para calendario unificado y reportes.
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.shortcuts import render
from django.http import HttpResponse
from django.utils import timezone
from django.db.models import Sum, Count
from django.db.models.functions import TruncMonth
from django.contrib.admin.views.decorators import staff_member_required
from django.core.serializers.json import DjangoJSONEncoder

from .models import AnuncioAirbnb, ReservaAirbnb, PagoAirbnb, ConflictoCalendario
from comercial.models import Cotizacion


@staff_member_required
def calendario_unificado(request):
    """
    Vista de calendario que muestra tanto eventos de la quinta
    como reservas de Airbnb en una sola vista.
    """
    eventos_lista = []
    
    # Eventos de la quinta (Cotizaciones confirmadas)
    cotizaciones = Cotizacion.objects.exclude(estado='CANCELADA')
    for c in cotizaciones:
        color = '#28a745' if c.estado == 'CONFIRMADA' else '#6c757d'
        eventos_lista.append({
            'title': f"🎉 {c.cliente.nombre} - {c.nombre_evento}",
            'start': c.fecha_evento.strftime("%Y-%m-%d"),
            'color': color,
            'url': f'/admin/comercial/cotizacion/{c.id}/change/',
            'tipo': 'evento',
        })
    
    # Reservas de Airbnb
    reservas = ReservaAirbnb.objects.filter(
        estado__in=['CONFIRMADA', 'BLOQUEADA']
    ).select_related('anuncio')
    
    for r in reservas:
        # Color según tipo de anuncio
        if r.anuncio.tipo == 'CASA':
            color = '#17a2b8'  # Azul - Casa (no afecta quinta)
        else:
            color = '#fd7e14'  # Naranja - Habitación (puede afectar)
        
        if r.estado == 'BLOQUEADA':
            color = '#6c757d'  # Gris para bloqueos manuales
        
        # Verificar si tiene conflicto
        tiene_conflicto = r.conflictos.filter(estado='PENDIENTE').exists()
        prefix = "⚠️ " if tiene_conflicto else "🏠 "
        
        eventos_lista.append({
            'title': f"{prefix}{r.anuncio.nombre}: {r.titulo}",
            'start': r.fecha_inicio.strftime("%Y-%m-%d"),
            'end': r.fecha_fin.strftime("%Y-%m-%d"),
            'color': '#e74c3c' if tiene_conflicto else color,
            'url': f'/admin/airbnb/reservaairbnb/{r.id}/change/',
            'tipo': 'airbnb',
        })
    
    # Conflictos pendientes para mostrar alerta
    conflictos_pendientes = ConflictoCalendario.objects.filter(
        estado='PENDIENTE'
    ).count()
    
    context = {
        'eventos_json': json.dumps(eventos_lista, cls=DjangoJSONEncoder),
        'conflictos_pendientes': conflictos_pendientes,
        'title': 'Calendario Unificado - Eventos + Airbnb',
    }
    
    return render(request, 'admin/airbnb/calendario_unificado.html', context)


@staff_member_required
def reporte_pagos_airbnb(request):
    """
    Reporte de pagos de Airbnb para el contador.
    Incluye desglose de retenciones (ISR 4%, IVA 8%).
    """
    # Filtros
    año = request.GET.get('año', timezone.now().year)
    mes = request.GET.get('mes', '')
    
    pagos = PagoAirbnb.objects.filter(estado='PAGADO')
    
    if año:
        pagos = pagos.filter(fecha_checkin__year=año)
    if mes:
        pagos = pagos.filter(fecha_checkin__month=mes)
    
    # Totales
    totales = pagos.aggregate(
        total_bruto=Sum('monto_bruto'),
        total_comision=Sum('comision_airbnb'),
        total_isr=Sum('retencion_isr'),
        total_iva=Sum('retencion_iva'),
        total_neto=Sum('monto_neto'),
        num_reservas=Count('id'),
    )
    
    # Asegurar valores no nulos
    for key in totales:
        if totales[key] is None:
            totales[key] = Decimal('0.00') if 'total' in key else 0
    
    # Resumen por mes
    resumen_mensual = pagos.annotate(
        mes=TruncMonth('fecha_checkin')
    ).values('mes').annotate(
        bruto=Sum('monto_bruto'),
        neto=Sum('monto_neto'),
        isr=Sum('retencion_isr'),
        iva=Sum('retencion_iva'),
        reservas=Count('id'),
    ).order_by('mes')
    
    # Resumen por anuncio
    resumen_anuncio = pagos.values(
        'anuncio__nombre'
    ).annotate(
        bruto=Sum('monto_bruto'),
        neto=Sum('monto_neto'),
        reservas=Count('id'),
    ).order_by('-bruto')
    
    context = {
        'pagos': pagos.select_related('anuncio').order_by('-fecha_checkin'),
        'totales': totales,
        'resumen_mensual': resumen_mensual,
        'resumen_anuncio': resumen_anuncio,
        'año_actual': int(año) if año else timezone.now().year,
        'mes_actual': int(mes) if mes else None,
        'años_disponibles': range(2024, timezone.now().year + 2),
        'title': 'Reporte de Pagos Airbnb - Plataformas Tecnológicas',
    }
    
    # Exportar a Excel si se solicita
    if request.GET.get('export') == 'excel':
        return exportar_reporte_excel(pagos, totales)
    
    return render(request, 'admin/airbnb/reporte_pagos.html', context)


def exportar_reporte_excel(pagos, totales):
    """Genera archivo Excel con el reporte de pagos."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Pagos Airbnb"
    
    # Estilos
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2E7D32", fill_type="solid")
    border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    
    # Título
    ws.merge_cells('A1:H1')
    ws['A1'] = 'REPORTE DE INGRESOS AIRBNB - PLATAFORMAS TECNOLÓGICAS'
    ws['A1'].font = Font(bold=True, size=14)
    ws['A1'].alignment = Alignment(horizontal='center')
    
    ws.merge_cells('A2:H2')
    ws['A2'] = f'Generado: {timezone.now().strftime("%d/%m/%Y %H:%M")}'
    ws['A2'].alignment = Alignment(horizontal='center')
    
    # Headers
    headers = [
        'Código', 'Huésped', 'Anuncio', 'Check-in', 'Check-out', 
        'Bruto', 'Comisión', 'ISR 4%', 'IVA 8%', 'Neto'
    ]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=4, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center')
        cell.border = border
    
    # Datos
    for row, pago in enumerate(pagos, 5):
        ws.cell(row=row, column=1, value=pago.codigo_confirmacion or '-')
        ws.cell(row=row, column=2, value=pago.huesped)
        ws.cell(row=row, column=3, value=pago.anuncio.nombre if pago.anuncio else '-')
        ws.cell(row=row, column=4, value=pago.fecha_checkin.strftime('%d/%m/%Y'))
        ws.cell(row=row, column=5, value=pago.fecha_checkout.strftime('%d/%m/%Y'))
        ws.cell(row=row, column=6, value=float(pago.monto_bruto))
        ws.cell(row=row, column=7, value=float(pago.comision_airbnb))
        ws.cell(row=row, column=8, value=float(pago.retencion_isr))
        ws.cell(row=row, column=9, value=float(pago.retencion_iva))
        ws.cell(row=row, column=10, value=float(pago.monto_neto))
        
        for col in range(1, 11):
            ws.cell(row=row, column=col).border = border
    
    # Totales
    total_row = pagos.count() + 5
    ws.cell(row=total_row, column=5, value="TOTALES:").font = Font(bold=True)
    ws.cell(row=total_row, column=6, value=float(totales['total_bruto'])).font = Font(bold=True)
    ws.cell(row=total_row, column=7, value=float(totales['total_comision'])).font = Font(bold=True)
    ws.cell(row=total_row, column=8, value=float(totales['total_isr'])).font = Font(bold=True)
    ws.cell(row=total_row, column=9, value=float(totales['total_iva'])).font = Font(bold=True)
    ws.cell(row=total_row, column=10, value=float(totales['total_neto'])).font = Font(bold=True)
    
    # Nota fiscal
    nota_row = total_row + 2
    ws.merge_cells(f'A{nota_row}:J{nota_row}')
    ws[f'A{nota_row}'] = 'Régimen: Actividad Empresarial - Plataformas Tecnológicas (Art. 113-A LISR)'
    ws[f'A{nota_row}'].font = Font(italic=True, color="666666")
    
    # Ajustar anchos
    ws.column_dimensions['A'].width = 15
    ws.column_dimensions['B'].width = 25
    ws.column_dimensions['C'].width = 20
    ws.column_dimensions['D'].width = 12
    ws.column_dimensions['E'].width = 12
    ws.column_dimensions['F'].width = 12
    ws.column_dimensions['G'].width = 12
    ws.column_dimensions['H'].width = 10
    ws.column_dimensions['I'].width = 10
    ws.column_dimensions['J'].width = 12
    
    # Respuesta HTTP
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="Airbnb_Pagos_{timezone.now().strftime("%Y%m")}.xlsx"'
    wb.save(response)
    
    return response


@staff_member_required
def dashboard_airbnb(request):
    """
    Dashboard resumen del módulo Airbnb.
    """
    hoy = timezone.now()
    
    # Estadísticas de anuncios
    anuncios = AnuncioAirbnb.objects.filter(activo=True)
    total_anuncios = anuncios.count()
    
    # Reservas próximas (30 días)
    reservas_proximas = ReservaAirbnb.objects.filter(
        estado='CONFIRMADA',
        fecha_inicio__gte=hoy.date(),
        fecha_inicio__lte=hoy.date() + timedelta(days=30)
    ).select_related('anuncio').order_by('fecha_inicio')[:10]
    
    # Conflictos pendientes
    conflictos = ConflictoCalendario.objects.filter(
        estado='PENDIENTE'
    ).select_related('reserva_airbnb', 'cotizacion')[:5]
    
    # Pagos del mes
    pagos_mes = PagoAirbnb.objects.filter(
        fecha_checkin__year=hoy.year,
        fecha_checkin__month=hoy.month,
        estado='PAGADO'
    ).aggregate(
        total_bruto=Sum('monto_bruto'),
        total_neto=Sum('monto_neto'),
        num_reservas=Count('id'),
    )
    
    # Gráfico de ingresos últimos 6 meses
    from django.db.models.functions import TruncMonth
    ingresos_mensual = PagoAirbnb.objects.filter(
        estado='PAGADO',
        fecha_checkin__gte=hoy - timedelta(days=180)
    ).annotate(
        mes=TruncMonth('fecha_checkin')
    ).values('mes').annotate(
        total=Sum('monto_neto')
    ).order_by('mes')
    
    context = {
        'total_anuncios': total_anuncios,
        'reservas_proximas': reservas_proximas,
        'conflictos': conflictos,
        'conflictos_count': ConflictoCalendario.objects.filter(estado='PENDIENTE').count(),
        'pagos_mes': pagos_mes,
        'ingresos_labels': json.dumps([
            m['mes'].strftime('%b %Y') for m in ingresos_mensual
        ]),
        'ingresos_data': json.dumps([
            float(m['total'] or 0) for m in ingresos_mensual
        ]),
        'title': 'Dashboard Airbnb',
    }
    
    return render(request, 'admin/airbnb/dashboard.html', context)
