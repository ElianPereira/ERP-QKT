"""
Vistas del módulo Airbnb
========================
Vistas para calendario unificado, reportes, iCal inverso y bloqueo manual.
"""
import json
from datetime import timedelta, datetime
from decimal import Decimal

from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse
from django.utils import timezone
from django.db.models import Sum, Count
from django.db.models.functions import TruncMonth
from django.contrib.admin.views.decorators import staff_member_required
from django.core.serializers.json import DjangoJSONEncoder
from django.contrib import messages

from .models import AnuncioAirbnb, ReservaAirbnb, PagoAirbnb, ConflictoCalendario


@staff_member_required
def calendario_unificado(request):
    """
    Calendario unificado que muestra eventos de la quinta + reservas de Airbnb.
    """
    from comercial.models import Cotizacion
    
    eventos_lista = []
    
    # EVENTOS DE LA QUINTA (Cotizaciones)
    cotizaciones = Cotizacion.objects.exclude(estado='CANCELADA').select_related('cliente')
    
    for c in cotizaciones:
        if c.estado == 'CONFIRMADA':
            color = '#27ae60'
            icon = '🎉'
        else:
            color = '#95a5a6'
            icon = '📝'
        
        eventos_lista.append({
            'title': f"{icon} {c.cliente.nombre} - {c.nombre_evento}",
            'start': c.fecha_evento.strftime("%Y-%m-%d"),
            'color': color,
            'url': f'/admin/comercial/cotizacion/{c.id}/change/',
            'extendedProps': {'tipo': 'evento'}
        })
    
    # RESERVAS DE AIRBNB
    reservas = ReservaAirbnb.objects.filter(
        estado__in=['CONFIRMADA', 'BLOQUEADA', 'PENDIENTE'],
        anuncio__activo=True
    ).select_related('anuncio')
    
    for r in reservas:
        tiene_conflicto = r.conflictos.filter(estado='PENDIENTE').exists()
        
        if tiene_conflicto:
            color = '#e74c3c'
            icon = '⚠️'
        elif r.estado == 'PENDIENTE':
            color = '#f39c12'
            icon = '⏳'
        elif r.estado == 'BLOQUEADA':
            color = '#6c757d'
            icon = '🔒'
        elif r.anuncio.tipo == 'CASA':
            color = '#3498db'
            icon = '🏠'
        else:
            color = '#e67e22'
            icon = '🛏️'
        
        eventos_lista.append({
            'title': f"{icon} {r.anuncio.nombre}: {r.titulo or 'Reserva'}",
            'start': r.fecha_inicio.strftime("%Y-%m-%d"),
            'end': r.fecha_fin.strftime("%Y-%m-%d"),
            'color': color,
            'url': f'/admin/airbnb/reservaairbnb/{r.id}/change/',
            'extendedProps': {'tipo': 'airbnb'}
        })
    
    conflictos_pendientes = ConflictoCalendario.objects.filter(estado='PENDIENTE').count()
    
    # URL de iCal para mostrar en la página
    ical_url = request.build_absolute_uri('/airbnb/ical/eventos/')
    
    context = {
        'eventos_json': json.dumps(eventos_lista, cls=DjangoJSONEncoder),
        'conflictos_pendientes': conflictos_pendientes,
        'ical_url': ical_url,
        'title': 'Calendario Unificado',
    }
    
    return render(request, 'admin/airbnb/calendario_unificado.html', context)


