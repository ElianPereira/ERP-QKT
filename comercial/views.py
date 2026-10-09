import logging
import math
import re
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.core.mail import EmailMultiAlternatives
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags
from django.views.decorators.csrf import csrf_exempt

from core_erp import impuestos
from core_erp.documentos import nombre_archivo, render_pdf, respuesta_pdf
from core_erp.excel import respuesta_excel

from .models import (
    Cliente,
    Compra,
    Cotizacion,
    Insumo,
    ItemCotizacion,
    MovimientoInventario,
    Pago,
    PlantillaBarra,
    Producto,
)
from .services import CalculadoraBarraService, actualizar_item_cotizacion

try:
    from facturacion.models import SolicitudFactura
except ImportError:
    SolicitudFactura = None

logger = logging.getLogger(__name__)

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
            'costo_unitario': insumo.costo_unitario,
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
                'proveedor': insumo.proveedor.nombre if insumo.proveedor else PROVEEDOR_SIN_ASIGNAR,
                'costo_unitario': insumo.costo_unitario,
                'proporcion': 1.0,
                'insumo_id': insumo.id,
                '_via_fallback': True,
            }

    return None


PROVEEDOR_SIN_ASIGNAR = 'Sin proveedor asignado'
PROVEEDOR_POR_CONFIGURAR = 'Configurar en Plantilla de Barra'


