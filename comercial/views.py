import json
import math
import os
import openpyxl 
from openpyxl.styles import Font, PatternFill
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.contrib import messages
from django.core.mail import EmailMessage
from django.conf import settings
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from weasyprint import HTML

# IMPORTAMOS MODELOS
from .models import Cotizacion, Gasto, Pago
from .forms import CalculadoraForm

try:
    from facturacion.models import SolicitudFactura
except ImportError:
    SolicitudFactura = None 

# --- 1. DASHBOARD PRINCIPAL (MEJORADO) ---
@staff_member_required 
def ver_dashboard_kpis(request):
    context = admin.site.each_context(request)
    context['app_list'] = admin.site.get_app_list(request)
    
    hoy = timezone.now()
    inicio_mes = hoy.replace(day=1, hour=0, minute=0, second=0)

    # 1. KPIs Rápidos
    ventas_mes = Cotizacion.objects.filter(
        estado__in=['CONFIRMADA', 'ACEPTADA'],
        fecha_evento__year=hoy.year,
        fecha_evento__month=hoy.month
    ).aggregate(total=Sum('precio_final'))['total'] or 0

    gastos_mes = Gasto.objects.filter(
        fecha_gasto__year=hoy.year,
        fecha_gasto__month=hoy.month
    ).aggregate(total=Sum('monto'))['total'] or 0

    utilidad_mes = ventas_mes - gastos_mes

    solicitudes_count = 0
    if SolicitudFactura:
        solicitudes_count = SolicitudFactura.objects.filter(
            fecha_solicitud__month=hoy.month
        ).count()

    # 2. Próximos Eventos
    proximo_evento = Cotizacion.objects.filter(
        fecha_evento__gte=hoy.date(),
        estado='CONFIRMADA'
    ).order_by('fecha_evento').first()

    ultimos_eventos = Cotizacion.objects.filter(
        fecha_evento__gte=hoy.date(),
        estado='CONFIRMADA'
    ).order_by('fecha_evento')[:5]

    # 3. DATOS PARA LA GRÁFICA (Comparativa Ventas vs Gastos)
    ventas_data = Cotizacion.objects.filter(estado='CONFIRMADA')\
        .annotate(mes=TruncMonth('fecha_evento'))\
        .values('mes')\
        .annotate(total=Sum('precio_final'))\
        .order_by('mes')
    
    gastos_data = Gasto.objects.annotate(mes=TruncMonth('fecha_gasto'))\
        .values('mes')\
        .annotate(total=Sum('monto'))\
        .order_by('mes')

    grafica_final = {}
    
    # Procesar Ventas
    for v in ventas_data:
        mes_str = v['mes'].strftime('%B %Y')
        grafica_final[mes_str] = {'ventas': float(v['total']), 'gastos': 0}
        
    # Procesar Gastos (unir con meses existentes o crear nuevos)
    for g in gastos_data:
        if g['mes']:
            mes_str = g['mes'].strftime('%B %Y')
            if mes_str not in grafica_final:
                grafica_final[mes_str] = {'ventas': 0, 'gastos': 0}
            grafica_final[mes_str]['gastos'] = float(g['total'])

    # Ordenar cronológicamente (opcional, básico por strings aquí)
    labels = list(grafica_final.keys())
    data_ventas = [v['ventas'] for v in grafica_final.values()]
    data_gastos = [v['gastos'] for v in grafica_final.values()]

    context.update({
        'ventas_mes': ventas_mes,
        'gastos_mes': gastos_mes, # Nuevo KPI
        'utilidad_mes': utilidad_mes, # Nuevo KPI
        'solicitudes_count': solicitudes_count,
        'proximo_evento': proximo_evento,
        'ultimos_eventos': ultimos_eventos,
        'chart_labels': json.dumps(labels),
        'chart_ventas': json.dumps(data_ventas), # Renombrado para claridad
        'chart_gastos': json.dumps(data_gastos), # Nuevo dataset
        'es_jefe': request.user.is_superuser or request.user.groups.filter(name='Gerencia').exists()
    })
    
    return render(request, 'admin/dashboard.html', context)


