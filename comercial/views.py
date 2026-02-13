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
from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncMonth
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from decimal import Decimal
from weasyprint import HTML
from django.core.management import call_command

from .models import Cotizacion, Gasto, Pago, ItemCotizacion, Compra
from .forms import CalculadoraForm
from .services import CalculadoraBarraService, actualizar_item_cotizacion # Importamos Servicio

try:
    from facturacion.models import SolicitudFactura
except ImportError:
    SolicitudFactura = None 

# ==========================================
# 0. LÓGICA DE LISTA DE COMPRAS
# ==========================================
def generar_lista_compras_barra(cotizacion):
    calc = CalculadoraBarraService(cotizacion)
    datos = calc.calcular()
    if not datos: return {}

    lista_compras = {
        'Licores y Alcohol': [],
        'Bebidas y Mezcladores': [],
        'Frutas y Verduras': [],
        'Abarrotes y Consumibles': []
    }

    # Alcohol
    if datos['cervezas_unidades'] > 0:
        cajas = math.ceil(datos['cervezas_unidades'] / 12.0)
        lista_compras['Licores y Alcohol'].append({'item': 'Cerveza Nacional (Caguama 940ml)', 'cantidad': cajas, 'unidad': 'Cajas (12u)'})

    if datos['botellas_nacional'] > 0:
        b = datos['botellas_nacional']
        lista_compras['Licores y Alcohol'].append({'item': 'Tequila (Tradicional/Cuervo)', 'cantidad': math.ceil(b * 0.4), 'unidad': 'Botellas'})
        lista_compras['Licores y Alcohol'].append({'item': 'Whisky (Red Label)', 'cantidad': math.ceil(b * 0.3), 'unidad': 'Botellas'})
        lista_compras['Licores y Alcohol'].append({'item': 'Ron (Bacardí)', 'cantidad': math.ceil(b * 0.2), 'unidad': 'Botellas'})
        lista_compras['Licores y Alcohol'].append({'item': 'Vodka (Smirnoff)', 'cantidad': math.ceil(b * 0.1), 'unidad': 'Botellas'})

    if datos['botellas_premium'] > 0:
        b = datos['botellas_premium']
        lista_compras['Licores y Alcohol'].append({'item': 'Tequila Premium (Don Julio 70)', 'cantidad': math.ceil(b * 0.4), 'unidad': 'Botellas'})
        lista_compras['Licores y Alcohol'].append({'item': 'Whisky Premium (Black Label)', 'cantidad': math.ceil(b * 0.3), 'unidad': 'Botellas'})
        lista_compras['Licores y Alcohol'].append({'item': 'Ginebra / Ron Premium', 'cantidad': math.ceil(b * 0.3), 'unidad': 'Botellas'})

    # Mezcladores (Desglose real según litros calculados por el servicio)
    if l := datos['litros_mezcladores']:
        lista_compras['Bebidas y Mezcladores'].append({'item': 'Coca-Cola (2.5L)', 'cantidad': math.ceil((l * 0.6) / 2.5), 'unidad': 'Botellas'})
        lista_compras['Bebidas y Mezcladores'].append({'item': 'Refresco Toronja (2L)', 'cantidad': math.ceil((l * 0.2) / 2.0), 'unidad': 'Botellas'})
        lista_compras['Bebidas y Mezcladores'].append({'item': 'Agua Mineral (2L)', 'cantidad': math.ceil((l * 0.2) / 2.0), 'unidad': 'Botellas'})

    if datos['litros_agua'] > 0:
        lista_compras['Bebidas y Mezcladores'].append({'item': 'Agua Natural (Garrafón)', 'cantidad': math.ceil(datos['litros_agua'] / 20), 'unidad': 'Garrafones'})

    # Hielo
    lista_compras['Abarrotes y Consumibles'].append({
        'item': 'Hielo (Bolsa 20kg)', 
        'cantidad': datos['bolsas_hielo_20kg'], 
        'unidad': 'Bolsas',
        'nota': datos['hielo_info']
    })

    # Coctelería
    if cotizacion.incluye_cocteleria_basica:
        lista_compras['Frutas y Verduras'].append({'item': 'Limón Persa', 'cantidad': math.ceil(cotizacion.num_personas / 8), 'unidad': 'Kg'})
        lista_compras['Frutas y Verduras'].append({'item': 'Hierbabuena', 'cantidad': math.ceil(cotizacion.num_personas / 15), 'unidad': 'Manojos'})
        lista_compras['Abarrotes y Consumibles'].append({'item': 'Jarabe Natural', 'cantidad': math.ceil(cotizacion.num_personas / 40), 'unidad': 'Litros'})

    if cotizacion.incluye_cocteleria_premium:
        lista_compras['Frutas y Verduras'].append({'item': 'Frutos Rojos', 'cantidad': math.ceil(cotizacion.num_personas / 20), 'unidad': 'Bolsas'})
        lista_compras['Abarrotes y Consumibles'].append({'item': 'Café Espresso', 'cantidad': 1, 'unidad': 'Kg'})

    # Insumos Generales
    lista_compras['Abarrotes y Consumibles'].append({'item': 'Servilletas / Popotes', 'cantidad': 1, 'unidad': 'Kit'})

    return lista_compras

