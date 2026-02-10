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

# IMPORTANTE: Aseguramos importar los modelos necesarios
from .models import Cotizacion, Gasto, Pago, ItemCotizacion, Compra
from .forms import CalculadoraForm

try:
    from facturacion.models import SolicitudFactura
except ImportError:
    SolicitudFactura = None 

# ==========================================
# 0. FUNCIONES AUXILIARES (LÓGICA DE COMPRAS)
# ==========================================
def generar_lista_compras_barra(cotizacion):
    """
    Genera la lista de compras basada en las CASILLAS (Checkboxes) seleccionadas.
    """
    # Si no hay nada seleccionado, lista vacía
    checks = [
        cotizacion.incluye_refrescos, cotizacion.incluye_cerveza, 
        cotizacion.incluye_licor_nacional, cotizacion.incluye_licor_premium,
        cotizacion.incluye_cocteleria_basica, cotizacion.incluye_cocteleria_premium
    ]
    if not any(checks):
        return {}

    lista_compras = {
        'Bebidas y Mezcladores': [],
        'Frutas y Verduras': [],
        'Abarrotes y Consumibles': [],
        'Licores y Alcohol': []
    }

    # DATOS DEL EVENTO
    personas = cotizacion.num_personas
    horas = cotizacion.horas_servicio
    clima = cotizacion.clima

    # FACTORES CLIMA
    mult_liquido = 1.0
    mult_hielo = 1.0
    
    if clima == 'calor':
        mult_liquido = 1.3 # 30% mas
        mult_hielo = 1.4 # 40% mas
    elif clima == 'extremo':
        mult_liquido = 1.5
        mult_hielo = 1.6

    # ---------------------------------------------------------
    # 1. CÁLCULO DE LÍQUIDOS Y HIELO (BASE)
    # ---------------------------------------------------------
    litros_totales = personas * 1.5
    factor_hielo_base = 1.8 # Kg por persona
    
    if horas > 5:
        litros_totales += (personas * 0.3 * (horas - 5))
        factor_hielo_base += 0.4
    
    # Ajuste por Coctelería (Cualquiera de las dos consume más hielo/jugo)
    if cotizacion.incluye_cocteleria_basica or cotizacion.incluye_cocteleria_premium:
        factor_hielo_base *= 1.3 
        litros_totales *= 1.2
    
    # APLICAR CLIMA
    litros_totales = litros_totales * mult_liquido
    kilos_hielo = (personas * factor_hielo_base) * mult_hielo
    bolsas_hielo = math.ceil(kilos_hielo / 20) 

    # ---------------------------------------------------------
    # 2. GENERACIÓN DE ITEMS SEGÚN CHECKBOXES
    # ---------------------------------------------------------

    # --- A) REFRESCOS ---
    if cotizacion.incluye_refrescos:
        # Si hay cerveza, bajamos el refresco al 40%
        factor_refresco = 0.4 if cotizacion.incluye_cerveza else 1.0
        
        coca_cola_litros = (litros_totales * 0.60) * factor_refresco
        squirt_litros = (litros_totales * 0.20) * factor_refresco
        mineral_litros = (litros_totales * 0.20) * factor_refresco
        
        # Si hay coctelería premium (Aperol), necesitamos más agua mineral
        if cotizacion.incluye_cocteleria_premium:
            mineral_litros += (personas * 0.2)

        lista_compras['Bebidas y Mezcladores'].append({
            'item': 'Refresco de Cola (2.5L o 3L)',
            'cantidad': math.ceil(coca_cola_litros / 2.5), 'unidad': 'Botellas'
        })
        lista_compras['Bebidas y Mezcladores'].append({
            'item': 'Refresco Toronja/Lima (2.5L)',
            'cantidad': math.ceil(squirt_litros / 2.5), 'unidad': 'Botellas'
        })
        lista_compras['Bebidas y Mezcladores'].append({
            'item': 'Agua Mineral (Peñafiel 2L)',
            'cantidad': math.ceil(mineral_litros / 2), 'unidad': 'Botellas'
        })
        # Agua Natural
        cant_agua = (personas * 0.5) * mult_liquido
        lista_compras['Bebidas y Mezcladores'].append({
            'item': 'Agua Natural (Garrafón 20L)',
            'cantidad': math.ceil(cant_agua / 20), 'unidad': 'Garrafones'
        })

    # --- B) CERVEZA ---
    if cotizacion.incluye_cerveza:
        consumo_cheve = (personas * 1.2 * horas) * mult_liquido
        cartones = math.ceil(consumo_cheve / 24)
        lista_compras['Licores y Alcohol'].append({
            'item': 'Cerveza Nacional (Cartón 24u - Media)',
            'cantidad': cartones, 'unidad': 'Cartones'
        })

    # --- C) LICORES NACIONALES ---
    if cotizacion.incluye_licor_nacional:
        # Lógica: 1 botella cada 5 pax. Dividimos entre las 4 marcas principales.
        total_botellas = math.ceil(personas / 5)
        # Distribución sugerida: 40% Tequila, 30% Whisky, 20% Ron, 10% Vodka
        lista_compras['Licores y Alcohol'].append({'item': 'Tequila (Tradicional/Cuervo)', 'cantidad': math.ceil(total_botellas * 0.4), 'unidad': 'Botellas'})
        lista_compras['Licores y Alcohol'].append({'item': 'Whisky (Red Label)', 'cantidad': math.ceil(total_botellas * 0.3), 'unidad': 'Botellas'})
        lista_compras['Licores y Alcohol'].append({'item': 'Ron (Bacardí)', 'cantidad': math.ceil(total_botellas * 0.2), 'unidad': 'Botellas'})
        lista_compras['Licores y Alcohol'].append({'item': 'Vodka (Smirnoff/Absolut)', 'cantidad': math.ceil(total_botellas * 0.1) or 1, 'unidad': 'Botellas'})

    # --- D) LICORES PREMIUM ---
    if cotizacion.incluye_licor_premium:
        total_botellas_prem = math.ceil(personas / 5)
        # Distribución: 40% Tequila, 30% Whisky, 15% Ron, 15% Vodka
        lista_compras['Licores y Alcohol'].append({'item': 'Tequila Premium (Don Julio 70)', 'cantidad': math.ceil(total_botellas_prem * 0.4), 'unidad': 'Botellas'})
        lista_compras['Licores y Alcohol'].append({'item': 'Whisky Premium (Black Label)', 'cantidad': math.ceil(total_botellas_prem * 0.3), 'unidad': 'Botellas'})
        lista_compras['Licores y Alcohol'].append({'item': 'Ron Premium (Matusalem)', 'cantidad': math.ceil(total_botellas_prem * 0.15), 'unidad': 'Botellas'})
        lista_compras['Licores y Alcohol'].append({'item': 'Vodka Premium (Grey Goose)', 'cantidad': math.ceil(total_botellas_prem * 0.15), 'unidad': 'Botellas'})

    # --- E) COCTELERÍA BÁSICA ---
    if cotizacion.incluye_cocteleria_basica:
        # Mojitos, Margaritas
        lista_compras['Frutas y Verduras'].append({'item': 'Hierbabuena Fresca', 'cantidad': math.ceil(personas / 20), 'unidad': 'Manojos grandes'})
        lista_compras['Frutas y Verduras'].append({'item': 'Limón Persa (Extra)', 'cantidad': math.ceil(personas / 15), 'unidad': 'kg'})
        lista_compras['Abarrotes y Consumibles'].append({'item': 'Jarabe Natural', 'cantidad': math.ceil(personas / 40), 'unidad': 'Litros'})
        # Si no hay licores marcados, avisar que faltan
        if not (cotizacion.incluye_licor_nacional or cotizacion.incluye_licor_premium):
             lista_compras['Licores y Alcohol'].append({'item': '*** ALCOHOL PARA COCTELES NO SELECCIONADO ***', 'cantidad': 0, 'unidad': 'NOTA'})

    # --- F) COCTELERÍA PREMIUM ---
    if cotizacion.incluye_cocteleria_premium:
        # Carajillos (Licor 43), Aperol
        # 1 botella Licor 43 rinde 14 carajillos. Asumimos 30% invitados piden.
        pedidos_carajillo = personas * 0.4 
        botellas_43 = math.ceil(pedidos_carajillo / 14)
        
        # Aperol: 1 botella rinde 12 copas. 20% invitados.
        pedidos_aperol = personas * 0.2
        botellas_aperol = math.ceil(pedidos_aperol / 12)
        botellas_prosecco = math.ceil(pedidos_aperol / 5) # Prosecco rinde menos

        lista_compras['Licores y Alcohol'].append({'item': 'Licor 43 (Carajillos)', 'cantidad': botellas_43, 'unidad': 'Botellas'})
        lista_compras['Licores y Alcohol'].append({'item': 'Aperol', 'cantidad': botellas_aperol, 'unidad': 'Botellas'})
        lista_compras['Licores y Alcohol'].append({'item': 'Vino Espumoso / Prosecco', 'cantidad': botellas_prosecco, 'unidad': 'Botellas'})
        lista_compras['Abarrotes y Consumibles'].append({'item': 'Café Espresso (Grano/Cargas)', 'cantidad': math.ceil(pedidos_carajillo), 'unidad': 'Cargas/Dosis'})
        lista_compras['Frutas y Verduras'].append({'item': 'Naranjas (Garnish Aperol)', 'cantidad': math.ceil(personas / 40), 'unidad': 'kg'})

    # --- COMUNES ---
    # Limones base (siempre necesarios)
    kilos_limon_base = math.ceil(personas / 25)
    lista_compras['Frutas y Verduras'].append({
        'item': 'Limón (Uso general)',
        'cantidad': kilos_limon_base, 'unidad': 'kg'
    })

    lista_compras['Abarrotes y Consumibles'].append({
        'item': 'Hielo (Bolsa Grande 20kg)',
        'cantidad': bolsas_hielo, 'unidad': 'Bolsas'
    })
    lista_compras['Abarrotes y Consumibles'].append({
        'item': 'Servilletas Cocktail',
        'cantidad': 1 if personas < 100 else 2, 'unidad': 'Paquetes'
    })
    lista_compras['Abarrotes y Consumibles'].append({
        'item': 'Popotes / Agitadores',
        'cantidad': 1, 'unidad': 'Caja'
    })
    
    return lista_compras

