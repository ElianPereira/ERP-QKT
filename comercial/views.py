import json
import math
import openpyxl 
from openpyxl.styles import Font, PatternFill
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.contrib import messages
from django.core.mail import EmailMessage
from django.conf import settings
from django.db.models import Sum
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from weasyprint import HTML

from .models import Cotizacion, Gasto, Pago
from .forms import CalculadoraForm

# --- 1. VISTA PARA VER/IMPRIMIR PDF ---
def generar_pdf_cotizacion(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    
    total_pagado = cotizacion.pagos.aggregate(Sum('monto'))['monto__sum'] or 0
    saldo_pendiente = cotizacion.precio_final - total_pagado

    context = {
        'cotizacion': cotizacion,
        'total_pagado': total_pagado,
        'saldo_pendiente': saldo_pendiente
    }
    
    html_string = render_to_string('cotizaciones/pdf_recibo.html', context)
    response = HttpResponse(content_type='application/pdf')
    filename = f"Recibo_{cotizacion.id}_{cotizacion.cliente.nombre}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf(response)
    return response

# --- 2. VISTA PARA ENVIAR CORREO ---
def enviar_cotizacion_email(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    cliente = cotizacion.cliente
    
    if not cliente.email:
        messages.error(request, f"El cliente {cliente.nombre} no tiene email registrado.")
        return redirect(request.META.get('HTTP_REFERER', '/admin/'))

    total_pagado = cotizacion.pagos.aggregate(Sum('monto'))['monto__sum'] or 0
    saldo_pendiente = cotizacion.precio_final - total_pagado

    context = {
        'cotizacion': cotizacion,
        'total_pagado': total_pagado,
        'saldo_pendiente': saldo_pendiente
    }
    
    html_string = render_to_string('cotizaciones/pdf_recibo.html', context)
    html = HTML(string=html_string, base_url=request.build_absolute_uri())
    pdf_file = html.write_pdf()

    asunto = f"Recibo de pago - Evento {cotizacion.fecha_evento}"
    mensaje = f"""
    Hola {cliente.nombre},
    
    Adjunto encontrarás el recibo actualizado de tu evento.
    
    Total del evento: ${cotizacion.precio_final}
    Abonado hasta hoy: ${total_pagado}
    Saldo Pendiente: ${saldo_pendiente}
    
    Saludos,
    Quinta Kooxtanil
    """

    email = EmailMessage(
        asunto,
        mensaje,
        settings.DEFAULT_FROM_EMAIL,
        [cliente.email],
    )
    email.attach(f"Recibo_{cotizacion.id}.pdf", pdf_file, 'application/pdf')
    email.send()

    messages.success(request, f"✅ Correo enviado a {cliente.email}")
    return redirect(request.META.get('HTTP_REFERER', '/admin/'))

# --- 3. VISTA DEL CALENDARIO ---
def ver_calendario(request):
    cotizaciones = Cotizacion.objects.exclude(estado='CANCELADA')
    
    eventos_lista = []
    for c in cotizaciones:
        color = '#28a745' if c.estado == 'CONFIRMADA' else '#6c757d'
        eventos_lista.append({
            'title': f"{c.cliente.nombre} - {c.producto.nombre}",
            'start': c.fecha_evento.strftime("%Y-%m-%d"),
            'color': color,
            'url': f'/admin/comercial/cotizacion/{c.id}/change/'
        })

    eventos_json = json.dumps(eventos_lista, cls=DjangoJSONEncoder)
    return render(request, 'admin/calendario.html', {'eventos_json': eventos_json})

# --- 4. VISTA DEL DASHBOARD (CON CANDADO Y PERMISOS DE JEFE) ---
@staff_member_required 
def ver_dashboard_kpis(request):
    context = admin.site.each_context(request)
    context['app_list'] = admin.site.get_app_list(request)
    
    # LÓGICA DE JEFE AGREGADA:
    es_jefe = request.user.is_superuser or request.user.groups.filter(name='Gerencia').exists()
    context['es_jefe'] = es_jefe
    
    return render(request, 'admin/dashboard_kpi.html', context)

# --- 5. VISTA DE LA CALCULADORA DE INSUMOS ---
@staff_member_required
def calculadora_insumos(request):
    resultado = None
    
    if request.method == 'POST':
        form = CalculadoraForm(request.POST)
        if form.is_valid():
            p = form.cleaned_data['invitados']
            h = form.cleaned_data['horas']
            tipo = form.cleaned_data['tipo_evento']
            clima = form.cleaned_data['clima']
            
            factor_consumo = 2.0 if tipo == 'boda' else 1.5
            if tipo == 'empresarial': factor_consumo = 1.0
            if clima == 'calor': factor_consumo *= 1.2 
            
            total_tragos = p * h * factor_consumo
            
            resultado = {
                'total_tragos_estimados': int(total_tragos),
                'insumos': []
            }
            
            kilos_hielo = p * 0.75 if clima == 'calor' else p * 0.5
            if h > 5: kilos_hielo += (p * 0.1 * (h-5))
            bolsas_hielo = math.ceil(kilos_hielo / 5)
            resultado['insumos'].append({'nombre': 'Hielo (Bolsas 5kg)', 'cantidad': bolsas_hielo, 'nota': 'Incluye enfriamiento y servicio'})

            if form.cleaned_data['calcular_destilados']:
                vasos_con_refresco = total_tragos * 0.4 
                botellas_refresco = math.ceil(vasos_con_refresco / 8)
                resultado['insumos'].append({'nombre': 'Refrescos (2L - Surtido)', 'cantidad': botellas_refresco, 'nota': 'Coca-Cola, Squirt, Mineral'})

            if form.cleaned_data['calcular_cerveza']:
                porcentaje_cheve = 0.6 if form.cleaned_data['calcular_destilados'] else 1.0
                litros_cheve = (total_tragos * porcentaje_cheve) * 0.355
                cartones = math.ceil(litros_cheve / (24 * 0.355))
                resultado['insumos'].append({'nombre': 'Cerveza (Cartones 24 pzas)', 'cantidad': cartones, 'nota': 'Calculado en medias/latas'})
            
            garrafones = math.ceil(p / 40)
            resultado['insumos'].append({'nombre': 'Agua Purificada (Garrafones)', 'cantidad': garrafones, 'nota': 'Para servicio y cocina'})

    else:
        form = CalculadoraForm()

    context = admin.site.each_context(request)
    context.update({'form': form, 'resultado': resultado})
    return render(request, 'admin/calculadora.html', context)

# --- 6. EXPORTAR CIERRE CONTABLE (EXCEL CON FORMATO) ---
@staff_member_required
def exportar_cierre_excel(request):
    # CANDADO DE SEGURIDAD
    if not (request.user.is_superuser or request.user.groups.filter(name='Gerencia').exists()):
        messages.error(request, "⛔ Acceso denegado: Solo Gerencia puede descargar reportes.")
        return redirect('/admin/')

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    hoy = timezone.now()
    nombre_archivo = f"Contabilidad_Mes_{hoy.strftime('%B_%Y')}.xlsx"
    response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'

    wb = openpyxl.Workbook()
    
    # --- PESTAÑA 1: INGRESOS ---
    ws_ingresos = wb.active
    ws_ingresos.title = "Ingresos (Cobrado)"
    ws_ingresos.append(['Fecha Cobro', 'Cliente', 'Concepto / Evento', 'Método Pago', 'Monto Recibido'])
    
    header_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    for cell in ws_ingresos[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill

    pagos_mes = Pago.objects.filter(fecha_pago__month=hoy.month, fecha_pago__year=hoy.year)
    total_ingresos = 0
    for pago in pagos_mes:
        ws_ingresos.append([
            pago.fecha_pago,
            pago.cotizacion.cliente.nombre,
            f"Abono a Evento: {pago.cotizacion.fecha_evento} ({pago.cotizacion.producto.nombre})",
            pago.metodo,
            pago.monto
        ])
        total_ingresos += pago.monto
        # Formato Moneda Columna E
        ws_ingresos.cell(row=ws_ingresos.max_row, column=5).number_format = '#,##0.00'

    ws_ingresos.append(['', '', '', 'TOTAL INGRESOS:', total_ingresos])
    cell_total = ws_ingresos.cell(row=ws_ingresos.max_row, column=5)
    cell_total.font = Font(bold=True)
    cell_total.number_format = '#,##0.00'

    # --- PESTAÑA 2: GASTOS ---
    ws_gastos = wb.create_sheet(title="Gastos (Pagado)")
    ws_gastos.append(['Fecha Gasto', 'Proveedor', 'Descripción', 'Categoría', 'Monto Pagado', 'UUID (Fiscal)', 'Tipo'])
    
    gastos_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    for cell in ws_gastos[1]:
        cell.font = Font(bold=True)
        cell.fill = gastos_fill

    gastos_mes = Gasto.objects.filter(fecha_gasto__month=hoy.month, fecha_gasto__year=hoy.year)
    total_egresos = 0
    for g in gastos_mes:
        ws_gastos.append([
            g.fecha_gasto, g.proveedor, g.descripcion,
            g.get_categoria_display(), g.monto, g.uuid, "XML" if g.archivo_xml else "Manual"
        ])
        total_egresos += g.monto if g.monto else 0
        # Formato Moneda Columna E
        ws_gastos.cell(row=ws_gastos.max_row, column=5).number_format = '#,##0.00'

    ws_gastos.append(['', '', '', 'TOTAL GASTOS:', total_egresos])
    cell_total_g = ws_gastos.cell(row=ws_gastos.max_row, column=5)
    cell_total_g.font = Font(bold=True)
    cell_total_g.number_format = '#,##0.00'

    wb.save(response)
    return response