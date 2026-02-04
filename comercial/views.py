import json
import math
import os
import openpyxl 
from openpyxl.styles import Font, PatternFill
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.utils.html import strip_tags
from django.conf import settings
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from decimal import Decimal
from weasyprint import HTML
from django.core.management import call_command

# IMPORTANTE: Aseguramos importar Compra y Gasto
from .models import Cotizacion, Gasto, Pago, ItemCotizacion, Compra
from .forms import CalculadoraForm

try:
    from facturacion.models import SolicitudFactura
except ImportError:
    SolicitudFactura = None 

# --- 1. DASHBOARD PRINCIPAL ---
@staff_member_required 
def ver_dashboard_kpis(request):
    context = admin.site.each_context(request)
    hoy = timezone.now()
    
    # 1. Ventas del Mes (Cotizaciones Confirmadas)
    ventas_mes = Cotizacion.objects.filter(
        estado__in=['CONFIRMADA', 'ACEPTADA'],
        fecha_evento__year=hoy.year,
        fecha_evento__month=hoy.month
    ).aggregate(total=Sum('precio_final'))['total'] or 0

    # 2. Gastos del Mes (Usamos COMPRA ahora, que tiene el total de la factura XML)
    gastos_mes = Compra.objects.filter(
        fecha_emision__year=hoy.year,
        fecha_emision__month=hoy.month
    ).aggregate(total=Sum('total'))['total'] or 0

    utilidad_mes = ventas_mes - gastos_mes

    solicitudes_count = 0
    if SolicitudFactura:
        solicitudes_count = SolicitudFactura.objects.filter(fecha_solicitud__month=hoy.month).count()

    proximo_evento = Cotizacion.objects.filter(fecha_evento__gte=hoy.date(), estado='CONFIRMADA').order_by('fecha_evento').first()
    ultimos_eventos = Cotizacion.objects.filter(fecha_evento__gte=hoy.date(), estado='CONFIRMADA').order_by('fecha_evento')[:5]

    # Gráficas
    ventas_data = Cotizacion.objects.filter(estado='CONFIRMADA').annotate(mes=TruncMonth('fecha_evento')).values('mes').annotate(total=Sum('precio_final')).order_by('mes')
    
    # Actualizado: Agrupamos COMPRAS por mes para la gráfica de gastos
    gastos_data = Compra.objects.annotate(mes=TruncMonth('fecha_emision')).values('mes').annotate(total=Sum('total')).order_by('mes')

    grafica_final = {}
    for v in ventas_data:
        if v['mes']: grafica_final[v['mes'].strftime('%B %Y')] = {'ventas': float(v['total']), 'gastos': 0}
        
    for g in gastos_data:
        if g['mes']:
            mes_str = g['mes'].strftime('%B %Y')
            if mes_str not in grafica_final: grafica_final[mes_str] = {'ventas': 0, 'gastos': 0}
            grafica_final[mes_str]['gastos'] = float(g['total'])

    context.update({
        'ventas_mes': ventas_mes, 'gastos_mes': gastos_mes, 'utilidad_mes': utilidad_mes,
        'solicitudes_count': solicitudes_count, 'proximo_evento': proximo_evento, 'ultimos_eventos': ultimos_eventos,
        'chart_labels': json.dumps(list(grafica_final.keys())),
        'chart_ventas': json.dumps([v['ventas'] for v in grafica_final.values()]),
        'chart_gastos': json.dumps([v['gastos'] for v in grafica_final.values()]),
        'es_jefe': request.user.is_superuser or request.user.groups.filter(name='Gerencia').exists()
    })
    return render(request, 'admin/dashboard.html', context)