# ==========================================
# 1. DASHBOARD PRINCIPAL
# ==========================================
@staff_member_required 
def ver_dashboard_kpis(request):
    context = admin.site.each_context(request)
    hoy = timezone.now()
    
    # 1. Ventas del Mes
    ventas_mes = Cotizacion.objects.filter(
        estado__in=['CONFIRMADA', 'ACEPTADA'],
        fecha_evento__year=hoy.year,
        fecha_evento__month=hoy.month
    ).aggregate(total=Sum('precio_final'))['total'] or 0

    # 2. Gastos del Mes
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
# 2. GENERAR LISTA DE COMPRAS (GLOBAL Y POR EVENTO)
# ==========================================
@staff_member_required
def generar_lista_compras(request):
    # Nota: Esta función es para el reporte global por fechas
    # La hemos simplificado para mantener compatibilidad, pero lo ideal es usar la lógica nueva
    # si quisieras reporte masivo. Por ahora lo dejamos funcional con la lógica básica.
    if request.method == 'POST':
        fecha_inicio = request.POST.get('fecha_inicio')
        fecha_fin = request.POST.get('fecha_fin')

        eventos = Cotizacion.objects.filter(
            estado='CONFIRMADA',
            fecha_evento__gte=fecha_inicio,
            fecha_evento__lte=fecha_fin
        )

        # Aquí podríamos implementar una lógica agregada compleja, 
        # pero por simplicidad dejaremos la versión básica que suma items guardados.
        compras = {}
        # ... (Lógica de suma de items guardados si existen) ...
        # Para la versión "Checklist" usamos la función nueva abajo.
        
        return render(request, 'comercial/reporte_form.html', {'titulo': 'Reporte Masivo en Construcción'})

    return render(request, 'comercial/reporte_form.html', {'titulo': 'Generar Lista de Compras'})