# ==========================================
# 1. DASHBOARD
# ==========================================
@staff_member_required 
def ver_dashboard_kpis(request):
    context = admin.site.each_context(request)
    hoy = timezone.now()
    
    ventas_mes = Cotizacion.objects.filter(estado__in=['CONFIRMADA', 'ACEPTADA'], fecha_evento__year=hoy.year, fecha_evento__month=hoy.month).aggregate(total=Sum('precio_final'))['total'] or 0
    gastos_mes = Compra.objects.filter(fecha_emision__year=hoy.year, fecha_emision__month=hoy.month).aggregate(total=Sum('total'))['total'] or 0
    utilidad_mes = ventas_mes - gastos_mes

    solicitudes_count = 0
    if SolicitudFactura:
        solicitudes_count = SolicitudFactura.objects.filter(fecha_solicitud__month=hoy.month).count()

    proximo_evento = Cotizacion.objects.filter(fecha_evento__gte=hoy.date(), estado='CONFIRMADA').order_by('fecha_evento').first()
    ultimos_eventos = Cotizacion.objects.filter(fecha_evento__gte=hoy.date(), estado='CONFIRMADA').order_by('fecha_evento')[:5]

    ventas_data = Cotizacion.objects.filter(estado='CONFIRMADA').annotate(mes=TruncMonth('fecha_evento')).values('mes').annotate(total=Sum('precio_final')).order_by('mes')
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

# ==========================================
# 2. REPORTES
# ==========================================
@staff_member_required
def generar_lista_compras(request):
    if request.method == 'POST':
        return render(request, 'comercial/reporte_form.html', {'titulo': 'Reporte Masivo en Construcción'})
    return render(request, 'comercial/reporte_form.html', {'titulo': 'Generar Lista de Compras'})