@staff_member_required
def reporte_pagos_airbnb(request):
    """
    Reporte de pagos de Airbnb para el contador.
    """
    hoy = timezone.now()
    
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
    
    totales = pagos.aggregate(
        total_bruto=Sum('monto_bruto'),
        total_comision=Sum('comision_airbnb'),
        total_isr=Sum('retencion_isr'),
        total_iva=Sum('retencion_iva'),
        total_neto=Sum('monto_neto'),
        num_reservas=Count('id'),
    )
    
    for key in totales:
        if totales[key] is None:
            totales[key] = Decimal('0.00') if 'total' in key else 0
    
    resumen_mensual = pagos.annotate(
        mes=TruncMonth('fecha_checkin')
    ).values('mes').annotate(
        bruto=Sum('monto_bruto'),
        neto=Sum('monto_neto'),
        isr=Sum('retencion_isr'),
        iva=Sum('retencion_iva'),
        reservas=Count('id'),
    ).order_by('mes')
    
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
    
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2E7D32", fill_type="solid")
    border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    
    ws.merge_cells('A1:J1')
    ws['A1'] = 'REPORTE DE INGRESOS AIRBNB - PLATAFORMAS TECNOLÓGICAS'
    ws['A1'].font = Font(bold=True, size=14)
    ws['A1'].alignment = Alignment(horizontal='center')
    
    ws.merge_cells('A2:J2')
    ws['A2'] = f'Año: {año} | Generado: {timezone.now().strftime("%d/%m/%Y %H:%M")}'
    ws['A2'].alignment = Alignment(horizontal='center')
    
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
    
    total_row = row_num
    ws.cell(row=total_row, column=5, value="TOTALES:").font = Font(bold=True)
    ws.cell(row=total_row, column=6, value=float(totales['total_bruto'] or 0)).font = Font(bold=True)
    ws.cell(row=total_row, column=7, value=float(totales['total_comision'] or 0)).font = Font(bold=True)
    ws.cell(row=total_row, column=8, value=float(totales['total_isr'] or 0)).font = Font(bold=True)
    ws.cell(row=total_row, column=9, value=float(totales['total_iva'] or 0)).font = Font(bold=True)
    ws.cell(row=total_row, column=10, value=float(totales['total_neto'] or 0)).font = Font(bold=True)
    
    nota_row = total_row + 2
    ws.merge_cells(f'A{nota_row}:J{nota_row}')
    ws[f'A{nota_row}'] = 'Régimen: Actividad Empresarial - Plataformas Tecnológicas (Art. 113-A LISR)'
    ws[f'A{nota_row}'].font = Font(italic=True, color="666666")
    
    column_widths = [15, 25, 20, 12, 12, 12, 12, 10, 10, 12]
    for i, width in enumerate(column_widths, 1):
        ws.column_dimensions[chr(64 + i)].width = width
    
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="Airbnb_Pagos_{año}.xlsx"'
    wb.save(response)
    
    return response


