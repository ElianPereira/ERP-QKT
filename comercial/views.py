import json
import math
import os
import re
import openpyxl 
from .models import MovimientoInventario
from datetime import datetime, timedelta
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
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
from django.views.decorators.csrf import csrf_exempt
from django.core.files.base import ContentFile

from .models import Cotizacion, Gasto, Pago, ItemCotizacion, Compra, Producto, Cliente, PlantillaBarra, Insumo
from .services import CalculadoraBarraService, actualizar_item_cotizacion

try:
    from facturacion.models import SolicitudFactura
except ImportError:
    SolicitudFactura = None

try:
    from airbnb.models import PagoAirbnb
except ImportError:
    PagoAirbnb = None

# Estados que representan una venta ya concretada (no un simple borrador o
# cotización enviada, y tampoco cancelada). El flujo normal de una venta
# exitosa avanza CONFIRMADA -> EJECUTADA -> CERRADA; los tres deben contar
# como "venta real" en KPIs y reportes financieros — de lo contrario, un
# evento que ya se realizó y se cobró al 100% deja de contar en cuanto
# avanza más allá de "Confirmada", penalizando justo a las ventas más
# completas.
ESTADOS_VENTA_REAL = ['CONFIRMADA', 'EJECUTADA', 'CERRADA']


# ==========================================
# 0. LÓGICA DE LISTA DE COMPRAS (REFACTORIZADO)
# ==========================================

def _buscar_insumo_palabra_completa(keyword):
    """
    Busca un insumo donde el keyword sea una PALABRA COMPLETA,
    no parte de otra palabra.
    Evita que 'ron' matchee 'Toronja' o 'gin' matchee 'Original'.
    """
    insumo = Insumo.objects.filter(
        nombre__istartswith=keyword,
        categoria='CONSUMIBLE'
    ).first()
    if insumo:
        return insumo
    
    insumo = Insumo.objects.filter(
        nombre__icontains=f' {keyword}',
        categoria='CONSUMIBLE'
    ).first()
    if insumo:
        return insumo
    
    return None


def _obtener_item_plantilla(categoria):
    plantilla = PlantillaBarra.objects.filter(
        categoria=categoria, activo=True
    ).select_related('insumo').first()
    
    if plantilla and plantilla.insumo:
        insumo = plantilla.insumo
        nombre = insumo.nombre
        if insumo.presentacion:
            nombre = f"{insumo.nombre} ({insumo.presentacion})"
        return {
            'nombre': nombre,
            'proveedor': insumo.proveedor.nombre if insumo.proveedor else '',
            'costo_unitario': float(insumo.costo_unitario),
            'proporcion': float(plantilla.proporcion),
            'insumo_id': insumo.id,
        }
    
    BUSQUEDA_KEYWORDS = {
        'CERVEZA': ['cerveza', 'caguama', 'tecate', 'corona'],
        'TEQUILA_NAC': ['tequila cuervo', 'tequila tradicional', 'tequila'],
        'WHISKY_NAC': ['whisky', 'whiskey'],
        'RON_NAC': ['ron bacardi', 'ron castillo', 'ron havana', 'ron '],
        'VODKA_NAC': ['vodka'],
        'TEQUILA_PREM': ['don julio', 'herradura', 'tequila 1800'],
        'WHISKY_PREM': ['buchanan', 'jack daniel', 'johnnie walker black', 'etiqueta negra'],
        'GIN_PREM': ['ginebra', 'hendrick', 'tanqueray', 'bombay'],
        'REFRESCO_COLA': ['coca cola', 'coca-cola'],
        'REFRESCO_TORONJA': ['toronja', 'squirt', 'fresca'],
        'AGUA_MINERAL': ['agua mineral', 'topochico', 'topo chico', 'peñafiel mineral'],
        'AGUA_NATURAL': ['garrafon', 'garrafón', 'agua natural', 'agua purificada'],
        'HIELO': ['hielo'],
        'LIMON': ['limon', 'limón'],
        'HIERBABUENA': ['hierbabuena', 'menta'],
        'JARABE': ['jarabe'],
        'FRUTOS_ROJOS': ['frutos rojos', 'berries', 'zarzamora', 'frambuesa'],
        'CAFE': ['café', 'cafe', 'espresso'],
        'SERVILLETAS': ['servilleta', 'popote'],
    }
    
    keywords = BUSQUEDA_KEYWORDS.get(categoria, [])
    
    for keyword in keywords:
        if ' ' in keyword:
            insumo = Insumo.objects.filter(
                nombre__icontains=keyword,
                categoria='CONSUMIBLE'
            ).first()
        elif len(keyword) <= 4:
            insumo = _buscar_insumo_palabra_completa(keyword)
        else:
            insumo = Insumo.objects.filter(
                nombre__icontains=keyword,
                categoria='CONSUMIBLE'
            ).first()
        
        if insumo:
            nombre = insumo.nombre
            if insumo.presentacion:
                nombre = f"{insumo.nombre} ({insumo.presentacion})"
            return {
                'nombre': nombre,
                'proveedor': insumo.proveedor.nombre if insumo.proveedor else ' Sin proveedor',
                'costo_unitario': float(insumo.costo_unitario),
                'proporcion': 1.0,
                'insumo_id': insumo.id,
                '_via_fallback': True,
            }
    
    return None