def _fallback_item(nombre_generico):
    """Devuelve un item con datos genéricos cuando no hay plantilla configurada."""
    return {
        'nombre': nombre_generico,
        'proveedor': PROVEEDOR_SIN_ASIGNAR,
        'costo_unitario': Decimal('0'),
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
        entry['proveedor_pendiente'] = proveedor in (PROVEEDOR_SIN_ASIGNAR, PROVEEDOR_POR_CONFIGURAR)
    if costo_unitario > 0:
        entry['costo_unitario'] = costo_unitario
        entry['costo_total'] = (costo_unitario * cantidad).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    lista[seccion].append(entry)


def generar_lista_compras_barra(cotizacion):
    calc = CalculadoraBarraService(cotizacion)
    datos = calc.calcular()
    if not datos:
        return {}

    lista_compras = {}

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
                    _agregar_a_lista(lista_compras, 'Licores y Alcohol', fallback_nombre, cant, 'Botellas', proveedor=PROVEEDOR_POR_CONFIGURAR)

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
                    _agregar_a_lista(lista_compras, 'Licores y Alcohol', fallback_nombre, cant, 'Botellas', proveedor=PROVEEDOR_POR_CONFIGURAR)

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
                    _agregar_a_lista(lista_compras, 'Bebidas y Mezcladores', fallback_nombre, cant, 'Botellas', proveedor=PROVEEDOR_POR_CONFIGURAR)

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

    return lista_compras


# ==========================================
# 0.5 ASISTENTE DE CONFIGURACIÓN DE PLANTILLA DE BARRA
# ==========================================

@staff_member_required
@permission_required('comercial.change_plantillabarra', raise_exception=True)
def configurar_plantilla_barra(request):
    """Vista de asistente visual para vincular insumos reales a cada concepto de la Plantilla de Barra."""
    from django.contrib import admin as django_admin

    GRUPO_CONFIG = {
        'ALCOHOL_NACIONAL': {'nombre': 'Licores Nacionales',
                             'categorias': ['TEQUILA_NAC', 'WHISKY_NAC', 'RON_NAC', 'VODKA_NAC']},
        'ALCOHOL_PREMIUM': {'nombre': 'Licores Premium',
                            'categorias': ['TEQUILA_PREM', 'WHISKY_PREM', 'GIN_PREM']},
        'CERVEZA': {'nombre': 'Cerveza',
                    'categorias': ['CERVEZA']},
        'MEZCLADOR': {'nombre': 'Bebidas y Mezcladores',
                      'categorias': ['REFRESCO_COLA', 'REFRESCO_TORONJA', 'AGUA_MINERAL', 'AGUA_NATURAL']},
        'HIELO': {'nombre': 'Hielo',
                  'categorias': ['HIELO']},
        'COCTELERIA': {'nombre': 'Frutas y Verduras (Coctelería)',
                       'categorias': ['LIMON', 'HIERBABUENA', 'JARABE', 'FRUTOS_ROJOS', 'CAFE']},
        'CONSUMIBLE': {'nombre': 'Abarrotes y Consumibles',
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

                except (Insumo.DoesNotExist, ValueError):
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
    # Fecha local (America/Merida), no UTC: con timezone.now() el "mes actual"
    # saltaba al mes siguiente a partir de las 18:00 del último día del mes.
    hoy = timezone.localdate()

    # --- Elián · Quinta Ko'ox Tanil (Eventos) ---
    ventas_mes_quinta = Cotizacion.objects.filter(estado__in=ESTADOS_VENTA_REAL, fecha_evento__year=hoy.year, fecha_evento__month=hoy.month).aggregate(total=Sum('precio_final'))['total'] or 0
    gastos_mes_quinta = Compra.objects.filter(unidad_negocio__clave='QUINTA', fecha_emision__year=hoy.year, fecha_emision__month=hoy.month).aggregate(total=Sum('total'))['total'] or 0
    # Comisión de terminal (TPV): el banco la descuenta antes de depositar, así
    # que es un gasto financiero real aunque no venga de una Compra — se resta
    # del ingreso bruto igual que cualquier otro gasto para que la utilidad
    # refleje el margen neto real.
    comisiones_tpv_mes_quinta = Pago.objects.filter(fecha_pago__year=hoy.year, fecha_pago__month=hoy.month).aggregate(total=Sum('comision_tpv'))['total'] or 0
    gastos_mes_quinta += comisiones_tpv_mes_quinta
    utilidad_mes_quinta = ventas_mes_quinta - gastos_mes_quinta

    ventas_data_quinta = Cotizacion.objects.filter(estado__in=ESTADOS_VENTA_REAL, fecha_evento__year=hoy.year).annotate(mes=TruncMonth('fecha_evento')).values('mes').annotate(total=Sum('precio_final')).order_by('mes')
    gastos_data_quinta = Compra.objects.filter(unidad_negocio__clave='QUINTA', fecha_emision__year=hoy.year).annotate(mes=TruncMonth('fecha_emision')).values('mes').annotate(total=Sum('total')).order_by('mes')

    # Un solo eje de meses para ventas y gastos.
    chart_labels, series_grafica = _grafica_multi_series_ordenada([
        ('ventas_quinta', ventas_data_quinta),
        ('gastos_quinta', gastos_data_quinta),
    ])

    solicitudes_count = 0
    if SolicitudFactura:
        solicitudes_count = SolicitudFactura.objects.filter(fecha_solicitud__month=hoy.month).count()

    ultimos_eventos = Cotizacion.objects.filter(fecha_evento__gte=hoy, estado='CONFIRMADA').order_by('fecha_evento')[:5]

    context.update({
        'ventas_mes_quinta': ventas_mes_quinta, 'gastos_mes_quinta': gastos_mes_quinta, 'utilidad_mes_quinta': utilidad_mes_quinta,
        # Sin json.dumps: la plantilla los serializa con |json_script. Hoy solo
        # llevan meses y cifras agregadas, pero el patrón |safe sobre json.dumps
        # es el que abrió el XSS del calendario (ver comercial/views_calendario.py).
        'chart_labels': chart_labels,
        'chart_ventas_quinta': series_grafica['ventas_quinta'],
        'chart_gastos_quinta': series_grafica['gastos_quinta'],
        'solicitudes_count': solicitudes_count, 'ultimos_eventos': ultimos_eventos,
        'es_jefe': request.user.is_superuser or request.user.groups.filter(name='Gerencia').exists()
    })
    return render(request, 'admin/dashboard.html', context)

# ==========================================
# 2. REPORTES
# ==========================================
@staff_member_required
@permission_required('comercial.view_cotizacion', raise_exception=True)
def descargar_lista_compras_pdf(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    lista = generar_lista_compras_barra(cotizacion)
    total = sum((i.get('costo_total', Decimal('0')) for items in lista.values() for i in items), Decimal('0'))
    return respuesta_pdf('pdf_lista_compras.html', {
        'titulo': 'Lista de compras de barra',
        'cotizacion': cotizacion, 'lista': lista, 'total_estimado': total,
        'pendientes': sum(1 for items in lista.values() for i in items if i.get('proveedor_pendiente')),
    }, nombre_archivo('ListaCompras', f"COT-{cotizacion.id:03d}"), request=request)

# ==========================================
# 3. PDF Y EMAIL
# ==========================================
def obtener_contexto_cotizacion(cotizacion):
    calc = CalculadoraBarraService(cotizacion)
    datos_barra = calc.calcular()

    return {
        'titulo': f"Cotización de {cotizacion.get_tipo_servicio_display()}",
        'cotizacion': cotizacion, 'items': cotizacion.items.all(),
        'total_pagado': cotizacion.total_pagado(),
        'saldo_pendiente': cotizacion.saldo_pendiente(), 'barra': datos_barra
    }


def nombre_pdf_cotizacion(cotizacion):
    return nombre_archivo('Cotizacion', f"COT-{cotizacion.id:03d}")


def contexto_plan_pagos(cotizacion, plan):
    """Contexto del PDF del plan de pagos. Las condiciones salen de las fuentes
    vigentes (tabla de la Política de Cancelación y días de pago total), no de
    texto fijo en la plantilla, para no prometer algo distinto al contrato."""
    from . import reglas_contrato as rc

    dias = Cotizacion.DIAS_PAGO_TOTAL.get(cotizacion.tipo_servicio, Cotizacion.DIAS_PAGO_TOTAL['EVENTO'])
    tabla = 'HOSPEDAJE' if cotizacion.tipo_servicio == 'HOSPEDAJE' else 'SERVICIO'
    return {
        'titulo': 'Plan de pagos',
        'cotizacion': cotizacion,
        'plan': plan,
        'parcialidades': plan.parcialidades.all(),
        'dias_pago_total': dias,
        'fecha_pago_total': cotizacion.fecha_evento - timedelta(days=dias),
        'tabla_cancelacion': rc.TABLA_CANCELACION[tabla],
    }


@staff_member_required
@permission_required('comercial.view_cotizacion', raise_exception=True)
def generar_pdf_cotizacion(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    return respuesta_pdf(
        'cotizaciones/pdf_recibo.html', obtener_contexto_cotizacion(cotizacion),
        nombre_pdf_cotizacion(cotizacion), request=request,
    )

@staff_member_required
@permission_required('comercial.view_cotizacion', raise_exception=True)
def enviar_cotizacion_email(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    cliente = cotizacion.cliente
    if not cliente.email:
        messages.error(request, f"El cliente {cliente.nombre} no tiene email.")
        return redirect(request.META.get('HTTP_REFERER', '/admin/'))
    try:
        context = obtener_contexto_cotizacion(cotizacion)
        context['cliente'] = cliente
        pdf_file = render_pdf('cotizaciones/pdf_recibo.html', context)
        folio = f"COT-{cotizacion.id:03d}"
        filename = nombre_pdf_cotizacion(cotizacion)
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
    if not (request.user.is_superuser or request.user.groups.filter(name='Gerencia').exists()):
        return redirect('/admin/')
    hoy = timezone.localdate()
    pagos = Pago.objects.filter(
        fecha_pago__year=hoy.year, fecha_pago__month=hoy.month,
    ).select_related('cotizacion__cliente').order_by('fecha_pago')
    compras = Compra.objects.filter(
        fecha_emision__year=hoy.year, fecha_emision__month=hoy.month,
    ).order_by('fecha_emision')
    return respuesta_excel(f"Cierre {hoy:%m/%Y}", [
        ('Ingresos', ['Fecha', 'Cliente', 'Tipo', 'Método', 'Monto'],
         [(p.fecha_pago, p.cotizacion.cliente.nombre, p.get_tipo_display(), p.get_metodo_display(),
           -p.monto if p.tipo == 'REEMBOLSO' else p.monto) for p in pagos]),
        ('Gastos', ['Fecha', 'Proveedor', 'RFC emisor', 'Total factura'],
         [(c.fecha_emision, c.proveedor_display, c.rfc_emisor, c.total) for c in compras]),
    ], nombre_archivo('Cierre', f"{hoy:%Y-%m}", extension='xlsx'))

# ==========================================
# 5. FICHA TÉCNICA
# ==========================================
@staff_member_required
@permission_required('comercial.view_producto', raise_exception=True)
def descargar_ficha_producto(request, producto_id):
    producto = get_object_or_404(Producto, id=producto_id)
    if producto.es_paquete:
        incluye = [c.producto_hijo.nombre for c in producto.productos_incluidos.select_related('producto_hijo')]
    else:
        incluye = [c.subproducto.nombre for c in producto.componentes.select_related('subproducto')]
    return respuesta_pdf('comercial/pdf_ficha_producto.html', {
        'titulo': producto.nombre,
        'p': producto,
        'precio': impuestos.con_iva(Decimal(str(producto.sugerencia_precio()))),
        'descripcion': producto.descripcion_corta or producto.descripcion,
        'incluye': incluye,
        'imagen': request.build_absolute_uri(producto.imagen_promocional.url) if producto.imagen_promocional else '',
    }, nombre_archivo('Ficha', producto.nombre), request=request)

# ==========================================
# DASHBOARD CxC (CARTERA DE CLIENTES)
# ==========================================

@staff_member_required
@permission_required('comercial.view_pago', raise_exception=True)
def ver_cartera_cxc(request):
    """Dashboard de Cuentas por Cobrar."""
    from django.db.models import Case, CharField, DecimalField, F, Value, When
    from django.db.models.functions import Coalesce

    context = admin.site.each_context(request)
    hoy = timezone.now().date()

    from django.db.models import Sum
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
@permission_required('comercial.add_planpago', raise_exception=True)
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
@permission_required('comercial.view_planpago', raise_exception=True)
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

    return respuesta_pdf(
        'cotizaciones/pdf_plan_pagos.html', contexto_plan_pagos(cotizacion, plan),
        nombre_archivo('PlanPagos', f"COT-{cotizacion.id:03d}"), request=request,
    )


# ==========================================
# CONTRATO DE SERVICIO
# ==========================================
@staff_member_required
@permission_required('comercial.add_contratoservicio', raise_exception=True)
def generar_contrato(request, cotizacion_id):
    from .services import ContratoService, emitir_contrato

    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)

    if cotizacion.estado != 'CONFIRMADA':
        messages.error(request, " Solo se pueden generar contratos para cotizaciones CONFIRMADAS.")
        return redirect(request.META.get('HTTP_REFERER', '/admin/'))

    # El tipo sale de la cotización, nunca de la URL: así no puede emitirse
    # un contrato de Evento para una Pasadía.
    deposito = request.GET.get('deposito')
    deposito = Decimal(deposito) if deposito else None
    if cotizacion.tipo_servicio not in ContratoService.TIPOS:
        messages.error(request, " Tipo de contrato no disponible.")
        return redirect(request.META.get('HTTP_REFERER', '/admin/'))

    try:
        contrato, pdf_bytes = emitir_contrato(cotizacion, usuario=request.user, deposito=deposito)
        numero = contrato.numero
        filename = f"Contrato_{numero}.pdf"

        messages.success(request, f" Contrato {numero} generado correctamente.")

        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        return response

    except Exception as e:
        messages.error(request, f" Error al generar el contrato: {e}")
        return redirect(request.META.get('HTTP_REFERER', '/admin/'))


@staff_member_required
@permission_required('comercial.view_contratoservicio', raise_exception=True)
def vista_previa_contrato_propio(request, cotizacion_id):
    """PDF del contrato propio (Issue #318) con marca de agua, para revisarlo
    con datos reales antes de activarlo. No guarda nada: no crea
    ContratoServicio ni toca el archivo de la cotización."""
    from .services import ContratoService

    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    if cotizacion.tipo_servicio not in ContratoService.TIPOS:
        messages.error(request, " Tipo de contrato no disponible.")
        return redirect(request.META.get('HTTP_REFERER', '/admin/'))

    pdf_bytes, numero = ContratoService(cotizacion, vista_previa=True).generar()
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="Contrato_{numero}.pdf"'
    return response


@staff_member_required
@permission_required('comercial.view_contratoservicio', raise_exception=True)
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

    Operación destructiva de importación masiva: staff_member_required solo
    exige is_staff, así que se exige is_superuser también en el GET (antes
    solo el POST lo comprobaba, dejando la vista previa del historial
    abierta a cualquier staff).
    """
    if not request.user.is_superuser:
        raise PermissionDenied

    from io import StringIO

    from comercial.management.commands.importar_historico import (
        CLIENTES,
        COTIZACIONES,
        PAGOS,
        Command,
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


# Copiar es más caro que comprobar: por archivo son dos consultas al bucket
# más una descarga y una subida. Gunicorn corta la petición a los 120 s (ver
# Dockerfile), así que el tope se recorta antes para que la página alcance a
# renderizar el resumen en vez de morir con un 502.
MIGRACION_TIEMPO_MAXIMO = 90
MIGRACION_LIMITE = 15


@staff_member_required
def migrar_archivos_privados_view(request):
    """Copia desde el navegador los documentos sensibles al bucket privado.

    Misma lógica que `manage.py migrar_archivos_privados`, para cuando no hay
    una terminal con las variables de producción a mano. GET solo explica; hay
    que elegir explícitamente simular o copiar.
    """
    from comercial.services_migracion_privada import (
        CAMPOS_PRIVADOS,
        MigracionError,
        migrar_archivos_privados,
    )

    # staff_member_required solo comprueba is_staff; esto mueve nómina,
    # contratos e identificaciones de ARCO entre buckets.
    if not request.user.is_superuser:
        messages.error(request, "Solo un superusuario puede migrar archivos.")
        return redirect("/admin/")

    context = {
        "title": "Migrar documentos sensibles al bucket privado",
        "n_campos": len(CAMPOS_PRIVADOS),
        "limite": MIGRACION_LIMITE,
        "tiempo_maximo": MIGRACION_TIEMPO_MAXIMO,
        "resultado": None,
        "aplicado": False,
        "error": None,
    }

    if request.method != "POST":
        return render(request, "admin/migrar_archivos_privados.html", context)

    aplicar = request.POST.get("accion") == "aplicar"
    context["aplicado"] = aplicar

    try:
        context["resultado"] = migrar_archivos_privados(
            aplicar=aplicar,
            limite=MIGRACION_LIMITE if aplicar else None,
            tiempo_maximo=MIGRACION_TIEMPO_MAXIMO,
        )
    except MigracionError as exc:
        context["error"] = str(exc)
    except Exception as exc:
        logger.exception("Fallo inesperado migrando archivos al bucket privado")
        context["error"] = f"Fallo inesperado: {exc}"

    return render(request, "admin/migrar_archivos_privados.html", context)