# ==========================================
# ICAL INVERSO - Exportar eventos del ERP
# ==========================================
def generar_ical_eventos(request):
    """
    Genera un archivo iCal con los eventos confirmados del ERP.
    Esta URL se importa en Airbnb para bloquear fechas automáticamente.
    
    URL: /airbnb/ical/eventos/
    
    Airbnb sincroniza calendarios externos cada 2-24 horas.
    No requiere autenticación para que Airbnb pueda acceder.
    """
    from comercial.models import Cotizacion
    
    # Solo eventos confirmados (últimos 30 días + futuros)
    cotizaciones = Cotizacion.objects.filter(
        estado='CONFIRMADA',
        fecha_evento__gte=timezone.now().date() - timedelta(days=30)
    ).select_related('cliente')
    
    # Generar contenido iCal
    lineas = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//Quinta Koox Tanil//ERP//ES',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        'X-WR-CALNAME:Eventos Quinta Koox Tanil',
        'X-WR-TIMEZONE:America/Merida',
    ]
    
    for cot in cotizaciones:
        uid = f"evento-{cot.id}@qkt-erp"
        
        # Fecha de inicio y fin (evento de día completo)
        fecha_inicio = cot.fecha_evento.strftime('%Y%m%d')
        fecha_fin = (cot.fecha_evento + timedelta(days=1)).strftime('%Y%m%d')
        
        # Timestamp de creación
        dtstamp = timezone.now().strftime('%Y%m%dT%H%M%SZ')
        created = cot.created_at.strftime('%Y%m%dT%H%M%SZ') if hasattr(cot, 'created_at') and cot.created_at else dtstamp
        
        # Título del evento (Airbnb lo mostrará como "Not available" o el título)
        titulo = f"EVENTO QKT: {cot.nombre_evento}"
        
        # Descripción
        descripcion = f"Cliente: {cot.cliente.nombre}\\nPersonas: {cot.num_personas}\\nEstado: Confirmado"
        
        lineas.extend([
            'BEGIN:VEVENT',
            f'UID:{uid}',
            f'DTSTAMP:{dtstamp}',
            f'CREATED:{created}',
            f'DTSTART;VALUE=DATE:{fecha_inicio}',
            f'DTEND;VALUE=DATE:{fecha_fin}',
            f'SUMMARY:{titulo}',
            f'DESCRIPTION:{descripcion}',
            'STATUS:CONFIRMED',
            'TRANSP:OPAQUE',
            'END:VEVENT',
        ])
    
    lineas.append('END:VCALENDAR')
    
    contenido = '\r\n'.join(lineas)
    
    response = HttpResponse(contenido, content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = 'inline; filename="eventos_qkt.ics"'
    
    return response


# ==========================================
# BLOQUEO MANUAL EN AIRBNB
# ==========================================
@staff_member_required  
def bloquear_en_airbnb(request, cotizacion_id):
    """
    Redirige a Airbnb para bloquear manualmente las fechas de un evento.
    Abre el calendario del anuncio en la fecha específica.
    """
    from comercial.models import Cotizacion
    
    cotizacion = get_object_or_404(Cotizacion, pk=cotizacion_id)
    
    # Obtener anuncios que afectan la quinta
    anuncios = AnuncioAirbnb.objects.filter(
        afecta_eventos_quinta=True,
        activo=True
    )
    
    if not anuncios.exists():
        messages.warning(request, "⚠️ No hay anuncios configurados que afecten la quinta")
        return redirect('admin:comercial_cotizacion_change', cotizacion_id)
    
    # Generar URLs de bloqueo para cada anuncio
    urls_bloqueo = []
    fecha_str = cotizacion.fecha_evento.strftime('%Y-%m-%d')
    
    for anuncio in anuncios:
        if anuncio.airbnb_listing_id:
            # URL directa al calendario del anuncio en la fecha específica
            url = f"https://www.airbnb.com/hosting/calendar/{anuncio.airbnb_listing_id}?date={fecha_str}"
            urls_bloqueo.append({
                'nombre': anuncio.nombre,
                'url': url,
                'listing_id': anuncio.airbnb_listing_id,
            })
    
    if not urls_bloqueo:
        messages.warning(request, "⚠️ Los anuncios no tienen Listing ID configurado")
        return redirect('admin:comercial_cotizacion_change', cotizacion_id)
    
    if len(urls_bloqueo) == 1:
        # Si solo hay un anuncio, redirigir directamente
        messages.info(
            request, 
            f"🔒 Bloquea la fecha {cotizacion.fecha_evento.strftime('%d/%m/%Y')} en el calendario de Airbnb"
        )
        return redirect(urls_bloqueo[0]['url'])
    
    # Si hay múltiples anuncios, mostrar página con links
    context = {
        'cotizacion': cotizacion,
        'urls_bloqueo': urls_bloqueo,
        'title': f'Bloquear en Airbnb: {cotizacion.nombre_evento}',
    }
    
    return render(request, 'admin/airbnb/bloquear_manual.html', context)


@staff_member_required
def dashboard_airbnb(request):
    """
    Dashboard del módulo Airbnb con estadísticas y accesos rápidos.
    """
    hoy = timezone.now()
    
    # Estadísticas
    total_anuncios = AnuncioAirbnb.objects.filter(activo=True).count()
    
    pagos_mes = PagoAirbnb.objects.filter(
        fecha_checkin__year=hoy.year,
        fecha_checkin__month=hoy.month,
        estado='PAGADO'
    ).aggregate(
        total_neto=Sum('monto_neto'),
        num_reservas=Count('id'),
    )
    
    conflictos_count = ConflictoCalendario.objects.filter(estado='PENDIENTE').count()
    
    # Próximas reservas
    reservas_proximas = ReservaAirbnb.objects.filter(
        fecha_inicio__gte=hoy.date(),
        estado='CONFIRMADA',
        anuncio__activo=True
    ).select_related('anuncio').order_by('fecha_inicio')[:5]
    
    # Conflictos pendientes
    conflictos = ConflictoCalendario.objects.filter(
        estado='PENDIENTE'
    ).select_related('reserva_airbnb__anuncio', 'cotizacion')[:5]
    
    # Datos para gráfica de ingresos (últimos 6 meses)
    ingresos_labels = []
    ingresos_data = []
    
    for i in range(5, -1, -1):
        if i > 0:
            fecha = hoy - timedelta(days=30*i)
        else:
            fecha = hoy
        
        total = PagoAirbnb.objects.filter(
            fecha_checkin__year=fecha.year,
            fecha_checkin__month=fecha.month,
            estado='PAGADO'
        ).aggregate(total=Sum('monto_neto'))['total'] or 0
        
        ingresos_labels.append(fecha.strftime('%b %Y'))
        ingresos_data.append(float(total))
    
    # URL del iCal
    ical_url = request.build_absolute_uri('/airbnb/ical/eventos/')
    
    context = {
        'total_anuncios': total_anuncios,
        'pagos_mes': pagos_mes,
        'conflictos_count': conflictos_count,
        'reservas_proximas': reservas_proximas,
        'conflictos': conflictos,
        'today': hoy.date(),
        'ingresos_labels': json.dumps(ingresos_labels),
        'ingresos_data': json.dumps(ingresos_data),
        'ical_url': ical_url,
        'title': 'Dashboard Airbnb',
    }
    
    return render(request, 'admin/airbnb/dashboard.html', context)