def _fallback_item(nombre_generico):
    """Devuelve un item con datos genéricos cuando no hay plantilla configurada."""
    return {
        'nombre': nombre_generico,
        'proveedor': ' Sin asignar',
        'costo_unitario': 0,
        'proporcion': 1.0,
        'insumo_id': None,
    }


def _agregar_a_lista(lista, seccion, item_nombre, cantidad, unidad, nota='', proveedor='', costo_unitario=0):
    """Helper para agregar un ítem a la lista de compras con formato consistente."""
    if seccion not in lista:
        lista[seccion] = []
    
    entry = {
        'item': item_nombre,
        'cantidad': cantidad,
        'unidad': unidad,
    }
    if nota:
        entry['nota'] = nota
    if proveedor:
        entry['proveedor'] = proveedor
    if costo_unitario > 0:
        entry['costo_unitario'] = costo_unitario
        entry['costo_total'] = round(costo_unitario * cantidad, 2)
    
    lista[seccion].append(entry)


def generar_lista_compras_barra(cotizacion):
    calc = CalculadoraBarraService(cotizacion)
    datos = calc.calcular()
    if not datos: 
        return {}

    lista_compras = {}
    costo_total_lista = Decimal('0.00')

    if datos['cervezas_unidades'] > 0:
        p = _obtener_item_plantilla('CERVEZA') or _fallback_item('Cerveza Nacional (Caguama)')
        cajas = math.ceil(datos['cervezas_unidades'] / 12.0)
        _agregar_a_lista(lista_compras, 'Licores y Alcohol', p['nombre'], cajas, 'Cajas (12u)', proveedor=p['proveedor'], costo_unitario=p['costo_unitario'])

    if datos['botellas_nacional'] > 0:
        b = datos['botellas_nacional']
        mapeo_nacional = [
            ('TEQUILA_NAC', 'Tequila Nacional', 0.40),
            ('WHISKY_NAC', 'Whisky Nacional', 0.30),
            ('RON_NAC', 'Ron Nacional', 0.20),
            ('VODKA_NAC', 'Vodka Nacional', 0.10),
        ]
        for cat, fallback_nombre, default_prop in mapeo_nacional:
            p = _obtener_item_plantilla(cat)
            if p:
                prop = p['proporcion']
                cant = math.ceil(b * prop)
                if cant > 0:
                    _agregar_a_lista(lista_compras, 'Licores y Alcohol', p['nombre'], cant, 'Botellas', proveedor=p['proveedor'], costo_unitario=p['costo_unitario'])
            else:
                cant = math.ceil(b * default_prop)
                if cant > 0:
                    _agregar_a_lista(lista_compras, 'Licores y Alcohol', fallback_nombre, cant, 'Botellas', proveedor=' Configurar en Plantilla de Barra')

    if datos['botellas_premium'] > 0:
        b = datos['botellas_premium']
        mapeo_premium = [
            ('TEQUILA_PREM', 'Tequila Premium', 0.40),
            ('WHISKY_PREM', 'Whisky Premium', 0.30),
            ('GIN_PREM', 'Ginebra / Ron Premium', 0.30),
        ]
        for cat, fallback_nombre, default_prop in mapeo_premium:
            p = _obtener_item_plantilla(cat)
            if p:
                prop = p['proporcion']
                cant = math.ceil(b * prop)
                if cant > 0:
                    _agregar_a_lista(lista_compras, 'Licores y Alcohol', p['nombre'], cant, 'Botellas', proveedor=p['proveedor'], costo_unitario=p['costo_unitario'])
            else:
                cant = math.ceil(b * default_prop)
                if cant > 0:
                    _agregar_a_lista(lista_compras, 'Licores y Alcohol', fallback_nombre, cant, 'Botellas', proveedor=' Configurar en Plantilla de Barra')

    if l := datos['litros_mezcladores']:
        mapeo_mezcladores = [
            ('REFRESCO_COLA', 'Coca-Cola (2.5L)', 0.60, 2.5),
            ('REFRESCO_TORONJA', 'Refresco Toronja (2L)', 0.20, 2.0),
            ('AGUA_MINERAL', 'Agua Mineral (2L)', 0.20, 2.0),
        ]
        for cat, fallback_nombre, share, litros_envase in mapeo_mezcladores:
            p = _obtener_item_plantilla(cat)
            litros_necesarios = l * share
            cant = math.ceil(litros_necesarios / litros_envase)
            if cant > 0:
                if p:
                    _agregar_a_lista(lista_compras, 'Bebidas y Mezcladores', p['nombre'], cant, 'Botellas', proveedor=p['proveedor'], costo_unitario=p['costo_unitario'])
                else:
                    _agregar_a_lista(lista_compras, 'Bebidas y Mezcladores', fallback_nombre, cant, 'Botellas', proveedor=' Configurar en Plantilla de Barra')

    if datos['litros_agua'] > 0:
        p = _obtener_item_plantilla('AGUA_NATURAL') or _fallback_item('Agua Natural (Garrafón 20L)')
        cant = math.ceil(datos['litros_agua'] / 20)
        _agregar_a_lista(lista_compras, 'Bebidas y Mezcladores', p['nombre'], cant, 'Garrafones', proveedor=p['proveedor'], costo_unitario=p['costo_unitario'])

    p = _obtener_item_plantilla('HIELO') or _fallback_item('Hielo (Bolsa 20kg)')
    _agregar_a_lista(lista_compras, 'Abarrotes y Consumibles', p['nombre'], datos['bolsas_hielo_20kg'], 'Bolsas', nota=datos['hielo_info'], proveedor=p['proveedor'], costo_unitario=p['costo_unitario'])

    if cotizacion.incluye_cocteleria_basica:
        for cat, fallback, cant_calc, unidad in [
            ('LIMON', 'Limón Persa', math.ceil(cotizacion.num_personas / 8), 'Kg'),
            ('HIERBABUENA', 'Hierbabuena', math.ceil(cotizacion.num_personas / 15), 'Manojos'),
            ('JARABE', 'Jarabe Natural', math.ceil(cotizacion.num_personas / 40), 'Litros'),
        ]:
            p = _obtener_item_plantilla(cat) or _fallback_item(fallback)
            seccion = 'Frutas y Verduras' if cat in ('LIMON', 'HIERBABUENA') else 'Abarrotes y Consumibles'
            _agregar_a_lista(lista_compras, seccion, p['nombre'], cant_calc, unidad, proveedor=p['proveedor'], costo_unitario=p['costo_unitario'])

    if cotizacion.incluye_cocteleria_premium:
        for cat, fallback, cant_calc, unidad in [
            ('FRUTOS_ROJOS', 'Frutos Rojos', math.ceil(cotizacion.num_personas / 20), 'Bolsas'),
            ('CAFE', 'Café Espresso', 1, 'Kg'),
        ]:
            p = _obtener_item_plantilla(cat) or _fallback_item(fallback)
            seccion = 'Frutas y Verduras' if cat == 'FRUTOS_ROJOS' else 'Abarrotes y Consumibles'
            _agregar_a_lista(lista_compras, seccion, p['nombre'], cant_calc, unidad, proveedor=p['proveedor'], costo_unitario=p['costo_unitario'])

    p = _obtener_item_plantilla('SERVILLETAS') or _fallback_item('Servilletas / Popotes')
    _agregar_a_lista(lista_compras, 'Abarrotes y Consumibles', p['nombre'], 1, 'Kit', proveedor=p['proveedor'], costo_unitario=p['costo_unitario'])

    for seccion, items in lista_compras.items():
        for item in items:
            if 'costo_total' not in item and item.get('costo_unitario', 0) > 0:
                item['costo_total'] = round(item['costo_unitario'] * item['cantidad'], 2)

    return lista_compras


