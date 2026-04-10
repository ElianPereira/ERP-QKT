import json
import math
import os
import re
import openpyxl 
from .models import MovimientoInventario
from datetime import datetime, timedelta
from openpyxl.styles import Font, PatternFill
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
from django.core.management import call_command
from django.views.decorators.csrf import csrf_exempt
from core_erp.ratelimit import rate_limit as _rate_limit
from decouple import config
import hmac
import hashlib
from django.core.files.base import ContentFile

from .models import Cotizacion, Gasto, Pago, ItemCotizacion, Compra, Producto, Cliente, PlantillaBarra, Insumo, GrupoBarra, CategoriaBarra
from .forms import CalculadoraForm
from .services import CalculadoraBarraService, actualizar_item_cotizacion

try:
    from facturacion.models import SolicitudFactura
except ImportError:
    SolicitudFactura = None 


# ==========================================
# 0. LÓGICA DE LISTA DE COMPRAS (REFACTORIZADO)
# ==========================================

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


def _obtener_plantillas_categoria(categoria_clave):
    """Obtiene las PlantillaBarra activas para una categoría (por clave)."""
    return PlantillaBarra.objects.filter(
        categoria_ref__clave=categoria_clave,
        categoria_ref__activo=True,
        activo=True,
    ).select_related('insumo', 'insumo__proveedor', 'categoria_ref').order_by('orden')


def _nombre_insumo(insumo):
    """Nombre formateado del insumo incluyendo presentación."""
    if insumo.presentacion:
        return f"{insumo.nombre} ({insumo.presentacion})"
    return insumo.nombre


def _generar_items_grupo(lista_compras, grupo_clave, cantidad_total, seccion_nombre):
    """Genera items de lista de compras para todas las categorías activas de un grupo."""
    categorias = CategoriaBarra.objects.filter(
        grupo__clave=grupo_clave,
        activo=True,
    ).order_by('orden')

    for cat in categorias:
        plantillas = list(_obtener_plantillas_categoria(cat.clave))
        if not plantillas:
            # Fallback: mostrar categoría sin insumo vinculado
            cant = math.ceil(cantidad_total * float(cat.proporcion_default))
            if cant > 0:
                _agregar_a_lista(lista_compras, seccion_nombre, cat.nombre, cant,
                    cat.unidad_compra, proveedor='Configurar en Plantilla de Barra')
            continue

        for p in plantillas:
            prop = float(cat.proporcion_default) * float(p.proporcion)
            if cat.unidad_contenido and float(cat.unidad_contenido) > 1:
                # Para mezcladores: convertir litros necesarios a unidades de envase
                litros_necesarios = cantidad_total * prop
                cant = math.ceil(litros_necesarios / float(cat.unidad_contenido))
            else:
                cant = math.ceil(cantidad_total * prop)
            if cant <= 0:
                continue
            insumo = p.insumo
            _agregar_a_lista(
                lista_compras, seccion_nombre, _nombre_insumo(insumo), cant,
                cat.unidad_compra,
                proveedor=insumo.proveedor.nombre if insumo.proveedor else '',
                costo_unitario=float(insumo.costo_unitario),
            )


def _generar_items_cocteleria(lista_compras, cotizacion, grupo_clave, seccion_nombre):
    """Genera items de coctelería con cálculos por persona."""
    CALCULO_POR_PERSONA = {
        'LIMON': lambda n: math.ceil(n / 8),
        'HIERBABUENA': lambda n: math.ceil(n / 15),
        'JARABE': lambda n: math.ceil(n / 40),
        'FRUTOS_ROJOS': lambda n: math.ceil(n / 20),
        'CAFE': lambda n: 1,
    }
    categorias = CategoriaBarra.objects.filter(
        grupo__clave=grupo_clave,
        activo=True,
    ).order_by('orden')

    for cat in categorias:
        calc_fn = CALCULO_POR_PERSONA.get(cat.clave)
        if not calc_fn:
            continue
        cant = calc_fn(cotizacion.num_personas)
        if cant <= 0:
            continue
        plantillas = list(_obtener_plantillas_categoria(cat.clave))
        if plantillas:
            for p in plantillas:
                insumo = p.insumo
                _agregar_a_lista(
                    lista_compras, seccion_nombre, _nombre_insumo(insumo),
                    cant, cat.unidad_compra,
                    proveedor=insumo.proveedor.nombre if insumo.proveedor else '',
                    costo_unitario=float(insumo.costo_unitario),
                )
        else:
            _agregar_a_lista(lista_compras, seccion_nombre, cat.nombre,
                cant, cat.unidad_compra, proveedor='Configurar en Plantilla de Barra')