# --- NUEVO: LISTA DE COMPRAS ESPECÍFICA POR COTIZACIÓN (CHECKLIST) ---
@staff_member_required
def descargar_lista_compras_pdf(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    
    # Generamos la lista basada en la lógica de checkbox
    lista_insumos = generar_lista_compras_barra(cotizacion)
    
    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"

    html_string = render_to_string('pdf_lista_compras.html', {
        'cotizacion': cotizacion,
        'lista': lista_insumos,
        'logo_url': logo_url,
        'fecha_impresion': timezone.now()
    })
    
    html = HTML(string=html_string, base_url=request.build_absolute_uri())
    result = html.write_pdf()
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename=Checklist_Barra_{cotizacion.id}.pdf'
    response.write(result)
    return response


# ==========================================
# 3. COTIZACIONES (PDF Y EMAIL)
# ==========================================
def obtener_contexto_cotizacion(cotizacion):
    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"
    datos_barra = cotizacion.calcular_barra_insumos()
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
# 4. CALENDARIO
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

# ==========================================
# 5. EXPORTAR EXCEL (CONTABILIDAD)
# ==========================================
@staff_member_required
def exportar_cierre_excel(request):
    if not (request.user.is_superuser or request.user.groups.filter(name='Gerencia').exists()):
        return redirect('/admin/')
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

# ==========================================
# 6. REPORTE FINANCIERO (ESTADO DE RESULTADOS)
# ==========================================
@staff_member_required
def exportar_reporte_cotizaciones(request):
    if request.method != 'POST':
        return render(request, 'comercial/reporte_form.html')

    fecha_inicio = request.POST.get('fecha_inicio')
    fecha_fin = request.POST.get('fecha_fin')
    estado = request.POST.get('estado')
    
    cotizaciones = Cotizacion.objects.all().select_related('cliente').order_by('fecha_evento')
    
    if fecha_inicio: cotizaciones = cotizaciones.filter(fecha_evento__gte=fecha_inicio)
    if fecha_fin: cotizaciones = cotizaciones.filter(fecha_evento__lte=fecha_fin)
    if estado and estado != 'TODAS': cotizaciones = cotizaciones.filter(estado=estado)

    # --- INICIALIZACIÓN CON DECIMAL PARA EVITAR ERRORES DE PUNTO FLOTANTE ---
    t_subtotal = Decimal(0)
    t_descuento = Decimal(0)
    t_base_real = Decimal(0)
    t_total_ventas = Decimal(0)
    t_iva_trasladado = Decimal(0)
    t_ret_isr = Decimal(0)
    
    # Gastos Eventos
    t_gastos_ev_fiscal_base = Decimal(0)
    t_gastos_ev_fiscal_iva = Decimal(0)
    t_gastos_ev_nofiscal = Decimal(0)

    # Gastos Operativos
    t_gastos_op_fiscal_base = Decimal(0)
    t_gastos_op_fiscal_iva = Decimal(0)
    t_gastos_op_nofiscal = Decimal(0)
    
    datos_tabla = []
    
    # 1. PROCESAR EVENTOS
    for c in cotizaciones:
        base_real_venta = c.subtotal - c.descuento
        
        # Sumas globales
        t_subtotal += c.subtotal
        t_descuento += c.descuento
        t_base_real += base_real_venta
        t_iva_trasladado += c.iva
        t_ret_isr += c.retencion_isr
        t_total_ventas += c.precio_final
        
        # Gastos del evento (UUID vs Notas)
        ev_fiscal_base = Decimal(0)
        ev_fiscal_iva = Decimal(0)
        ev_nofiscal = Decimal(0)
        
        gastos_evento = c.gasto_set.all().select_related('compra')
        
        for g in gastos_evento:
            total_linea = g.total_linea or Decimal(0)
            compra = g.compra
            
            # Lógica: Si tiene UUID es Fiscal, si no, es Nota (Manual)
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
            'folio': c.id, 
            'fecha': c.fecha_evento, 
            'cliente': c.cliente.nombre,
            'producto': c.nombre_evento, 
            'base_real_venta': base_real_venta,
            'iva_trasladado': c.iva,
            'venta_total': c.precio_final,
            'gasto_fiscal_base': ev_fiscal_base,
            'gasto_nofiscal': ev_nofiscal,
            'iva_acreditable': ev_fiscal_iva,
            'utilidad': utilidad_bruta
        })

    # 2. PROCESAR GASTOS OPERATIVOS
    gastos_qs = Gasto.objects.filter(evento_relacionado__isnull=True).select_related('compra')
    if fecha_inicio: gastos_qs = gastos_qs.filter(fecha_gasto__gte=fecha_inicio)
    if fecha_fin: gastos_qs = gastos_qs.filter(fecha_gasto__lte=fecha_fin)

    ops_fiscales = []
    ops_nofiscales = []
    
    for g in gastos_qs:
        total_linea = g.total_linea or Decimal(0)
        compra = g.compra
        
        if compra.uuid: # FISCAL
            if compra.total > 0 and compra.iva > 0:
                factor = total_linea / compra.total
                iva_prop = factor * compra.iva
                base_prop = total_linea - iva_prop
            else:
                iva_prop = Decimal(0)
                base_prop = total_linea
            
            t_gastos_op_fiscal_base += base_prop
            t_gastos_op_fiscal_iva += iva_prop
            
            # Agrupar
            found = False
            for item in ops_fiscales:
                if item['cat'] == g.categoria:
                    item['base'] += base_prop
                    item['iva'] += iva_prop
                    item['total'] += total_linea
                    found = True
                    break
            if not found:
                ops_fiscales.append({'cat': g.categoria, 'base': base_prop, 'iva': iva_prop, 'total': total_linea})
                
        else: # NO FISCAL (Manual)
            t_gastos_op_nofiscal += total_linea
            
            found = False
            for item in ops_nofiscales:
                if item['cat'] == g.categoria:
                    item['total'] += total_linea
                    found = True
                    break
            if not found:
                ops_nofiscales.append({'cat': g.categoria, 'total': total_linea})

    cat_labels = dict(Gasto.CATEGORIAS)
    
    gastos_operativos_fiscales_list = []
    for item in ops_fiscales:
        gastos_operativos_fiscales_list.append({
            'nombre': cat_labels.get(item['cat'], item['cat']),
            'base': item['base'], 'iva': item['iva'], 'total': item['total']
        })
        
    gastos_operativos_nofiscales_list = []
    for item in ops_nofiscales:
        gastos_operativos_nofiscales_list.append({
            'nombre': cat_labels.get(item['cat'], item['cat']),
            'total': item['total']
        })

    # 3. RESULTADOS FINALES
    total_costos_deducibles = t_gastos_ev_fiscal_base + t_gastos_op_fiscal_base
    total_costos_no_deducibles = t_gastos_ev_nofiscal + t_gastos_op_nofiscal
    utilidad_neta_real = t_base_real - total_costos_deducibles - total_costos_no_deducibles
    
    total_iva_acreditable = t_gastos_ev_fiscal_iva + t_gastos_op_fiscal_iva
    iva_por_pagar = t_iva_trasladado - total_iva_acreditable

    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"
    
    context = {
        'datos': datos_tabla, 'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin, 'estado_filtro': estado,
        'logo_url': logo_url,
        
        # Totales
        't_base_real': t_base_real,
        't_iva_trasladado': t_iva_trasladado,
        't_venta_total': t_total_ventas,
        
        't_ev_fiscal_base': t_gastos_ev_fiscal_base,
        't_ev_nofiscal': t_gastos_ev_nofiscal,
        't_ev_iva': t_gastos_ev_fiscal_iva,
        
        't_op_fiscal_base': t_gastos_op_fiscal_base,
        't_op_nofiscal': t_gastos_op_nofiscal,
        't_op_iva': t_gastos_op_fiscal_iva,
        
        'gastos_operativos_fiscales_list': gastos_operativos_fiscales_list,
        'gastos_operativos_nofiscales_list': gastos_operativos_nofiscales_list,
        
        'total_costos_base': total_costos_deducibles,
        'total_costos_nofiscal': total_costos_no_deducibles,
        'utilidad_neta_real': utilidad_neta_real,
        
        'total_iva_acreditable': total_iva_acreditable,
        'iva_por_pagar': iva_por_pagar
    }
    
    html = render_to_string('cotizaciones/pdf_reporte_ventas.html', context)
    response = HttpResponse(content_type='application/pdf')
    filename = f"Estado_Resultados_{fecha_inicio if fecha_inicio else 'General'}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    HTML(string=html).write_pdf(response)
    return response