# ==========================================
# 0.5 ASISTENTE DE CONFIGURACIÓN DE PLANTILLA DE BARRA
# ==========================================

@staff_member_required
def configurar_plantilla_barra(request):
    """Vista de asistente visual para vincular insumos reales a cada concepto de la Plantilla de Barra."""
    from django.contrib import admin as django_admin
    
    GRUPO_CONFIG = {
        'ALCOHOL_NACIONAL': {'nombre': 'Licores Nacionales', 'color': '#e67e22', 'icono': '',
                             'categorias': ['TEQUILA_NAC', 'WHISKY_NAC', 'RON_NAC', 'VODKA_NAC']},
        'ALCOHOL_PREMIUM': {'nombre': 'Licores Premium', 'color': '#9b59b6', 'icono': '',
                            'categorias': ['TEQUILA_PREM', 'WHISKY_PREM', 'GIN_PREM']},
        'CERVEZA': {'nombre': 'Cerveza', 'color': '#f39c12', 'icono': '',
                    'categorias': ['CERVEZA']},
        'MEZCLADOR': {'nombre': 'Bebidas y Mezcladores', 'color': '#3498db', 'icono': '',
                      'categorias': ['REFRESCO_COLA', 'REFRESCO_TORONJA', 'AGUA_MINERAL', 'AGUA_NATURAL']},
        'HIELO': {'nombre': 'Hielo', 'color': '#1abc9c', 'icono': '',
                  'categorias': ['HIELO']},
        'COCTELERIA': {'nombre': 'Frutas y Verduras (Coctelería)', 'color': '#27ae60', 'icono': '',
                       'categorias': ['LIMON', 'HIERBABUENA', 'JARABE', 'FRUTOS_ROJOS', 'CAFE']},
        'CONSUMIBLE': {'nombre': 'Abarrotes y Consumibles', 'color': '#95a5a6', 'icono': '',
                       'categorias': ['SERVILLETAS']},
    }
    
    cat_labels = dict(PlantillaBarra.CATEGORIAS_BARRA)
    insumos = Insumo.objects.all().order_by('nombre')
    mensaje_exito = None
    mensaje_error = None
    
    if request.method == 'POST':
        creados = 0
        actualizados = 0
        errores = 0
        
        for cat_key, cat_label in PlantillaBarra.CATEGORIAS_BARRA:
            insumo_id = request.POST.get(f'insumo_{cat_key}', '')
            proporcion_pct = request.POST.get(f'proporcion_{cat_key}', '100')
            
            try:
                proporcion_pct = int(proporcion_pct)
            except (ValueError, TypeError):
                proporcion_pct = 100
            
            proporcion_decimal = Decimal(proporcion_pct) / Decimal('100')
            
            grupo = 'CONSUMIBLE'
            for g_key, g_conf in GRUPO_CONFIG.items():
                if cat_key in g_conf['categorias']:
                    grupo = g_key
                    break
            
            if insumo_id:
                try:
                    insumo = Insumo.objects.get(id=int(insumo_id))
                    plantilla = PlantillaBarra.objects.filter(categoria=cat_key).first()
                    
                    if plantilla:
                        plantilla.insumo = insumo
                        plantilla.proporcion = proporcion_decimal
                        plantilla.grupo = grupo
                        plantilla.activo = True
                        plantilla.save()
                        actualizados += 1
                    else:
                        PlantillaBarra.objects.create(
                            categoria=cat_key, grupo=grupo, insumo=insumo,
                            proporcion=proporcion_decimal, activo=True, orden=0
                        )
                        creados += 1
                        
                except (Insumo.DoesNotExist, ValueError) as e:
                    errores += 1
            else:
                PlantillaBarra.objects.filter(categoria=cat_key).update(activo=False)
        
        if errores == 0:
            mensaje_exito = f"Plantilla guardada: {creados} nuevos, {actualizados} actualizados."
        else:
            mensaje_error = f"Guardado con {errores} errores. {creados} nuevos, {actualizados} actualizados."
    
    grupos = []
    vinculados = 0
    sin_vincular = 0
    
    for g_key, g_conf in GRUPO_CONFIG.items():
        categorias_grupo = []
        
        for cat_key in g_conf['categorias']:
            cat_label = cat_labels.get(cat_key, cat_key)
            plantilla = PlantillaBarra.objects.filter(
                categoria=cat_key, activo=True
            ).select_related('insumo').first()
            
            insumo_actual = plantilla.insumo if plantilla else None
            proporcion_pct = int(plantilla.proporcion * 100) if plantilla else 100
            
            if insumo_actual:
                vinculados += 1
            else:
                sin_vincular += 1
            
            categorias_grupo.append({
                'key': cat_key,
                'label': cat_label,
                'insumo_actual': insumo_actual,
                'proporcion_pct': proporcion_pct,
            })
        
        grupos.append({
            'key': g_key,
            'nombre': g_conf['nombre'],
            'color': g_conf['color'],
            'icono': g_conf['icono'],
            'categorias': categorias_grupo,
        })
    
    context = {
        **django_admin.site.each_context(request),
        'grupos': grupos,
        'insumos': insumos,
        'vinculados': vinculados,
        'sin_vincular': sin_vincular,
        'total_insumos': insumos.count(),
        'mensaje_exito': mensaje_exito,
        'mensaje_error': mensaje_error,
    }
    
    return render(request, 'admin/comercial/configurar_plantilla_barra.html', context)