# --- 2. GENERAR LISTA DE COMPRAS ---
@staff_member_required
def generar_lista_compras(request):
    if request.method == 'POST':
        fecha_inicio = request.POST.get('fecha_inicio')
        fecha_fin = request.POST.get('fecha_fin')

        eventos = Cotizacion.objects.filter(
            estado='CONFIRMADA',
            fecha_evento__gte=fecha_inicio,
            fecha_evento__lte=fecha_fin
        ).prefetch_related('items__producto__componentes__subproducto__receta__insumo', 'items__insumo')

        compras = {}

        for evento in eventos:
            for item in evento.items.all():
                cantidad_item = item.cantidad
                
                # A) ES PRODUCTO -> DESGLOSAR
                if item.producto:
                    for comp in item.producto.componentes.all(): # Subproductos
                        sub_qty = comp.cantidad * cantidad_item
                        for receta in comp.subproducto.receta.all(): # Insumos
                            insumo = receta.insumo
                            if insumo.categoria == 'CONSUMIBLE':
                                total_necesario = receta.cantidad * sub_qty
                                if insumo.nombre not in compras:
                                    compras[insumo.nombre] = {'cantidad': 0, 'unidad': insumo.unidad_medida, 'stock': insumo.cantidad_stock}
                                compras[insumo.nombre]['cantidad'] += float(total_necesario)
                
                # B) ES INSUMO DIRECTO
                elif item.insumo:
                    insumo = item.insumo
                    if insumo.categoria == 'CONSUMIBLE':
                        if insumo.nombre not in compras:
                            compras[insumo.nombre] = {'cantidad': 0, 'unidad': insumo.unidad_medida, 'stock': insumo.cantidad_stock}
                        compras[insumo.nombre]['cantidad'] += float(cantidad_item)

        lista_final = []
        for nombre, datos in compras.items():
            faltante = datos['cantidad'] - float(datos['stock'])
            lista_final.append({
                'nombre': nombre, 'requerido': datos['cantidad'],
                'stock': datos['stock'], 'comprar': faltante if faltante > 0 else 0,
                'unidad': datos['unidad']
            })

        ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
        logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"
        
        html_string = render_to_string('comercial/pdf_lista_compras.html', {
            'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin, 'lista': lista_final,
            'eventos': eventos, 'logo_url': logo_url
        })
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="Lista_Compras_{fecha_inicio}.pdf"'
        HTML(string=html_string).write_pdf(response)
        return response

    return render(request, 'comercial/reporte_form.html', {'titulo': 'Generar Lista de Compras'})

# --- 3. CONTEXTO DE COTIZACIÓN (PDF) ---
def obtener_contexto_cotizacion(cotizacion):
    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"

    return {
        'cotizacion': cotizacion,
        'items': cotizacion.items.all(), 
        'logo_url': logo_url,
        'total_pagado': cotizacion.total_pagado(),
        'saldo_pendiente': cotizacion.saldo_pendiente()
    }