def generar_lista_compras_barra(cotizacion):
    """Genera lista de compras data-driven desde GrupoBarra/CategoriaBarra/PlantillaBarra."""
    calc = CalculadoraBarraService(cotizacion)
    datos = calc.calcular()
    if not datos:
        return {}

    lista_compras = {}

    # Cerveza
    if datos['cervezas_unidades'] > 0:
        cajas = math.ceil(datos['cervezas_unidades'] / 12.0)
        _generar_items_grupo(lista_compras, 'CERVEZA', cajas, 'Licores y Alcohol')

    # Licores Nacionales
    if datos['botellas_nacional'] > 0:
        _generar_items_grupo(lista_compras, 'ALCOHOL_NACIONAL', datos['botellas_nacional'], 'Licores y Alcohol')

    # Licores Premium
    if datos['botellas_premium'] > 0:
        _generar_items_grupo(lista_compras, 'ALCOHOL_PREMIUM', datos['botellas_premium'], 'Licores y Alcohol')

    # Mezcladores (sin AGUA_NATURAL, que va aparte)
    if datos['litros_mezcladores'] > 0:
        categorias_mixer = CategoriaBarra.objects.filter(
            grupo__clave='MEZCLADOR', activo=True
        ).exclude(clave='AGUA_NATURAL').order_by('orden')
        for cat in categorias_mixer:
            plantillas = list(_obtener_plantillas_categoria(cat.clave))
            litros_necesarios = datos['litros_mezcladores'] * float(cat.proporcion_default)
            contenido = float(cat.unidad_contenido) if cat.unidad_contenido and float(cat.unidad_contenido) > 0 else 1.0
            cant = math.ceil(litros_necesarios / contenido)
            if cant <= 0:
                continue
            if plantillas:
                for p in plantillas:
                    insumo = p.insumo
                    _agregar_a_lista(lista_compras, 'Bebidas y Mezcladores',
                        _nombre_insumo(insumo), cant, cat.unidad_compra,
                        proveedor=insumo.proveedor.nombre if insumo.proveedor else '',
                        costo_unitario=float(insumo.costo_unitario))
            else:
                _agregar_a_lista(lista_compras, 'Bebidas y Mezcladores',
                    cat.nombre, cant, cat.unidad_compra,
                    proveedor='Configurar en Plantilla de Barra')

    # Agua Natural
    if datos['litros_agua'] > 0:
        plantillas_agua = list(_obtener_plantillas_categoria('AGUA_NATURAL'))
        cant = math.ceil(datos['litros_agua'] / 20)
        if plantillas_agua:
            insumo = plantillas_agua[0].insumo
            _agregar_a_lista(lista_compras, 'Bebidas y Mezcladores',
                _nombre_insumo(insumo), cant, 'Garrafones',
                proveedor=insumo.proveedor.nombre if insumo.proveedor else '',
                costo_unitario=float(insumo.costo_unitario))
        else:
            _agregar_a_lista(lista_compras, 'Bebidas y Mezcladores',
                'Agua Natural (Garrafón 20L)', cant, 'Garrafones')

    # Hielo
    plantillas_hielo = list(_obtener_plantillas_categoria('HIELO'))
    if plantillas_hielo:
        insumo = plantillas_hielo[0].insumo
        _agregar_a_lista(lista_compras, 'Abarrotes y Consumibles',
            _nombre_insumo(insumo), datos['bolsas_hielo_20kg'], 'Bolsas',
            nota=datos['hielo_info'],
            proveedor=insumo.proveedor.nombre if insumo.proveedor else '',
            costo_unitario=float(insumo.costo_unitario))
    else:
        _agregar_a_lista(lista_compras, 'Abarrotes y Consumibles',
            'Hielo (Bolsa 20kg)', datos['bolsas_hielo_20kg'], 'Bolsas',
            nota=datos['hielo_info'])

    # Coctelería Básica (ingredientes)
    if cotizacion.incluye_cocteleria_basica:
        _generar_items_cocteleria(lista_compras, cotizacion, 'COCTELERIA_BASICA', 'Frutas y Verduras')

    # Coctelería Premium (ingredientes)
    if cotizacion.incluye_cocteleria_premium:
        _generar_items_cocteleria(lista_compras, cotizacion, 'COCTELERIA_PREMIUM', 'Frutas y Verduras')

    # Consumibles
    plantillas_serv = list(_obtener_plantillas_categoria('SERVILLETAS'))
    if plantillas_serv:
        insumo = plantillas_serv[0].insumo
        _agregar_a_lista(lista_compras, 'Abarrotes y Consumibles',
            _nombre_insumo(insumo), 1, 'Kit',
            proveedor=insumo.proveedor.nombre if insumo.proveedor else '',
            costo_unitario=float(insumo.costo_unitario))
    else:
        _agregar_a_lista(lista_compras, 'Abarrotes y Consumibles',
            'Servilletas / Popotes', 1, 'Kit')

    # Calcular costos totales
    for seccion, items in lista_compras.items():
        for item in items:
            if 'costo_total' not in item and item.get('costo_unitario', 0) > 0:
                item['costo_total'] = round(item['costo_unitario'] * item['cantidad'], 2)

    return lista_compras