@staff_member_required
def descargar_lista_compras_pdf(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    lista_insumos = generar_lista_compras_barra(cotizacion)
    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"

    html_string = render_to_string('pdf_lista_compras.html', {
        'cotizacion': cotizacion, 'lista': lista_insumos, 'logo_url': logo_url, 'fecha_impresion': timezone.now()
    })
    
    html = HTML(string=html_string, base_url=request.build_absolute_uri())
    result = html.write_pdf()
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename=Checklist_Barra_{cotizacion.id}.pdf'
    response.write(result)
    return response

# ==========================================
# 3. PDF Y EMAIL
# ==========================================
def obtener_contexto_cotizacion(cotizacion):
    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"
    
    # Usamos el servicio aquí también
    calc = CalculadoraBarraService(cotizacion)
    datos_barra = calc.calcular()
    
    return {
        'cotizacion': cotizacion, 'items': cotizacion.items.all(), 
        'logo_url': logo_url, 'total_pagado': cotizacion.total_pagado(),
        'saldo_pendiente': cotizacion.saldo_pendiente(), 'barra': datos_barra
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

# ==========================================
# 4. CALENDARIO Y EXPORTS
# ==========================================
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

@staff_member_required
def exportar_cierre_excel(request):
    if not (request.user.is_superuser or request.user.groups.filter(name='Gerencia').exists()): return redirect('/admin/')
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    hoy = timezone.now()
    response['Content-Disposition'] = f'attachment; filename="Contabilidad_{hoy.strftime("%B_%Y")}.xlsx"'
    wb = openpyxl.Workbook()
    ws_ingresos = wb.active
    ws_ingresos.title = "Ingresos"
    ws_ingresos.append(['Fecha', 'Cliente', 'Monto', 'Metodo'])
    for p in Pago.objects.filter(fecha_pago__month=hoy.month):
        ws_ingresos.append([p.fecha_pago, p.cotizacion.cliente.nombre, p.monto, p.metodo])
    ws_gastos = wb.create_sheet(title="Gastos")
    ws_gastos.append(['Fecha', 'Proveedor', 'Total Factura', 'RFC Emisor'])
    for c in Compra.objects.filter(fecha_emision__month=hoy.month):
        ws_gastos.append([c.fecha_emision, c.proveedor, c.total, c.rfc_emisor])
    wb.save(response)
    return response

@staff_member_required
def exportar_reporte_cotizaciones(request):
    if request.method != 'POST': return render(request, 'comercial/reporte_form.html')
    fecha_inicio = request.POST.get('fecha_inicio')
    fecha_fin = request.POST.get('fecha_fin')
    estado = request.POST.get('estado')
    
    # CORRECCION N+1: Usamos prefetch_related
    cotizaciones = Cotizacion.objects.all().select_related('cliente').prefetch_related('gasto_set__compra').order_by('fecha_evento')
    
    if fecha_inicio: cotizaciones = cotizaciones.filter(fecha_evento__gte=fecha_inicio)
    if fecha_fin: cotizaciones = cotizaciones.filter(fecha_evento__lte=fecha_fin)
    if estado and estado != 'TODAS': cotizaciones = cotizaciones.filter(estado=estado)

    # ... (El resto del código de reporte se mantiene igual, ya optimizado por el prefetch) ...
    # Copia el resto de la función exportar_reporte_cotizaciones del archivo original, 
    # ya que la lógica interna de sumas estaba bien, solo fallaba el query.
    # Por brevedad en esta respuesta, asumo que mantienes el bloque de cálculo de impuestos.
    
    # [BLOQUE DE CÁLCULO DE IMPUESTOS Y RENDERIZADO DEL PDF VA AQUÍ - IDÉNTICO AL ANTERIOR]
    # ...
    # (Si necesitas que repita todo el bloque de sumas, pídemelo, pero es exactamente el mismo de tu archivo original)
    
    # Para completar el script funcional:
    t_subtotal = Decimal(0)
    t_descuento = Decimal(0)
    t_base_real = Decimal(0)
    t_total_ventas = Decimal(0)
    t_iva_trasladado = Decimal(0)
    t_ret_isr = Decimal(0)
    t_gastos_ev_fiscal_base = Decimal(0)
    t_gastos_ev_fiscal_iva = Decimal(0)
    t_gastos_ev_nofiscal = Decimal(0)
    t_gastos_op_fiscal_base = Decimal(0)
    t_gastos_op_fiscal_iva = Decimal(0)
    t_gastos_op_nofiscal = Decimal(0)
    datos_tabla = []
    
    for c in cotizaciones:
        base_real_venta = c.subtotal - c.descuento
        t_subtotal += c.subtotal
        t_descuento += c.descuento
        t_base_real += base_real_venta
        t_iva_trasladado += c.iva
        t_ret_isr += c.retencion_isr
        t_total_ventas += c.precio_final
        
        ev_fiscal_base = Decimal(0)
        ev_fiscal_iva = Decimal(0)
        ev_nofiscal = Decimal(0)
        
        # Ahora esto no golpea la BD N veces gracias al prefetch_related
        gastos_evento = c.gasto_set.all() 
        
        for g in gastos_evento:
            total_linea = g.total_linea or Decimal(0)
            compra = g.compra
            if compra.uuid: 
                if compra.total > 0 and compra.iva > 0:
                    factor = total_linea / compra.total
                    iva_prop = factor * compra.iva
                    base_prop = total_linea - iva_prop
                else:
                    iva_prop = Decimal(0)
                    base_prop = total_linea
                ev_fiscal_base += base_prop
                ev_fiscal_iva += iva_prop
            else: 
                ev_nofiscal += total_linea

        t_gastos_ev_fiscal_base += ev_fiscal_base
        t_gastos_ev_fiscal_iva += ev_fiscal_iva
        t_gastos_ev_nofiscal += ev_nofiscal
        utilidad_bruta = base_real_venta - (ev_fiscal_base + ev_nofiscal)

        datos_tabla.append({
            'folio': c.id, 'fecha': c.fecha_evento, 'cliente': c.cliente.nombre,
            'producto': c.nombre_evento, 'base_real_venta': base_real_venta,
            'iva_trasladado': c.iva, 'venta_total': c.precio_final,
            'gasto_fiscal_base': ev_fiscal_base, 'gasto_nofiscal': ev_nofiscal,
            'iva_acreditable': ev_fiscal_iva, 'utilidad': utilidad_bruta
        })

    gastos_qs = Gasto.objects.filter(evento_relacionado__isnull=True).select_related('compra')
    if fecha_inicio: gastos_qs = gastos_qs.filter(fecha_gasto__gte=fecha_inicio)
    if fecha_fin: gastos_qs = gastos_qs.filter(fecha_gasto__lte=fecha_fin)

    ops_fiscales = []
    ops_nofiscales = []
    
    for g in gastos_qs:
        total_linea = g.total_linea or Decimal(0)
        compra = g.compra
        if compra.uuid:
            if compra.total > 0 and compra.iva > 0:
                factor = total_linea / compra.total
                iva_prop = factor * compra.iva
                base_prop = total_linea - iva_prop
            else:
                iva_prop = Decimal(0)
                base_prop = total_linea
            t_gastos_op_fiscal_base += base_prop
            t_gastos_op_fiscal_iva += iva_prop
            
            found = False
            for item in ops_fiscales:
                if item['cat'] == g.categoria:
                    item['base'] += base_prop
                    item['iva'] += iva_prop
                    item['total'] += total_linea
                    found = True
                    break
            if not found: ops_fiscales.append({'cat': g.categoria, 'base': base_prop, 'iva': iva_prop, 'total': total_linea})
        else:
            t_gastos_op_nofiscal += total_linea
            found = False
            for item in ops_nofiscales:
                if item['cat'] == g.categoria:
                    item['total'] += total_linea
                    found = True
                    break
            if not found: ops_nofiscales.append({'cat': g.categoria, 'total': total_linea})

    cat_labels = dict(Gasto.CATEGORIAS)
    gastos_operativos_fiscales_list = [{'nombre': cat_labels.get(item['cat'], item['cat']), 'base': item['base'], 'iva': item['iva'], 'total': item['total']} for item in ops_fiscales]
    gastos_operativos_nofiscales_list = [{'nombre': cat_labels.get(item['cat'], item['cat']), 'total': item['total']} for item in ops_nofiscales]

    total_costos_deducibles = t_gastos_ev_fiscal_base + t_gastos_op_fiscal_base
    total_costos_no_deducibles = t_gastos_ev_nofiscal + t_gastos_op_nofiscal
    utilidad_neta_real = t_base_real - total_costos_deducibles - total_costos_no_deducibles
    total_iva_acreditable = t_gastos_ev_fiscal_iva + t_gastos_op_fiscal_iva
    iva_por_pagar = t_iva_trasladado - total_iva_acreditable

    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"
    
    context = {
        'datos': datos_tabla, 'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin, 'estado_filtro': estado,
        'logo_url': logo_url, 't_base_real': t_base_real, 't_iva_trasladado': t_iva_trasladado, 't_venta_total': t_total_ventas,
        't_ev_fiscal_base': t_gastos_ev_fiscal_base, 't_ev_nofiscal': t_gastos_ev_nofiscal, 't_ev_iva': t_gastos_ev_fiscal_iva,
        't_op_fiscal_base': t_gastos_op_fiscal_base, 't_op_nofiscal': t_gastos_op_nofiscal, 't_op_iva': t_gastos_op_fiscal_iva,
        'gastos_operativos_fiscales_list': gastos_operativos_fiscales_list, 'gastos_operativos_nofiscales_list': gastos_operativos_nofiscales_list,
        'total_costos_base': total_costos_deducibles, 'total_costos_nofiscal': total_costos_no_deducibles, 'utilidad_neta_real': utilidad_neta_real,
        'total_iva_acreditable': total_iva_acreditable, 'iva_por_pagar': iva_por_pagar
    }
    
    html = render_to_string('cotizaciones/pdf_reporte_ventas.html', context)
    response = HttpResponse(content_type='application/pdf')
    filename = f"Estado_Resultados_{fecha_inicio if fecha_inicio else 'General'}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    HTML(string=html).write_pdf(response)
    return response

@staff_member_required
def exportar_reporte_pagos(request):
    if request.method != 'POST': return render(request, 'comercial/reporte_form.html', {'titulo': 'Generar Reporte Detallado de Pagos'})
    fecha_inicio = request.POST.get('fecha_inicio')
    fecha_fin = request.POST.get('fecha_fin')
    pagos = Pago.objects.select_related('cotizacion', 'cotizacion__cliente', 'usuario').order_by('fecha_pago')
    if fecha_inicio: pagos = pagos.filter(fecha_pago__gte=fecha_inicio)
    if fecha_fin: pagos = pagos.filter(fecha_pago__lte=fecha_fin)
    total_ingresos = pagos.aggregate(Sum('monto'))['monto__sum'] or Decimal(0)
    metodos_data = pagos.values('metodo').annotate(total=Sum('monto')).order_by('-total')
    resumen_metodos = [{'nombre': dict(Pago.METODOS).get(item['metodo'], item['metodo']), 'total': item['total'], 'porcentaje': (item['total'] / total_ingresos * 100) if total_ingresos > 0 else 0} for item in metodos_data]
    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"
    context = {'pagos': pagos, 'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin, 'total_ingresos': total_ingresos, 'resumen_metodos': resumen_metodos, 'logo_url': logo_url, 'generado_el': timezone.now()}
    html = render_to_string('comercial/pdf_reporte_pagos.html', context)
    response = HttpResponse(content_type='application/pdf')
    filename = f"Reporte_Pagos_{fecha_inicio if fecha_inicio else 'Historico'}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    HTML(string=html).write_pdf(response)
    return response

@staff_member_required
def calculadora_insumos(request):
    # La calculadora simple se mantiene igual, aunque podríamos migrarla al servicio en el futuro.
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
    if not request.user.is_superuser: return HttpResponse("⛔ Acceso denegado.")
    try:
        call_command('migrate', interactive=False)
        return HttpResponse("✅ ¡MIGRACIÓN EXITOSA!")
    except Exception as e: return HttpResponse(f"❌ Error: {str(e)}")