def generar_pdf_cotizacion(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    context = obtener_contexto_cotizacion(cotizacion)
    html_string = render_to_string('cotizaciones/pdf_recibo.html', context)
    
    response = HttpResponse(content_type='application/pdf')
    folio = f"COT-{cotizacion.id:03d}"
    filename = f"{folio}_{timezone.now().strftime('%d-%m-%Y')}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    HTML(string=html_string).write_pdf(response)
    return response

def enviar_cotizacion_email(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    cliente = cotizacion.cliente
    
    if not cliente.email:
        messages.error(request, f"El cliente {cliente.nombre} no tiene email.")
        return redirect(request.META.get('HTTP_REFERER', '/admin/'))
    
    try:
        context = obtener_contexto_cotizacion(cotizacion)
        context['cliente'] = cliente
        html_pdf = render_to_string('cotizaciones/pdf_recibo.html', context)
        pdf_file = HTML(string=html_pdf).write_pdf()
        
        folio = f"COT-{cotizacion.id:03d}"
        filename = f"{folio}_{timezone.now().strftime('%d-%m-%Y')}.pdf"

        html_email = render_to_string('emails/cotizacion.html', context)
        msg = EmailMultiAlternatives(f"Cotización {folio} - Quinta Ko'ox Tanil", strip_tags(html_email), settings.DEFAULT_FROM_EMAIL, [cliente.email])
        msg.attach_alternative(html_email, "text/html")
        msg.attach(filename, pdf_file, 'application/pdf')
        msg.send()
        
        messages.success(request, f"✅ Enviado a {cliente.email}")
    except Exception as e:
        messages.error(request, f"❌ Error: {e}")

    return redirect(request.META.get('HTTP_REFERER', '/admin/'))

# --- CALENDARIO ---
def ver_calendario(request):
    cotizaciones = Cotizacion.objects.exclude(estado='CANCELADA')
    eventos_lista = []
    for c in cotizaciones:
        color = '#28a745' if c.estado == 'CONFIRMADA' else '#6c757d'
        eventos_lista.append({
            'title': f"{c.cliente.nombre} - {c.nombre_evento}",
            'start': c.fecha_evento.strftime("%Y-%m-%d"),
            'color': color,
            'url': f'/admin/comercial/cotizacion/{c.id}/change/'
        })
    return render(request, 'admin/calendario.html', {'eventos_json': json.dumps(eventos_lista, cls=DjangoJSONEncoder)})

# --- EXPORTAR EXCEL (CONTABILIDAD GENERAL) ---
@staff_member_required
def exportar_cierre_excel(request):
    if not (request.user.is_superuser or request.user.groups.filter(name='Gerencia').exists()):
        return redirect('/admin/')
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    hoy = timezone.now()
    response['Content-Disposition'] = f'attachment; filename="Contabilidad_{hoy.strftime("%B_%Y")}.xlsx"'
    wb = openpyxl.Workbook()
    
    # 1. Ingresos
    ws_ingresos = wb.active
    ws_ingresos.title = "Ingresos"
    ws_ingresos.append(['Fecha', 'Cliente', 'Monto', 'Metodo'])
    for p in Pago.objects.filter(fecha_pago__month=hoy.month):
        ws_ingresos.append([p.fecha_pago, p.cotizacion.cliente.nombre, p.monto, p.metodo])
    
    # 2. Gastos (Usamos Compras/Facturas completas)
    ws_gastos = wb.create_sheet(title="Gastos")
    ws_gastos.append(['Fecha', 'Proveedor', 'Total Factura', 'RFC Emisor'])
    for c in Compra.objects.filter(fecha_emision__month=hoy.month):
        ws_gastos.append([c.fecha_emision, c.proveedor, c.total, c.rfc_emisor])
        
    wb.save(response)
    return response

# --- REPORTE DE UTILIDADES (CORREGIDO: DISTINCIÓN FACTURAS VS NOTAS) ---
@staff_member_required
def exportar_reporte_cotizaciones(request):
    if request.method != 'POST':
        return render(request, 'comercial/reporte_form.html')

    # --- INPUTS ---
    fecha_inicio = request.POST.get('fecha_inicio')
    fecha_fin = request.POST.get('fecha_fin')
    estado = request.POST.get('estado')
    
    # ==========================================
    # 1. EVENTOS (UTILIDAD BRUTA)
    # ==========================================
    cotizaciones = Cotizacion.objects.all().select_related('cliente').order_by('fecha_evento')
    
    if fecha_inicio: cotizaciones = cotizaciones.filter(fecha_evento__gte=fecha_inicio)
    if fecha_fin: cotizaciones = cotizaciones.filter(fecha_evento__lte=fecha_fin)
    if estado and estado != 'TODAS': cotizaciones = cotizaciones.filter(estado=estado)

    # Inicializamos acumuladores globales
    t_subtotal, t_descuento, t_base_real, t_total_ventas = 0,0,0,0
    t_iva, t_ret_isr = 0,0
    t_pagado = 0
    
    # Acumuladores de Gastos
    t_gastos_total_con_iva = 0
    t_gastos_base = 0
    t_gastos_iva = 0
    
    t_ganancia_eventos = 0
    
    datos_tabla = []
    
    for c in cotizaciones:
        pagado = c.pagos.aggregate(Sum('monto'))['monto__sum'] or Decimal(0)
        
        # --- NUEVA LÓGICA DE GASTOS: Iterar para verificar si es Factura (UUID) o Nota ---
        gastos_del_evento = c.gasto_set.all().select_related('compra')
        
        evento_gasto_total = Decimal(0) # Lo que salió del banco (Total con IVA o Nota)
        evento_gasto_iva_acreditable = Decimal(0) # Solo lo que es fiscal
        
        for g in gastos_del_evento:
            total_linea = g.total_linea or Decimal(0)
            evento_gasto_total += total_linea
            
            # Verificación Fiscal Inteligente:
            # Si la compra tiene UUID (Timbre) Y tiene IVA > 0, calculamos la parte proporcional.
            compra_padre = g.compra
            if compra_padre.uuid and compra_padre.iva > 0 and compra_padre.total > 0:
                # Prorrateo: (TotalLinea / TotalFactura) * IvaFactura
                factor = total_linea / compra_padre.total
                iva_proporcional = factor * compra_padre.iva
                evento_gasto_iva_acreditable += iva_proporcional
            else:
                # Es una Nota, Remisión o Factura tasa 0%: NO hay crédito de IVA.
                # El IVA acreditable es 0.
                pass 
        
        # El Gasto Base (Costo Real) es: Lo que pagué menos lo que recupero de impuestos
        evento_gasto_base = evento_gasto_total - evento_gasto_iva_acreditable
        
        # --- Datos Venta ---
        base_real_venta = c.subtotal - c.descuento
        
        # --- Calcular UTILIDAD REAL ---
        # (Venta sin IVA) - (Gasto sin IVA Recuperable)
        # Nota: Si el gasto fue "Nota", el gasto base es el 100% del pago, lo cual reduce correctamente la utilidad.
        ganancia_real = base_real_venta - evento_gasto_base
        
        # Sumar a Globales
        t_subtotal += c.subtotal
        t_descuento += c.descuento
        t_base_real += base_real_venta
        t_iva += c.iva
        t_ret_isr += c.retencion_isr
        t_total_ventas += c.precio_final
        t_pagado += pagado
        
        t_gastos_total_con_iva += evento_gasto_total
        t_gastos_base += evento_gasto_base
        t_gastos_iva += evento_gasto_iva_acreditable
        
        t_ganancia_eventos += ganancia_real

        datos_tabla.append({
            'folio': c.id, 'fecha': c.fecha_evento, 'cliente': c.cliente.nombre,
            'producto': c.nombre_evento, 
            'estado': c.get_estado_display(),
            'subtotal': c.subtotal, 'descuento': c.descuento, 
            'base_real': base_real_venta,
            'iva': c.iva, 'isr': c.retencion_isr, 'total': c.precio_final,
            
            # Nuevos valores desglosados
            'gastos_total': evento_gasto_total,
            'gastos_base': evento_gasto_base,
            'gastos_iva': evento_gasto_iva_acreditable,
            
            'ganancia': ganancia_real
        })

    # ==========================================
    # 2. GASTOS OPERATIVOS (AGRUPADOS POR CATEGORIA)
    # ==========================================
    # También aplicamos la lógica de UUID aquí
    gastos_qs = Gasto.objects.filter(evento_relacionado__isnull=True).select_related('compra')
    if fecha_inicio: gastos_qs = gastos_qs.filter(fecha_gasto__gte=fecha_inicio)
    if fecha_fin: gastos_qs = gastos_qs.filter(fecha_gasto__lte=fecha_fin)

    gastos_procesados = [] # Lista temporal para agrupar manual
    
    total_gastos_op_total = 0
    total_gastos_op_base = 0
    total_gastos_op_iva = 0

    # Iteramos manual para verificar UUID
    for g in gastos_qs:
        total_linea = g.total_linea or Decimal(0)
        compra_padre = g.compra
        
        iva_linea = Decimal(0)
        if compra_padre.uuid and compra_padre.iva > 0 and compra_padre.total > 0:
            factor = total_linea / compra_padre.total
            iva_linea = factor * compra_padre.iva
        
        base_linea = total_linea - iva_linea
        
        # Acumulamos globales
        total_gastos_op_total += total_linea
        total_gastos_op_base += base_linea
        total_gastos_op_iva += iva_linea
        
        # Agrupamos en diccionario para la tabla
        found = False
        for item in gastos_procesados:
            if item['categoria'] == g.categoria:
                item['total'] += total_linea
                item['base'] += base_linea
                item['iva'] += iva_linea
                found = True
                break
        if not found:
            gastos_procesados.append({
                'categoria': g.categoria,
                'total': total_linea,
                'base': base_linea,
                'iva': iva_linea
            })

    # Ordenar y formatear nombres
    gastos_procesados.sort(key=lambda x: x['total'], reverse=True)
    
    gastos_operativos_display = []
    cat_labels = dict(Gasto.CATEGORIAS)
    
    for item in gastos_procesados:
        key = item['categoria']
        gastos_operativos_display.append({
            'nombre': cat_labels.get(key, key),
            'total': item['total'],
            'base': item['base'],
            'iva': item['iva']
        })
    
    # ==========================================
    # 3. RESULTADO FINAL
    # ==========================================
    # Utilidad Neta Real = Utilidad Bruta Eventos - Gastos Operativos BASE (sin IVA)
    utilidad_neta_real = t_ganancia_eventos - total_gastos_op_base

    # Calculo informativo de impuestos
    iva_por_pagar = t_iva - t_gastos_iva - total_gastos_op_iva

    # Flujo de Efectivo (Solo informativo)
    pagos = Pago.objects.filter(cotizacion__in=cotizaciones)
    total_efectivo = pagos.filter(metodo='EFECTIVO').aggregate(t=Sum('monto'))['t'] or 0
    total_transf = pagos.filter(metodo='TRANSFERENCIA').aggregate(t=Sum('monto'))['t'] or 0
    total_otros = pagos.exclude(metodo__in=['EFECTIVO','TRANSFERENCIA']).aggregate(t=Sum('monto'))['t'] or 0

    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"
    
    html = render_to_string('cotizaciones/pdf_reporte_ventas.html', {
        'datos': datos_tabla, 'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin, 'estado_filtro': estado,
        
        # Totales Eventos
        't_subtotal': t_subtotal, 't_descuento': t_descuento, 't_base_real': t_base_real,
        't_iva': t_iva, 't_ret_isr': t_ret_isr, 't_total_ventas': t_total_ventas,
        
        # Nuevos Totales Gastos Eventos
        't_gastos_base': t_gastos_base, 
        't_gastos_iva': t_gastos_iva,
        't_ganancia_eventos': t_ganancia_eventos,
        
        # Totales Operativos
        'gastos_operativos_list': gastos_operativos_display, 
        'total_gastos_op_total': total_gastos_op_total,
        'total_gastos_op_base': total_gastos_op_base,
        'total_gastos_op_iva': total_gastos_op_iva,
        
        # Resultados Finales
        'utilidad_neta_real': utilidad_neta_real,
        'iva_por_pagar': iva_por_pagar,
        
        # Flujo
        'total_efectivo': total_efectivo, 'total_transferencia': total_transf, 'total_otros': total_otros, 
        'total_ingresado': total_efectivo+total_transf+total_otros,
        'logo_url': logo_url
    })
    
    response = HttpResponse(content_type='application/pdf')
    filename = f"Reporte_Financiero_{fecha_inicio if fecha_inicio else 'General'}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    HTML(string=html).write_pdf(response)
    return response

# --- REPORTE DE PAGOS (NUEVO) ---
@staff_member_required
def exportar_reporte_pagos(request):
    if request.method != 'POST':
        # Renderiza el formulario si no es POST
        return render(request, 'comercial/reporte_form.html', {'titulo': 'Generar Reporte de Pagos'})

    # --- INPUTS ---
    fecha_inicio = request.POST.get('fecha_inicio')
    fecha_fin = request.POST.get('fecha_fin')
    
    # 1. CONSULTA DE PAGOS
    # Usamos select_related para optimizar la consulta a la cotización y al cliente
    pagos = Pago.objects.all().select_related('cotizacion', 'cotizacion__cliente').order_by('fecha_pago')
    
    if fecha_inicio: pagos = pagos.filter(fecha_pago__gte=fecha_inicio)
    if fecha_fin: pagos = pagos.filter(fecha_pago__lte=fecha_fin)

    # 2. TOTALES GENERALES
    total_ingresos = pagos.aggregate(Sum('monto'))['monto__sum'] or Decimal(0)
    
    # 3. RESUMEN POR MÉTODO DE PAGO
    # Agrupamos para saber cuánto entró por efectivo, transferencia, etc.
    metodos_data = pagos.values('metodo').annotate(total=Sum('monto')).order_by('-total')
    
    # Mapeamos las claves (ej. 'TARJETA_CREDITO') a nombres legibles usando las choices del modelo
    resumen_metodos = []
    dict_metodos = dict(Pago.METODOS) # Diccionario {CLAVE: Nombre Legible}
    
    for item in metodos_data:
        clave = item['metodo']
        nombre = dict_metodos.get(clave, clave) # Si no encuentra, usa la clave
        resumen_metodos.append({
            'nombre': nombre,
            'total': item['total']
        })

    # 4. RENDERIZAR PDF
    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"
    
    html = render_to_string('comercial/pdf_reporte_pagos.html', {
        'pagos': pagos,
        'fecha_inicio': fecha_inicio,
        'fecha_fin': fecha_fin,
        'total_ingresos': total_ingresos,
        'resumen_metodos': resumen_metodos,
        'logo_url': logo_url
    })
    
    response = HttpResponse(content_type='application/pdf')
    filename = f"Reporte_Pagos_{fecha_inicio if fecha_inicio else 'General'}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    HTML(string=html).write_pdf(response)
    return response

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
            resultado['insumos'].append({'nombre': 'Hielo (Bolsas 5kg)', 'cantidad': bolsas_hielo, 'nota': 'Aprox'})
            
            if form.cleaned_data['calcular_destilados']:
                vasos_con_refresco = total_tragos * 0.4 
                botellas_refresco = math.ceil(vasos_con_refresco / 8)
                resultado['insumos'].append({'nombre': 'Refrescos (2L)', 'cantidad': botellas_refresco, 'nota': 'Surtido'})

            if form.cleaned_data['calcular_cerveza']:
                porcentaje_cheve = 0.6 if form.cleaned_data['calcular_destilados'] else 1.0
                litros_cheve = (total_tragos * porcentaje_cheve) * 0.355
                cartones = math.ceil(litros_cheve / (24 * 0.355))
                resultado['insumos'].append({'nombre': 'Cerveza (Cartones)', 'cantidad': cartones, 'nota': 'Medias'})
            
            garrafones = math.ceil(p / 40)
            resultado['insumos'].append({'nombre': 'Agua Purificada', 'cantidad': garrafones, 'nota': 'Servicio'})
    else:
        form = CalculadoraForm()
    return render(request, 'admin/calculadora.html', {'form': form, 'resultado': resultado})

@staff_member_required
def forzar_migracion(request):
    if not request.user.is_superuser:
        return HttpResponse("⛔ Acceso denegado. Solo superusuarios.")
    
    try:
        call_command('migrate', interactive=False)
        return HttpResponse("✅ ¡MIGRACIÓN EXITOSA! La base de datos ya tiene la estructura de 3 niveles.")
    except Exception as e:
        return HttpResponse(f"❌ Error al migrar: {str(e)}")