# --- 2. GENERAR LISTA DE COMPRAS (NUEVO) ---
@staff_member_required
def generar_lista_compras(request):
    if request.method == 'POST':
        fecha_inicio = request.POST.get('fecha_inicio')
        fecha_fin = request.POST.get('fecha_fin')

        # Buscamos eventos confirmados
        eventos = Cotizacion.objects.filter(
            estado='CONFIRMADA',
            fecha_evento__gte=fecha_inicio,
            fecha_evento__lte=fecha_fin
        ).prefetch_related('producto__componentes__insumo')

        compras = {}

        for evento in eventos:
            for componente in evento.producto.componentes.all():
                insumo = componente.insumo
                # Solo sumamos consumibles
                if insumo.categoria == 'CONSUMIBLE':
                    if insumo.nombre not in compras:
                        compras[insumo.nombre] = {
                            'cantidad': 0, 
                            'unidad': insumo.unidad_medida,
                            'stock': insumo.cantidad_stock
                        }
                    compras[insumo.nombre]['cantidad'] += float(componente.cantidad)

        lista_final = []
        for nombre, datos in compras.items():
            faltante = datos['cantidad'] - float(datos['stock'])
            lista_final.append({
                'nombre': nombre,
                'requerido': datos['cantidad'],
                'stock': datos['stock'],
                'comprar': faltante if faltante > 0 else 0,
                'unidad': datos['unidad']
            })

        # --- FIX IMAGEN (Ruta Local) ---
        ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
        logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"
        # -------------------------------

        context = {
            'fecha_inicio': fecha_inicio,
            'fecha_fin': fecha_fin,
            'lista': lista_final,
            'eventos': eventos,
            'logo_url': logo_url # <--- Usamos ruta local
        }
        
        # Usamos un template específico para esto (necesitarás crearlo, te daré el código)
        # O reusamos uno simple si prefieres
        html_string = render_to_string('comercial/pdf_lista_compras.html', context)
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="Lista_Compras_{fecha_inicio}.pdf"'
        HTML(string=html_string).write_pdf(response)
        return response

    # Si es GET, mostramos el formulario de fechas (reusamos el mismo de reportes)
    return render(request, 'comercial/reporte_form.html', {'titulo': 'Generar Lista de Compras'})


# --- 3. VISTAS EXISTENTES (PDF, EMAIL, CALENDARIO, ETC) ---