def _grafica_multi_series_ordenada(series):
    """Alinea N series ya agrupadas por mes (.values('mes').annotate(total=...))
    sobre un único eje de meses (la unión de los meses de todas las series),
    ordenado por la fecha real (no por orden de inserción) — un mes que solo
    aparece en una serie debe seguir en su lugar cronológico y valer 0 en las
    demás, en vez de quedar fuera de orden o faltarle datos.

    `series` es una lista de tuplas (nombre, queryset). Devuelve
    (labels_ordenados, {nombre: [valores alineados a labels_ordenados]})."""
    valores_por_serie = {}
    meses = set()
    for nombre, queryset in series:
        por_mes = {}
        for fila in queryset:
            if fila['mes']:
                por_mes[fila['mes']] = float(fila['total'])
                meses.add(fila['mes'])
        valores_por_serie[nombre] = por_mes

    meses_ordenados = sorted(meses)
    labels = [mes.strftime('%B %Y') for mes in meses_ordenados]
    resultado = {
        nombre: [por_mes.get(mes, 0) for mes in meses_ordenados]
        for nombre, por_mes in valores_por_serie.items()
    }
    return labels, resultado


# ==========================================
# 1. DASHBOARD
# ==========================================
@staff_member_required
def ver_dashboard_kpis(request):
    context = admin.site.each_context(request)
    hoy = timezone.now()

    # --- Elián · Quinta Ko'ox Tanil (Eventos) ---
    ventas_mes_quinta = Cotizacion.objects.filter(estado__in=ESTADOS_VENTA_REAL, fecha_evento__year=hoy.year, fecha_evento__month=hoy.month).aggregate(total=Sum('precio_final'))['total'] or 0
    gastos_mes_quinta = Compra.objects.filter(unidad_negocio__clave='QUINTA', fecha_emision__year=hoy.year, fecha_emision__month=hoy.month).aggregate(total=Sum('total'))['total'] or 0
    utilidad_mes_quinta = ventas_mes_quinta - gastos_mes_quinta

    ventas_data_quinta = Cotizacion.objects.filter(estado__in=ESTADOS_VENTA_REAL, fecha_evento__year=hoy.year).annotate(mes=TruncMonth('fecha_evento')).values('mes').annotate(total=Sum('precio_final')).order_by('mes')
    gastos_data_quinta = Compra.objects.filter(unidad_negocio__clave='QUINTA', fecha_emision__year=hoy.year).annotate(mes=TruncMonth('fecha_emision')).values('mes').annotate(total=Sum('total')).order_by('mes')

    # --- Ruby · Hospedaje Airbnb ---
    ingresos_mes_ruby = 0
    gastos_mes_ruby = 0
    ingresos_data_ruby = []
    if PagoAirbnb:
        ingresos_mes_ruby = PagoAirbnb.objects.filter(estado='PAGADO', fecha_pago__year=hoy.year, fecha_pago__month=hoy.month).aggregate(total=Sum('monto_neto'))['total'] or 0
        gastos_mes_ruby = Compra.objects.filter(unidad_negocio__clave='AIRBNB', fecha_emision__year=hoy.year, fecha_emision__month=hoy.month).aggregate(total=Sum('total'))['total'] or 0
        ingresos_data_ruby = PagoAirbnb.objects.filter(estado='PAGADO', fecha_pago__year=hoy.year).annotate(mes=TruncMonth('fecha_pago')).values('mes').annotate(total=Sum('monto_neto')).order_by('mes')
    gastos_data_ruby = Compra.objects.filter(unidad_negocio__clave='AIRBNB', fecha_emision__year=hoy.year).annotate(mes=TruncMonth('fecha_emision')).values('mes').annotate(total=Sum('total')).order_by('mes')
    utilidad_mes_ruby = ingresos_mes_ruby - gastos_mes_ruby

    # Un solo eje de meses para las 4 series (ambas líneas de negocio en la
    # misma gráfica, con sus importes bien diferenciados por color/leyenda).
    chart_labels, series_grafica = _grafica_multi_series_ordenada([
        ('ventas_quinta', ventas_data_quinta),
        ('gastos_quinta', gastos_data_quinta),
        ('ingresos_ruby', ingresos_data_ruby),
        ('gastos_ruby', gastos_data_ruby),
    ])

    solicitudes_count = 0
    if SolicitudFactura:
        solicitudes_count = SolicitudFactura.objects.filter(fecha_solicitud__month=hoy.month).count()

    ultimos_eventos = Cotizacion.objects.filter(fecha_evento__gte=hoy.date(), estado='CONFIRMADA').order_by('fecha_evento')[:5]

    context.update({
        'ventas_mes_quinta': ventas_mes_quinta, 'gastos_mes_quinta': gastos_mes_quinta, 'utilidad_mes_quinta': utilidad_mes_quinta,
        'ingresos_mes_ruby': ingresos_mes_ruby, 'gastos_mes_ruby': gastos_mes_ruby, 'utilidad_mes_ruby': utilidad_mes_ruby,
        'chart_labels': json.dumps(chart_labels),
        'chart_ventas_quinta': json.dumps(series_grafica['ventas_quinta']),
        'chart_gastos_quinta': json.dumps(series_grafica['gastos_quinta']),
        'chart_ingresos_ruby': json.dumps(series_grafica['ingresos_ruby']),
        'chart_gastos_ruby': json.dumps(series_grafica['gastos_ruby']),
        'solicitudes_count': solicitudes_count, 'ultimos_eventos': ultimos_eventos,
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
    
    calc = CalculadoraBarraService(cotizacion)
    datos_barra = calc.calcular()
    
    return {
        'cotizacion': cotizacion, 'items': cotizacion.items.all(), 
        'logo_url': logo_url, 'total_pagado': cotizacion.total_pagado(),
        'saldo_pendiente': cotizacion.saldo_pendiente(), 'barra': datos_barra
    }

@staff_member_required
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

@staff_member_required
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
        messages.success(request, f" Enviado a {cliente.email}")
    except Exception as e:
        messages.error(request, f" Error: {e}")
    return redirect(request.META.get('HTTP_REFERER', '/admin/'))

# ==========================================
# 4. EXPORTS
# ==========================================
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
    
    cotizaciones = Cotizacion.objects.all().select_related('cliente').prefetch_related('gasto_set__compra').order_by('fecha_evento')
    
    if fecha_inicio: cotizaciones = cotizaciones.filter(fecha_evento__gte=fecha_inicio)
    if fecha_fin: cotizaciones = cotizaciones.filter(fecha_evento__lte=fecha_fin)
    if estado and estado != 'TODAS': cotizaciones = cotizaciones.filter(estado=estado)

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

# ==========================================
# 5. FICHA TÉCNICA
# ==========================================
@staff_member_required
def descargar_ficha_producto(request, producto_id):
    producto = get_object_or_404(Producto, id=producto_id)
    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    if os.name == 'nt':
        logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}"
    else:
        logo_url = f"file://{ruta_logo}"
    img_prod_url = ""
    if producto.imagen_promocional:
        img_prod_url = request.build_absolute_uri(producto.imagen_promocional.url)
    context = {
        'p': producto, 'logo_url': logo_url,
        'img_prod_url': img_prod_url, 'fecha_impresion': timezone.now()
    }
    html_string = render_to_string('comercial/pdf_ficha_producto.html', context)
    response = HttpResponse(content_type='application/pdf')
    filename = f"Ficha_{producto.nombre.replace(' ','_')}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    HTML(string=html_string).write_pdf(response)
    return response

# ==========================================
# DASHBOARD CxC (CARTERA DE CLIENTES)
# ==========================================

@staff_member_required
def ver_cartera_cxc(request):
    """Dashboard de Cuentas por Cobrar."""
    from django.db.models import F, Case, When, Value, CharField, DecimalField
    from django.db.models.functions import Coalesce
    
    context = admin.site.each_context(request)
    hoy = timezone.now().date()
    
    from django.db.models import Sum, Q
    cotizaciones = Cotizacion.objects.filter(
        estado__in=['COTIZADA', 'CONFIRMADA', 'EJECUTADA']
    ).select_related('cliente').annotate(
        _ingresos=Coalesce(Sum('pagos__monto', filter=Q(pagos__tipo='INGRESO')), Decimal('0.00')),
        _reembolsos=Coalesce(Sum('pagos__monto', filter=Q(pagos__tipo='REEMBOLSO')), Decimal('0.00')),
    ).order_by('fecha_evento')

    cartera = []
    total_por_cobrar = Decimal('0.00')
    total_vencido = Decimal('0.00')
    total_por_vencer = Decimal('0.00')
    al_dia = 0
    vence_7_dias = 0
    vence_30_dias = 0
    vencido = 0

    for cot in cotizaciones:
        total_pagado = cot._ingresos - cot._reembolsos
        saldo = cot.precio_final - total_pagado
        if saldo <= Decimal('0.50'):
            continue

        total_por_cobrar += saldo
        dias_evento = (cot.fecha_evento - hoy).days
        
        if dias_evento < 0:
            antiguedad = 'VENCIDO'
            vencido += 1
            total_vencido += saldo
        elif dias_evento <= 7:
            antiguedad = 'URGENTE'
            vence_7_dias += 1
            total_por_vencer += saldo
        elif dias_evento <= 30:
            antiguedad = 'PROXIMO'
            vence_30_dias += 1
            total_por_vencer += saldo
        else:
            antiguedad = 'AL_DIA'
            al_dia += 1
        
        cartera.append({
            'cotizacion': cot,
            'folio': f"COT-{cot.id:03d}",
            'cliente': cot.cliente.nombre,
            'evento': cot.nombre_evento,
            'fecha_evento': cot.fecha_evento,
            'precio_final': cot.precio_final,
            'total_pagado': total_pagado,
            'saldo': saldo,
            'porcentaje_pagado': round((total_pagado / cot.precio_final) * 100, 1) if cot.precio_final > 0 else Decimal('0.0'),
            'dias_evento': dias_evento,
            'antiguedad': antiguedad,
            'telefono': cot.cliente.telefono,
            'email': cot.cliente.email,
        })
    
    orden_prioridad = {'VENCIDO': 0, 'URGENTE': 1, 'PROXIMO': 2, 'AL_DIA': 3}
    cartera.sort(key=lambda x: (orden_prioridad.get(x['antiguedad'], 4), x['fecha_evento']))
    
    context.update({
        'cartera': cartera,
        'total_por_cobrar': total_por_cobrar,
        'total_vencido': total_vencido,
        'total_por_vencer': total_por_vencer,
        'count_total': len(cartera),
        'count_vencido': vencido,
        'count_urgente': vence_7_dias,
        'count_proximo': vence_30_dias,
        'count_al_dia': al_dia,
    })
    
    return render(request, 'admin/comercial/cartera_cxc.html', context)


# ==========================================
# PLAN DE PAGOS
# ==========================================

@staff_member_required
def generar_plan_pagos(request, cotizacion_id):
    """
    Genera un plan de pagos para una cotización.
    Acepta ?parcialidades=N para personalizar el número de parcialidades.
    """
    from .models import PlanPago
    from .services import PlanPagosService
    
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    
    if cotizacion.precio_final <= 0:
        messages.error(request, "La cotización no tiene precio calculado. Agrega items primero.")
        return redirect(request.META.get('HTTP_REFERER', '/admin/'))
    
    num_parcialidades = request.GET.get('parcialidades')
    if num_parcialidades:
        try:
            num_parcialidades = int(num_parcialidades)
            if num_parcialidades < 1 or num_parcialidades > 12:
                num_parcialidades = None
                messages.warning(request, "Número de parcialidades debe ser entre 1 y 12. Se usó el default.")
        except ValueError:
            num_parcialidades = None
    
    try:
        servicio = PlanPagosService(cotizacion)
        plan = servicio.generar(usuario=request.user, num_parcialidades=num_parcialidades)
        n = plan.parcialidades.count()
        messages.success(request, f"Plan de {n} pagos generado para COT-{cotizacion.id:03d}")
    except Exception as e:
        messages.error(request, f"Error al generar plan: {e}")
    
    return redirect(request.META.get('HTTP_REFERER', f'/admin/comercial/cotizacion/{cotizacion_id}/change/'))


@staff_member_required
def descargar_plan_pagos_pdf(request, cotizacion_id):
    """Genera PDF del plan de pagos."""
    from .models import PlanPago
    
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    
    try:
        plan = cotizacion.plan_pago
    except PlanPago.DoesNotExist:
        messages.error(request, "Esta cotización no tiene plan de pagos.")
        return redirect(request.META.get('HTTP_REFERER', '/admin/'))
    
    if not plan.activo:
        messages.error(request, "El plan de pagos está inactivo.")
        return redirect(request.META.get('HTTP_REFERER', '/admin/'))
    
    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"
    
    context = {
        'cotizacion': cotizacion,
        'plan': plan,
        'parcialidades': plan.parcialidades.all(),
        'logo_url': logo_url,
        'fecha_generacion': timezone.now(),
    }
    
    html_string = render_to_string('cotizaciones/pdf_plan_pagos.html', context)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="Plan_Pagos_COT-{cotizacion.id:03d}.pdf"'
    HTML(string=html_string).write_pdf(response)
    return response


# ==========================================
# CONTRATO DE SERVICIO
# ==========================================
@staff_member_required
def generar_contrato(request, cotizacion_id):
    from .models import ContratoServicio
    from .services import ContratoService

    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)

    if cotizacion.estado != 'CONFIRMADA':
        messages.error(request, " Solo se pueden generar contratos para cotizaciones CONFIRMADAS.")
        return redirect(request.META.get('HTTP_REFERER', '/admin/'))

    tipo     = request.GET.get('tipo_servicio', 'EVENTO')
    deposito = Decimal(request.GET.get('deposito', '0') or '0')

    try:
        servicio  = ContratoService(cotizacion, tipo_servicio=tipo, deposito=deposito)
        pdf_bytes, numero = servicio.generar()

        filename = f"Contrato_{numero}.pdf"

        contrato = ContratoServicio(
            cotizacion=cotizacion,
            numero=numero,
            tipo_servicio=tipo,
            deposito_garantia=deposito,
            generado_por=request.user,
        )
        contrato.archivo.save(filename, ContentFile(pdf_bytes), save=False)
        contrato.save()

        cotizacion.archivo_contrato.save(filename, ContentFile(pdf_bytes), save=False)
        Cotizacion.objects.filter(pk=cotizacion.pk).update(
            archivo_contrato=cotizacion.archivo_contrato.name
        )

        messages.success(request, f" Contrato {numero} generado correctamente.")

        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        return response

    except Exception as e:
        messages.error(request, f" Error al generar el contrato: {e}")
        return redirect(request.META.get('HTTP_REFERER', '/admin/'))