# ==========================================
# 0.5 ASISTENTE DE CONFIGURACIÓN DE PLANTILLA DE BARRA (DATA-DRIVEN)
# ==========================================

@staff_member_required
def configurar_plantilla_barra(request):
    """Vista de asistente visual para vincular insumos reales a cada concepto de la Plantilla de Barra."""
    from django.contrib import admin as django_admin

    insumos = Insumo.objects.all().order_by('nombre')
    mensaje_exito = None
    mensaje_error = None

    if request.method == 'POST':
        creados = 0
        actualizados = 0
        errores = 0

        for cat in CategoriaBarra.objects.select_related('grupo').all():
            insumo_id = request.POST.get(f'insumo_{cat.clave}', '')
            proporcion_pct = request.POST.get(f'proporcion_{cat.clave}', '100')
            cat_activo = request.POST.get(f'activo_{cat.clave}', '') == 'on'

            try:
                proporcion_pct = int(proporcion_pct)
            except (ValueError, TypeError):
                proporcion_pct = 100
            proporcion_decimal = Decimal(proporcion_pct) / Decimal('100')

            # Actualizar activo de CategoriaBarra
            if cat.activo != cat_activo:
                cat.activo = cat_activo
                cat.save(update_fields=['activo'])

            if insumo_id:
                try:
                    insumo = Insumo.objects.get(id=int(insumo_id))
                    plantilla = PlantillaBarra.objects.filter(
                        categoria_ref=cat
                    ).first()

                    if plantilla:
                        plantilla.insumo = insumo
                        plantilla.proporcion = proporcion_decimal
                        plantilla.categoria = cat.clave
                        plantilla.grupo = cat.grupo.clave if cat.grupo else ''
                        plantilla.activo = True
                        plantilla.save()
                        actualizados += 1
                    else:
                        PlantillaBarra.objects.create(
                            categoria_ref=cat,
                            categoria=cat.clave,
                            grupo=cat.grupo.clave if cat.grupo else '',
                            insumo=insumo,
                            proporcion=proporcion_decimal,
                            activo=True, es_default=True, orden=0
                        )
                        creados += 1

                except (Insumo.DoesNotExist, ValueError):
                    errores += 1
            else:
                PlantillaBarra.objects.filter(categoria_ref=cat).update(activo=False)

        if errores == 0:
            mensaje_exito = f"Plantilla guardada: {creados} nuevos, {actualizados} actualizados."
        else:
            mensaje_error = f"Guardado con {errores} errores. {creados} nuevos, {actualizados} actualizados."

    # Construir datos para el template desde BD
    grupos = []
    vinculados = 0
    sin_vincular = 0

    for grupo_obj in GrupoBarra.objects.filter(activo=True).order_by('orden'):
        categorias_grupo = []

        for cat in CategoriaBarra.objects.filter(grupo=grupo_obj).order_by('orden'):
            plantillas = PlantillaBarra.objects.filter(
                categoria_ref=cat, activo=True
            ).select_related('insumo').order_by('orden')

            insumo_actual = plantillas.first().insumo if plantillas.exists() else None
            proporcion_pct = int(plantillas.first().proporcion * 100) if plantillas.exists() else 100

            if insumo_actual:
                vinculados += 1
            else:
                sin_vincular += 1

            categorias_grupo.append({
                'key': cat.clave,
                'label': cat.nombre,
                'insumo_actual': insumo_actual,
                'proporcion_pct': proporcion_pct,
                'cat_activo': cat.activo,
                'plantillas': list(plantillas),
            })

        grupos.append({
            'key': grupo_obj.clave,
            'nombre': grupo_obj.nombre,
            'color': grupo_obj.color,
            'icono': '',
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
# 4. CALENDARIO Y EXPORTS
# ==========================================
@staff_member_required
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
    if not request.user.is_superuser: return HttpResponse(" Acceso denegado.")
    try:
        call_command('migrate', interactive=False)
        return HttpResponse(" ¡MIGRACIÓN EXITOSA!")
    except Exception as e: return HttpResponse(f" Error: {str(e)}")

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
# 6. INTEGRACIÓN MANYCHAT (WEBHOOK V7)
# ==========================================

def _verificar_token_webhook(request):
    """Valida el token secreto del webhook. Retorna True si es válido."""
    token_esperado = config('MANYCHAT_WEBHOOK_TOKEN', default='')
    if not token_esperado:
        return False
    token_recibido = request.headers.get('X-Webhook-Token', '')
    return hmac.compare_digest(token_recibido, token_esperado)


def _redondear_personas(num, es_pasadia=False):
    """Redondea al múltiplo de 10 hacia arriba. Ej: 91 → 100, 23 → 30"""
    if es_pasadia:
        return min(int(num), 20)
    return max(20, math.ceil(int(num) / 10) * 10)


def _buscar_producto_por_nombre(nombre_parcial):
    """Busca un producto por nombre parcial (case-insensitive)."""
    return Producto.objects.filter(nombre__icontains=nombre_parcial).first()


def _agregar_item_producto(cotizacion, producto, cantidad=1, descripcion_override=None):
    """Agrega un ItemCotizacion con un Producto, usando su precio sugerido."""
    if not producto:
        return None
    precio = producto.sugerencia_precio()
    desc = descripcion_override or producto.nombre
    return ItemCotizacion.objects.create(
        cotizacion=cotizacion,
        producto=producto,
        descripcion=desc,
        cantidad=Decimal(str(cantidad)),
        precio_unitario=Decimal(str(precio))
    )


def _detectar_clima_por_fecha(fecha):
    """
    Detecta automáticamente el clima según el mes del evento.
    - 'extremo': Mayo
    - 'calor':   Mar, Abr, Jun, Jul, Ago, Sep, Oct
    - 'normal':  Nov, Dic, Ene, Feb
    """
    if not fecha:
        return 'calor'
    mes = fecha.month
    if mes == 5:
        return 'extremo'
    elif mes in (3, 4, 6, 7, 8, 9, 10):
        return 'calor'
    else:
        return 'normal'


def _parsear_hora(hora_str):
    """
    Parsea una hora desde string. Soporta formatos:
    '14:00', '14:30', '14h', '2pm', '2:00pm', '14'
    Retorna un objeto time o None.
    """
    if not hora_str:
        return None
    hora_str = hora_str.strip().lower().replace(' ', '')

    try:
        return datetime.strptime(hora_str, "%H:%M").time()
    except ValueError:
        pass

    try:
        return datetime.strptime(hora_str, "%I:%M%p").time()
    except ValueError:
        pass

    try:
        return datetime.strptime(hora_str, "%I%p").time()
    except ValueError:
        pass

    try:
        return datetime.strptime(hora_str.replace('h', '').strip(), "%H").time()
    except ValueError:
        pass

    try:
        h = int(hora_str)
        if 0 <= h <= 23:
            from datetime import time as dt_time
            return dt_time(h, 0)
    except ValueError:
        pass

    return None


@csrf_exempt
@_rate_limit(key='webhook_manychat', limit=60, window=60)
def webhook_manychat(request):
    """
    Webhook V7: Procesa Evento y Pasadía.
    - Nombre de evento personalizado con fallback.
    - Crea PortalCliente y devuelve portal_url.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

    if not _verificar_token_webhook(request):
        return JsonResponse({'status': 'error', 'message': 'Token inválido'}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'message': 'JSON inválido'}, status=400)

    try:
        # =====================
        # 1. PARSEAR DATOS BASE
        # =====================
        telefono = data.get('telefono_cliente', '')
        nombre = data.get('nombre_cliente', '')
        email_cliente = str(data.get('email_cliente', '') or '').strip()
        tipo_servicio = data.get('tipo_servicio', 'Evento').strip()
        tipo_evento = data.get('tipo_evento', 'Evento General').strip()
        fecha_str = data.get('fecha_tentativa', '')

        es_pasadia = 'pasad' in tipo_servicio.lower()
        es_solo_arrendamiento = 'arrendamiento' in tipo_servicio.lower()

        num_raw = data.get('num_personas', '50')
        try:
            num_personas_raw = int(re.findall(r'\d+', str(num_raw))[0])
        except (IndexError, ValueError):
            num_personas_raw = 50 if not es_pasadia else 10

        num_personas = _redondear_personas(num_personas_raw, es_pasadia)

        hora_inicio_str = data.get('hora_inicio', '').strip()
        hora_fin_str = data.get('hora_fin', '').strip()
        hora_inicio_obj = _parsear_hora(hora_inicio_str)
        hora_fin_obj = _parsear_hora(hora_fin_str)

        if hora_inicio_obj and hora_fin_obj:
            from datetime import date as dt_date
            dt_inicio = datetime.combine(dt_date.today(), hora_inicio_obj)
            dt_fin = datetime.combine(dt_date.today(), hora_fin_obj)
            if dt_fin <= dt_inicio:
                dt_fin += timedelta(days=1)
            horas_evento = max(6, int((dt_fin - dt_inicio).total_seconds() / 3600))
        else:
            horas_evento = 6

        if es_pasadia:
            horas_evento = 9
            hora_inicio_obj = datetime.strptime("10:00", "%H:%M").time()
            hora_fin_obj = datetime.strptime("19:00", "%H:%M").time()

        try:
            fecha_evento = datetime.strptime(fecha_str.strip(), "%d/%m/%Y").date()
        except ValueError:
            fecha_evento = timezone.now().date() + timedelta(days=30)

        clima_auto = _detectar_clima_por_fecha(fecha_evento)

        # Verificar disponibilidad de la fecha
        aviso_fecha = None
        try:
            from airbnb.validacion_fechas import verificar_disponibilidad_fecha
            _disp, _msg = verificar_disponibilidad_fecha(fecha_evento)
            if not _disp:
                aviso_fecha = _msg
        except Exception:
            pass

        def _bool(val):
            if isinstance(val, bool):
                return val
            return str(val).lower().strip() in ('true', 'si', 'sí', '1', 'yes')

        # =====================
        # 2. CREAR/OBTENER CLIENTE
        # =====================
        if not telefono:
            return JsonResponse({'status': 'error', 'message': 'Teléfono requerido'}, status=400)

        from .services import get_or_create_cliente_desde_canal
        cliente, _ = get_or_create_cliente_desde_canal(
            telefono_raw=telefono,
            nombre_raw=nombre,
            origen='WhatsApp',
            email_raw=email_cliente,
        )
        telefono_limpio = cliente.telefono

        # =====================
        # 3. CREAR COTIZACIÓN
        # =====================
        resumen_partes = []

        # Nombre personalizado (aplica a ambos flujos)
        nombre_evento_custom = str(data.get('nombre_evento', '')).strip()

        if es_pasadia:
            # ===========================
            # FLUJO PASADÍA
            # ===========================
            nombre_ev_pasadia = nombre_evento_custom if nombre_evento_custom else f"Pasadía - {nombre}"

            cotizacion = Cotizacion(
                cliente=cliente,
                nombre_evento=nombre_ev_pasadia[:200],
                fecha_evento=fecha_evento,
                num_personas=num_personas,
                horas_servicio=horas_evento,
                hora_inicio=hora_inicio_obj,
                hora_fin=hora_fin_obj,
                estado='BORRADOR',
                clima=clima_auto,
                requiere_factura=True,
                incluye_refrescos=False,
                incluye_cerveza=False,
                incluye_licor_nacional=False,
                incluye_licor_premium=False,
                incluye_cocteleria_basica=False,
                incluye_cocteleria_premium=False,
            )
            cotizacion.save()

            prod = _buscar_producto_por_nombre('Pasadía') or _buscar_producto_por_nombre('Pasadia')
            if prod:
                _agregar_item_producto(cotizacion, prod, cantidad=1,
                    descripcion_override=f"Paquete Pasadía QKT ({num_personas} Pax, 10am-7pm)")
                resumen_partes.append("Pasadía")

            if _bool(data.get('pasadia_brincolin', False)):
                prod = _buscar_producto_por_nombre('Brincolín') or _buscar_producto_por_nombre('Brincolin')
                if prod:
                    _agregar_item_producto(cotizacion, prod, cantidad=1,
                        descripcion_override="Brincolín Inflable Tipo Castillo 4x4 Mts (6 Hrs)")
                    resumen_partes.append("Brincolín")

            if _bool(data.get('pasadia_bolis', False)):
                prod = _buscar_producto_por_nombre('Carrito Con Bolis')
                if prod:
                    _agregar_item_producto(cotizacion, prod, cantidad=1,
                        descripcion_override="Servicio de Carrito con Bolis (25 pzas)")
                    resumen_partes.append("Bolis")

            if _bool(data.get('pasadia_paletas', False)):
                prod = _buscar_producto_por_nombre('Carrito Con Paletas')
                if prod:
                    _agregar_item_producto(cotizacion, prod, cantidad=1,
                        descripcion_override="Servicio de Carrito con Paletas (25 pzas)")
                    resumen_partes.append("Paletas")

        else:
            # ===========================
            # FLUJO EVENTO
            # ===========================
            inc_cerveza = _bool(data.get('incluye_cerveza', False)) if not es_solo_arrendamiento else False
            inc_nacional = _bool(data.get('incluye_licor_nacional', False)) if not es_solo_arrendamiento else False
            inc_premium = _bool(data.get('incluye_licor_premium', False)) if not es_solo_arrendamiento else False
            inc_cocteleria = _bool(data.get('incluye_cocteleria_basica', False)) if not es_solo_arrendamiento else False
            inc_mixologia = _bool(data.get('incluye_mixologia', False)) if not es_solo_arrendamiento else False
            inc_refrescos = any([inc_cerveza, inc_nacional, inc_premium, inc_cocteleria, inc_mixologia])

            inc_dj_basico = _bool(data.get('incluye_dj_basico', False)) if not es_solo_arrendamiento else False
            inc_dj_iluminacion = _bool(data.get('incluye_dj_iluminacion', False)) if not es_solo_arrendamiento else False
            inc_catering = _bool(data.get('incluye_catering', False)) if not es_solo_arrendamiento else False
            inc_taquiza = _bool(data.get('incluye_taquiza', False)) if not es_solo_arrendamiento else False

            nombre_ev = nombre_evento_custom if nombre_evento_custom else f"{tipo_servicio} - {tipo_evento}"

            cotizacion = Cotizacion(
                cliente=cliente,
                nombre_evento=nombre_ev[:200],
                fecha_evento=fecha_evento,
                num_personas=num_personas,
                horas_servicio=horas_evento,
                hora_inicio=hora_inicio_obj,
                hora_fin=hora_fin_obj,
                estado='BORRADOR',
                clima=clima_auto,
                requiere_factura=True,
                incluye_refrescos=inc_refrescos,
                incluye_cerveza=inc_cerveza,
                incluye_licor_nacional=inc_nacional,
                incluye_licor_premium=inc_premium,
                incluye_cocteleria_basica=inc_cocteleria,
                incluye_cocteleria_premium=inc_mixologia,
            )
            cotizacion.save()

            prod = _buscar_producto_por_nombre('Paquete Esencial')
            if prod:
                if es_solo_arrendamiento:
                    desc = f"Paquete Esencial QKT - Arrendamiento ({num_personas} Pax, {horas_evento} Hrs)"
                    resumen_partes.append("Arrendamiento")
                else:
                    desc = f"Paquete Esencial QKT - {tipo_evento} ({num_personas} Pax, {horas_evento} Hrs)"
                    resumen_partes.append("Paquete Base")
                _agregar_item_producto(cotizacion, prod, cantidad=1, descripcion_override=desc)

            if not es_solo_arrendamiento:
                mob_principal = data.get('mobiliario_principal', '').strip()
                mob_lounge_tipo = data.get('mobiliario_lounge_tipo', '').strip()
                mob_lounge_cant_raw = data.get('mobiliario_lounge_cantidad', '0')
                mob_coctel_raw = data.get('mobiliario_coctel', '0')

                try:
                    mob_lounge_cant = max(0, int(re.findall(r'\d+', str(mob_lounge_cant_raw))[0]))
                except (IndexError, ValueError):
                    mob_lounge_cant = 0
                try:
                    mob_coctel_cant = max(0, int(re.findall(r'\d+', str(mob_coctel_raw))[0]))
                except (IndexError, ValueError):
                    mob_coctel_cant = 0

                if mob_principal:
                    prod = _buscar_producto_por_nombre(f'Mobiliario {mob_principal}')
                    if prod:
                        mult = math.ceil(num_personas / 10)
                        _agregar_item_producto(cotizacion, prod, cantidad=mult,
                            descripcion_override=f"Mobiliario {mob_principal} ({num_personas} Pax)")
                        resumen_partes.append(f"Mob.{mob_principal}")

                if mob_lounge_tipo and mob_lounge_tipo.lower() != 'ninguno' and mob_lounge_cant > 0:
                    prod = _buscar_producto_por_nombre(f'Mobiliario Set {mob_lounge_tipo}')
                    if prod:
                        _agregar_item_producto(cotizacion, prod, cantidad=mob_lounge_cant,
                            descripcion_override=f"{mob_lounge_tipo} ({mob_lounge_cant} sets)")
                        resumen_partes.append(f"Lounge x{mob_lounge_cant}")

                if mob_coctel_cant > 0:
                    prod = _buscar_producto_por_nombre('Mesa Cóctel') or _buscar_producto_por_nombre('Mesa Coctel')
                    if prod:
                        _agregar_item_producto(cotizacion, prod, cantidad=mob_coctel_cant,
                            descripcion_override=f"Mesas Cóctel ({mob_coctel_cant} unidades)")
                        resumen_partes.append(f"Cóctel x{mob_coctel_cant}")

            if inc_catering:
                prod = _buscar_producto_por_nombre('Catering')
                if prod:
                    mult = math.ceil(num_personas / 10)
                    _agregar_item_producto(cotizacion, prod, cantidad=mult,
                        descripcion_override=f"Servicio de Catering ({num_personas} Pax)")
                    resumen_partes.append("Catering")

            if inc_taquiza:
                prod = _buscar_producto_por_nombre('Taquiza')
                if prod:
                    mult = math.ceil(num_personas / 10)
                    _agregar_item_producto(cotizacion, prod, cantidad=mult,
                        descripcion_override=f"Servicio de Taquiza ({num_personas} Pax)")
                    resumen_partes.append("Taquiza")

            if inc_dj_iluminacion:
                prod = _buscar_producto_por_nombre('DJ Con Iluminación') or _buscar_producto_por_nombre('DJ Iluminacion')
                if prod:
                    _agregar_item_producto(cotizacion, prod, cantidad=1,
                        descripcion_override=f"Servicio de DJ con Iluminación - {horas_evento} Hrs")
                    resumen_partes.append("DJ+Ilum")
            elif inc_dj_basico:
                prod = _buscar_producto_por_nombre('Básico De DJ') or _buscar_producto_por_nombre('DJ Basico')
                if prod:
                    _agregar_item_producto(cotizacion, prod, cantidad=1,
                        descripcion_override=f"Servicio de DJ Básico - {horas_evento} Hrs")
                    resumen_partes.append("DJ")

            barra_partes = []
            if inc_cerveza: barra_partes.append("Cerveza")
            if inc_nacional: barra_partes.append("Nacional")
            if inc_premium: barra_partes.append("Premium")
            if inc_cocteleria: barra_partes.append("Cocteles")
            if inc_mixologia: barra_partes.append("Mixología")
            if barra_partes:
                resumen_partes.append("Barra(" + "/".join(barra_partes) + ")")

            if horas_evento > 6:
                horas_extra = horas_evento - 6
                prod = _buscar_producto_por_nombre('Hora Extra De Arrendamiento')
                if not prod:
                    prod = _buscar_producto_por_nombre('Hora Extra')
                if prod:
                    _agregar_item_producto(cotizacion, prod, cantidad=horas_extra,
                        descripcion_override=f"Horas Extra de Arrendamiento ({horas_extra} hrs adicionales)")
                    resumen_partes.append(f"+{horas_extra}hrs")

            mensaje_libre = data.get('mensaje_libre', '').strip()
            if mensaje_libre and mensaje_libre.lower() not in ('no', 'nada', 'ninguno', ''):
                nota = f" | Nota: {mensaje_libre[:150]}"
                nuevo_nombre = (cotizacion.nombre_evento + nota)[:200]
                Cotizacion.objects.filter(pk=cotizacion.pk).update(nombre_evento=nuevo_nombre)

        # =====================
        # 4. RECALCULAR TOTALES (con IVA)
        # =====================
        cotizacion.calcular_totales()
        Cotizacion.objects.filter(pk=cotizacion.pk).update(
            subtotal=cotizacion.subtotal,
            iva=cotizacion.iva,
            retencion_isr=cotizacion.retencion_isr,
            retencion_iva=cotizacion.retencion_iva,
            precio_final=cotizacion.precio_final
        )
        cotizacion.refresh_from_db()

        # =====================
        # 5. GENERAR PDF Y SUBIR A CLOUDINARY
        # =====================
        context = obtener_contexto_cotizacion(cotizacion)
        html_string = render_to_string('cotizaciones/pdf_recibo.html', context)
        pdf_bytes = HTML(string=html_string).write_pdf()

        folio = f"COT-{cotizacion.id:03d}"
        filename = f"{folio}_{timezone.now().strftime('%d-%m-%Y')}.pdf"

        from django.core.files.base import ContentFile
        cotizacion.archivo_pdf.save(filename, ContentFile(pdf_bytes), save=False)
        Cotizacion.objects.filter(pk=cotizacion.pk).update(archivo_pdf=cotizacion.archivo_pdf.name)
        cotizacion.refresh_from_db()

        pdf_url = cotizacion.archivo_pdf.url if cotizacion.archivo_pdf else ''

        # =====================
        # 5.5 CREAR PORTAL DEL CLIENTE
        # =====================
        from .models import PortalCliente
        portal, _ = PortalCliente.objects.get_or_create(
            cotizacion=cotizacion,
            defaults={'activo': True}
        )
        portal_url = f"https://erp-qkt.up.railway.app/mi-evento/{portal.token}/"

        # =====================
        # 6. RESUMEN
        # =====================
        clima_tag = ""
        if clima_auto == 'extremo':
            clima_tag = " Temporada calor extremo"
        elif clima_auto == 'calor':
            clima_tag = " Temporada calor"

        resumen = " + ".join(resumen_partes) + f" | {num_personas} Pax - {horas_evento} Hrs{clima_tag}"

        if aviso_fecha:
            try:
                from comunicacion.services import alertar_equipo_fecha_chocada
                alertar_equipo_fecha_chocada(cotizacion, aviso_fecha)
            except Exception:
                pass

        return JsonResponse({
            'status': 'success',
            'cotizacion_id': cotizacion.id,
            'folio': folio,
            'precio_final': f"${cotizacion.precio_final:,.2f}",
            'pdf_url': pdf_url,
            'portal_url': portal_url,
            'resumen': resumen,
            'num_personas_final': num_personas,
            'aviso_fecha': aviso_fecha,
            'fecha_disponible': aviso_fecha is None,
        }, status=200)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'message': f'Error interno: {str(e)}'}, status=500)

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
        estado__in=['COTIZADA', 'ANTICIPO', 'CONFIRMADA', 'EN_PREPARACION', 'EJECUTADA']
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