def generar_pdf_cotizacion(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    total_pagado = cotizacion.pagos.aggregate(Sum('monto'))['monto__sum'] or 0
    saldo_pendiente = cotizacion.precio_final - total_pagado
    
    # --- FIX IMAGEN (Ruta Local) ---
    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"
    # -------------------------------

    context = {
        'cotizacion': cotizacion, 
        'total_pagado': total_pagado, 
        'saldo_pendiente': saldo_pendiente,
        'logo_url': logo_url  # Pasamos la URL al template
    }
    
    html_string = render_to_string('cotizaciones/pdf_recibo.html', context)
    response = HttpResponse(content_type='application/pdf')
    filename = f"Recibo_{cotizacion.id}_{cotizacion.cliente.nombre}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    HTML(string=html_string).write_pdf(response)
    return response

def enviar_cotizacion_email(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    cliente = cotizacion.cliente
    if not cliente.email:
        messages.error(request, f"El cliente {cliente.nombre} no tiene email registrado.")
        return redirect(request.META.get('HTTP_REFERER', '/admin/'))
    
    total_pagado = cotizacion.pagos.aggregate(Sum('monto'))['monto__sum'] or 0
    saldo_pendiente = cotizacion.precio_final - total_pagado
    
    # --- FIX IMAGEN (Ruta Local) ---
    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"
    # -------------------------------

    context = {
        'cotizacion': cotizacion, 
        'total_pagado': total_pagado, 
        'saldo_pendiente': saldo_pendiente,
        'logo_url': logo_url # Pasamos la URL al template
    }
    
    html_string = render_to_string('cotizaciones/pdf_recibo.html', context)
    html = HTML(string=html_string)
    pdf_file = html.write_pdf()
    
    asunto = f"Recibo de pago - Evento {cotizacion.fecha_evento}"
    mensaje = f"""
    Hola {cliente.nombre},
    Adjunto encontrarás el recibo actualizado de tu evento.
    Total: ${cotizacion.precio_final} | Pendiente: ${saldo_pendiente}
    Saludos, Quinta Kooxtanil
    """
    
    email = EmailMessage(asunto, mensaje, settings.DEFAULT_FROM_EMAIL, [cliente.email])
    email.attach(f"Recibo_{cotizacion.id}.pdf", pdf_file, 'application/pdf')
    email.send()
    messages.success(request, f"✅ Correo enviado a {cliente.email}")
    return redirect(request.META.get('HTTP_REFERER', '/admin/'))

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
            factor = 2.0 if tipo == 'boda' else 1.5
            if tipo == 'empresarial': factor = 1.0
            if clima == 'calor': factor *= 1.2 
            total_tragos = p * h * factor
            resultado = {'total_tragos_estimados': int(total_tragos), 'insumos': []}
            
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

@staff_member_required
def exportar_cierre_excel(request):
    if not (request.user.is_superuser or request.user.groups.filter(name='Gerencia').exists()):
        messages.error(request, "⛔ Acceso denegado")
        return redirect('/admin/')
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    hoy = timezone.now()
    response['Content-Disposition'] = f'attachment; filename="Contabilidad_{hoy.strftime("%B_%Y")}.xlsx"'
    wb = openpyxl.Workbook()
    
    ws_ingresos = wb.active
    ws_ingresos.title = "Ingresos"
    ws_ingresos.append(['Fecha', 'Cliente', 'Monto', 'Metodo'])
    pagos = Pago.objects.filter(fecha_pago__month=hoy.month)
    for p in pagos: ws_ingresos.append([p.fecha_pago, p.cotizacion.cliente.nombre, p.monto, p.metodo])
    
    ws_gastos = wb.create_sheet(title="Gastos")
    ws_gastos.append(['Fecha', 'Proveedor', 'Monto'])
    gastos = Gasto.objects.filter(fecha_gasto__month=hoy.month)
    for g in gastos: ws_gastos.append([g.fecha_gasto, g.proveedor, g.monto])
    
    wb.save(response)
    return response

@staff_member_required
def exportar_reporte_cotizaciones(request):
    if request.method == 'POST':
        fecha_inicio = request.POST.get('fecha_inicio')
        fecha_fin = request.POST.get('fecha_fin')
        estado = request.POST.get('estado')

        cotizaciones = Cotizacion.objects.all().select_related('cliente', 'producto').order_by('fecha_evento')

        if fecha_inicio: cotizaciones = cotizaciones.filter(fecha_evento__gte=fecha_inicio)
        if fecha_fin: cotizaciones = cotizaciones.filter(fecha_evento__lte=fecha_fin)
        if estado and estado != 'TODAS': cotizaciones = cotizaciones.filter(estado=estado)

        total_ventas = 0; total_pagado = 0; total_pendiente = 0; total_gastos = 0; total_ganancia = 0
        datos_tabla = []
        for c in cotizaciones:
            pagado = c.pagos.aggregate(Sum('monto'))['monto__sum'] or 0
            pendiente = c.precio_final - pagado
            gastos_reales = c.gasto_set.aggregate(total=Sum('monto'))['total'] or 0
            ganancia_real = c.precio_final - gastos_reales
            margen = (ganancia_real / c.precio_final * 100) if c.precio_final > 0 else 0

            total_ventas += c.precio_final; total_pagado += pagado; total_pendiente += pendiente
            total_gastos += gastos_reales; total_ganancia += ganancia_real

            datos_tabla.append({
                'folio': c.id, 'fecha': c.fecha_evento, 'cliente': c.cliente.nombre,
                'producto': c.producto.nombre, 'estado': c.get_estado_display(),
                'total': c.precio_final, 'pagado': pagado, 'pendiente': pendiente,
                'gastos': gastos_reales, 'ganancia': ganancia_real, 'margen': margen
            })

        pagos_del_periodo = Pago.objects.filter(cotizacion__in=cotizaciones)
        total_efectivo = pagos_del_periodo.filter(metodo='EFECTIVO').aggregate(total=Sum('monto'))['total'] or 0
        total_transferencia = pagos_del_periodo.filter(metodo='TRANSFERENCIA').aggregate(total=Sum('monto'))['total'] or 0
        total_ingresado = total_efectivo + total_transferencia

        # --- FIX IMAGEN (Ruta Local) ---
        ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
        logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"
        # -------------------------------

        context = {
            'datos': datos_tabla, 'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin, 'estado_filtro': estado,
            'total_ventas': total_ventas, 'total_pagado': total_pagado, 'total_pendiente': total_pendiente,
            'total_gastos': total_gastos, 'total_ganancia': total_ganancia,
            'total_efectivo': total_efectivo, 'total_transferencia': total_transferencia, 'total_ingresado': total_ingresado,
            'logo_url': logo_url # <--- Usamos ruta local
        }

        html_string = render_to_string('cotizaciones/pdf_reporte_ventas.html', context)
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="Reporte_Rentabilidad_{fecha_inicio}.pdf"'
        HTML(string=html_string).write_pdf(response)
        return response

    return render(request, 'comercial/reporte_form.html')