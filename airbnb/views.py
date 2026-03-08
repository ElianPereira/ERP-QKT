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


@staff_member_required
def calendario_unificado(request):
    """
    Calendario unificado que muestra eventos de la quinta + reservas de Airbnb.
    Este reemplaza el calendario anterior.
    """
    # Importar aquí para evitar imports circulares
    from comercial.models import Cotizacion
    
    eventos_lista = []
    
    # ========================================
    # EVENTOS DE LA QUINTA (Cotizaciones)
    # ========================================
    cotizaciones = Cotizacion.objects.exclude(estado='CANCELADA').select_related('cliente')
    
    for c in cotizaciones:
        if c.estado == 'CONFIRMADA':
            color = '#27ae60'  # Verde
            icon = '🎉'
        else:
            color = '#95a5a6'  # Gris
            icon = '📝'
        
        eventos_lista.append({
            'title': f"{icon} {c.cliente.nombre} - {c.nombre_evento}",
            'start': c.fecha_evento.strftime("%Y-%m-%d"),
            'color': color,
            'url': f'/admin/comercial/cotizacion/{c.id}/change/',
            'extendedProps': {'tipo': 'evento'}
        })
    
    # ========================================
    # RESERVAS DE AIRBNB
    # ========================================
    reservas = ReservaAirbnb.objects.filter(
        estado__in=['CONFIRMADA', 'BLOQUEADA'],
        anuncio__activo=True
    ).select_related('anuncio')
    
    for r in reservas:
        # Determinar color según tipo y si tiene conflicto
        tiene_conflicto = r.conflictos.filter(estado='PENDIENTE').exists()
        
        if tiene_conflicto:
            color = '#e74c3c'  # Rojo - conflicto
            icon = '⚠️'
        elif r.anuncio.tipo == 'CASA':
            color = '#3498db'  # Azul - Casa (no afecta quinta)
            icon = '🏠'
        else:
            color = '#e67e22'  # Naranja - Habitación
            icon = '🛏️'
        
        if r.estado == 'BLOQUEADA':
            color = '#6c757d'  # Gris para bloqueos
            icon = '🔒'
        
        eventos_lista.append({
            'title': f"{icon} {r.anuncio.nombre}: {r.titulo or 'Reserva'}",
            'start': r.fecha_inicio.strftime("%Y-%m-%d"),
            'end': r.fecha_fin.strftime("%Y-%m-%d"),
            'color': color,
            'url': f'/admin/airbnb/reservaairbnb/{r.id}/change/',
            'extendedProps': {'tipo': 'airbnb'}
        })
    
    # Conflictos pendientes
    conflictos_pendientes = ConflictoCalendario.objects.filter(estado='PENDIENTE').count()
    
    context = {
        'eventos_json': json.dumps(eventos_lista, cls=DjangoJSONEncoder),
        'conflictos_pendientes': conflictos_pendientes,
        'title': 'Calendario Unificado',
    }
    
    return render(request, 'admin/airbnb/calendario_unificado.html', context)


@staff_member_required
def reporte_pagos_airbnb(request):
    """
    Reporte de pagos de Airbnb para el contador.
    Incluye desglose de retenciones (ISR 4%, IVA 8%).
    """
    hoy = timezone.now()
    
    # Filtros
    año = request.GET.get('año', hoy.year)
    mes = request.GET.get('mes', '')
    
    try:
        año = int(año)
    except:
        año = hoy.year
    
    pagos = PagoAirbnb.objects.filter(estado='PAGADO')
    
    if año:
        pagos = pagos.filter(fecha_checkin__year=año)
    if mes:
        try:
            pagos = pagos.filter(fecha_checkin__month=int(mes))
        except:
            pass
    
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
        'año_actual': año,
        'mes_actual': int(mes) if mes else None,
        'años_disponibles': range(2024, hoy.year + 2),
        'title': 'Reporte de Pagos Airbnb',
    }
    
    # Exportar a Excel si se solicita
    if request.GET.get('export') == 'excel':
        return exportar_reporte_excel(pagos, totales, año)
    
    return render(request, 'admin/airbnb/reporte_pagos.html', context)


def exportar_reporte_excel(pagos, totales, año):
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
    ws.merge_cells('A1:J1')
    ws['A1'] = 'REPORTE DE INGRESOS AIRBNB - PLATAFORMAS TECNOLÓGICAS'
    ws['A1'].font = Font(bold=True, size=14)
    ws['A1'].alignment = Alignment(horizontal='center')
    
    ws.merge_cells('A2:J2')
    ws['A2'] = f'Año: {año} | Generado: {timezone.now().strftime("%d/%m/%Y %H:%M")}'
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
    row_num = 5
    for pago in pagos:
        ws.cell(row=row_num, column=1, value=pago.codigo_confirmacion or '-')
        ws.cell(row=row_num, column=2, value=pago.huesped)
        ws.cell(row=row_num, column=3, value=pago.anuncio.nombre if pago.anuncio else '-')
        ws.cell(row=row_num, column=4, value=pago.fecha_checkin.strftime('%d/%m/%Y'))
        ws.cell(row=row_num, column=5, value=pago.fecha_checkout.strftime('%d/%m/%Y'))
        ws.cell(row=row_num, column=6, value=float(pago.monto_bruto))
        ws.cell(row=row_num, column=7, value=float(pago.comision_airbnb))
        ws.cell(row=row_num, column=8, value=float(pago.retencion_isr))
        ws.cell(row=row_num, column=9, value=float(pago.retencion_iva))
        ws.cell(row=row_num, column=10, value=float(pago.monto_neto))
        
        for col in range(1, 11):
            ws.cell(row=row_num, column=col).border = border
        
        row_num += 1
    
    # Totales
    total_row = row_num
    ws.cell(row=total_row, column=5, value="TOTALES:").font = Font(bold=True)
    ws.cell(row=total_row, column=6, value=float(totales['total_bruto'] or 0)).font = Font(bold=True)
    ws.cell(row=total_row, column=7, value=float(totales['total_comision'] or 0)).font = Font(bold=True)
    ws.cell(row=total_row, column=8, value=float(totales['total_isr'] or 0)).font = Font(bold=True)
    ws.cell(row=total_row, column=9, value=float(totales['total_iva'] or 0)).font = Font(bold=True)
    ws.cell(row=total_row, column=10, value=float(totales['total_neto'] or 0)).font = Font(bold=True)
    
    # Nota fiscal
    nota_row = total_row + 2
    ws.merge_cells(f'A{nota_row}:J{nota_row}')
    ws[f'A{nota_row}'] = 'Régimen: Actividad Empresarial - Plataformas Tecnológicas (Art. 113-A LISR)'
    ws[f'A{nota_row}'].font = Font(italic=True, color="666666")
    
    # Ajustar anchos
    column_widths = [15, 25, 20, 12, 12, 12, 12, 10, 10, 12]
    for i, width in enumerate(column_widths, 1):
        ws.column_dimensions[chr(64 + i)].width = width
    
    # Respuesta HTTP
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="Airbnb_Pagos_{año}.xlsx"'
    wb.save(response)
    
    return response