@staff_member_required
def enviar_contrato_email(request, contrato_id):
    """Envía el contrato por email al cliente."""
    from .models import ContratoServicio

    contrato   = get_object_or_404(ContratoServicio, id=contrato_id)
    cotizacion = contrato.cotizacion
    cliente    = cotizacion.cliente

    if not cliente.email:
        messages.error(request, f" El cliente {cliente.nombre} no tiene email registrado.")
        return redirect(request.META.get('HTTP_REFERER', '/admin/'))

    try:
        contrato.archivo.open('rb')
        docx_bytes = contrato.archivo.read()
        contrato.archivo.close()

        html_email = render_to_string('emails/contrato.html', {
            'cliente':    cliente,
            'cotizacion': cotizacion,
            'contrato':   contrato,
            'folio':      f"COT-{cotizacion.id:03d}",
        })

        msg = EmailMultiAlternatives(
            subject=f"Contrato de Servicio {contrato.numero} — Quinta Ko'ox Tanil",
            body=strip_tags(html_email),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[cliente.email],
        )
        msg.attach_alternative(html_email, "text/html")
        msg.attach(f"{contrato.numero}.docx", docx_bytes,
                   'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        msg.send()

        ContratoServicio.objects.filter(pk=contrato.pk).update(enviado_email=True)
        messages.success(request, f" Contrato enviado a {cliente.email}")

    except Exception as e:
        messages.error(request, f" Error al enviar el contrato: {e}")

    return redirect(request.META.get('HTTP_REFERER', '/admin/'))


# ---------------------------------------------------------------------------
# IMPORTACIÓN HISTÓRICA (una sola vez, desde el sistema anterior)
# ---------------------------------------------------------------------------

@staff_member_required
def importar_historico_view(request):
    """
    Página de administración para importar el historial del sistema anterior.
    GET  → muestra resumen y botón de confirmación.
    POST → ejecuta la importación y muestra resultados.
    """
    from io import StringIO
    from comercial.management.commands.importar_historico import (
        CLIENTES, COTIZACIONES, PAGOS, Command,
    )

    pagos_por_cot = {}
    for row in PAGOS:
        pagos_por_cot.setdefault(row[0], []).append(row)

    resumen_preview = []
    for cot_id, cliente_clave, fecha, tipo, total, estado in COTIZACIONES:
        n_pagos = len(pagos_por_cot.get(cot_id, []))
        resumen_preview.append({
            "cot_id": cot_id,
            "fecha": fecha,
            "tipo": tipo,
            "total": total,
            "estado": estado,
            "n_pagos": n_pagos,
        })

    context = {
        "title": "Importar Historial del Sistema Anterior",
        "n_clientes": len(CLIENTES),
        "n_cotizaciones": len(COTIZACIONES),
        "n_pagos": len(PAGOS),
        "preview": resumen_preview,
        "resultado": None,
    }

    if request.method == "POST":
        if not request.user.is_superuser:
            messages.error(request, "Solo un superusuario puede ejecutar esta importación.")
            return redirect("/admin/")

        out = StringIO()
        cmd = Command(stdout=out, no_color=True)
        try:
            cmd.handle(dry_run=False)
            context["resultado"] = out.getvalue()
            context["resultado_ok"] = True
        except Exception as exc:
            context["resultado"] = f"Error durante la importación:\n{exc}"
            context["resultado_ok"] = False

    return render(request, "admin/importar_historico.html", context)