# ==========================================
# 7. REPORTE DE PAGOS
# ==========================================
@staff_member_required
def exportar_reporte_pagos(request):
    if request.method != 'POST':
        return render(request, 'comercial/reporte_form.html', {'titulo': 'Generar Reporte Detallado de Pagos'})
    fecha_inicio = request.POST.get('fecha_inicio')
    fecha_fin = request.POST.get('fecha_fin')
    pagos = Pago.objects.select_related('cotizacion', 'cotizacion__cliente', 'usuario').order_by('fecha_pago')
    if fecha_inicio: pagos = pagos.filter(fecha_pago__gte=fecha_inicio)
    if fecha_fin: pagos = pagos.filter(fecha_pago__lte=fecha_fin)
    total_ingresos = pagos.aggregate(Sum('monto'))['monto__sum'] or Decimal(0)
    metodos_data = pagos.values('metodo').annotate(total=Sum('monto')).order_by('-total')
    resumen_metodos = []
    dict_metodos = dict(Pago.METODOS)
    for item in metodos_data:
        clave = item['metodo']
        nombre = dict_metodos.get(clave, clave)
        resumen_metodos.append({'nombre': nombre, 'total': item['total'], 'porcentaje': (item['total'] / total_ingresos * 100) if total_ingresos > 0 else 0})
    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"
    context = {'pagos': pagos, 'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin, 'total_ingresos': total_ingresos, 'resumen_metodos': resumen_metodos, 'logo_url': logo_url, 'generado_el': timezone.now()}
    html = render_to_string('comercial/pdf_reporte_pagos.html', context)
    response = HttpResponse(content_type='application/pdf')
    filename = f"Reporte_Pagos_{fecha_inicio if fecha_inicio else 'Historico'}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    HTML(string=html).write_pdf(response)
    return response

# ==========================================
# 8. EXTRAS
# ==========================================
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
    if not request.user.is_superuser: return HttpResponse("⛔ Acceso denegado.")
    try:
        call_command('migrate', interactive=False)
        return HttpResponse("✅ ¡MIGRACIÓN EXITOSA!")
    except Exception as e: return HttpResponse(f"❌ Error: {str(e)}")