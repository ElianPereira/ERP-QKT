from django.core.exceptions import ValidationError
from django.contrib import admin
from django.utils.html import format_html, mark_safe
from django.template.loader import render_to_string
from django.urls import reverse, NoReverseMatch, path
from django.contrib import messages
from django.shortcuts import render, redirect
from django.db.models import Sum
from .models import (
    Insumo, SubProducto, RecetaSubProducto, Producto, ComponenteProducto, 
    Cliente, Cotizacion, ItemCotizacion, Pago, 
    Compra, Gasto, ConstanteSistema, PlantillaBarra, Proveedor,
    MovimientoInventario, PlanPago, ParcialidadPago
)
from .services import CalculadoraBarraService

MEDIA_CONFIG = {
    'css': { 'all': ('css/admin_fix.css', 'css/mobile_fix.css') },
    'js': ('js/tabs_fix.js',)
}

@admin.register(ConstanteSistema)
class ConstanteSistemaAdmin(admin.ModelAdmin):
    list_display = ('clave', 'valor', 'descripcion')
    list_editable = ('valor',)


# ==========================================
# PROVEEDORES
# ==========================================
@admin.register(Proveedor)
class ProveedorAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'contacto', 'telefono', 'email', 'total_insumos', 'activo')
    list_filter = ('activo',)
    list_editable = ('activo',)
    search_fields = ('nombre', 'contacto', 'telefono', 'email')
    list_per_page = 25
    fieldsets = (
        (None, {'fields': ('nombre', 'contacto', 'telefono', 'email')}),
        ('Información Adicional', {'fields': ('notas', 'activo')}),
    )

    def total_insumos(self, obj):
        count = obj.insumo_set.count()
        if count > 0:
            return format_html(
                '<span style="background:#27ae60; color:white; padding:2px 8px; border-radius:4px;">{} insumos</span>',
                count
            )
        return mark_safe('<span style="color:#999;">Sin insumos</span>')
    
    total_insumos.short_description = "Insumos Vinculados"

    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']


# ==========================================
# INSUMOS
# ==========================================
@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'presentacion', 'categoria', 'proveedor', 'costo_unitario', 'factor_rendimiento', 'cantidad_stock', 'badge_stock')
    list_editable = ('costo_unitario', 'factor_rendimiento', 'categoria')
    list_filter = ('categoria', 'proveedor')
    search_fields = ('nombre', 'proveedor__nombre', 'presentacion') 
    autocomplete_fields = ['proveedor']
    list_per_page = 20
    fieldsets = (
        (None, {'fields': ('nombre', 'presentacion', 'categoria', 'unidad_medida')}),
        ('Costos y Stock', {'fields': ('costo_unitario', 'factor_rendimiento', 'cantidad_stock', 'stock_minimo')}),
        ('Proveedor', {'fields': ('proveedor',)}),
        ('Opciones', {'fields': ('crear_como_subproducto',), 'classes': ('collapse',)}),
    )
    
    def badge_stock(self, obj):
        if obj.stock_minimo > 0 and obj.cantidad_stock < obj.stock_minimo:
            return format_html(
                '<span style="background:#e74c3c; color:white; padding:2px 8px; border-radius:4px; font-size:11px;">⚠️ BAJO</span>'
            )
        elif obj.cantidad_stock > 0:
            return format_html(
                '<span style="background:#27ae60; color:white; padding:2px 8px; border-radius:4px; font-size:11px;">✅ OK</span>'
            )
        return format_html(
            '<span style="background:#95a5a6; color:white; padding:2px 8px; border-radius:4px; font-size:11px;">Sin stock</span>'
        )
    badge_stock.short_description = "Estado"
    
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']


# ==========================================
# MOVIMIENTOS DE INVENTARIO
# ==========================================
@admin.register(MovimientoInventario)
class MovimientoInventarioAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'insumo', 'tipo_badge', 'cantidad', 'stock_anterior', 'stock_posterior', 'nota_corta', 'created_by')
    list_filter = ('tipo', 'created_at', 'insumo')
    search_fields = ('insumo__nombre', 'nota')
    raw_id_fields = ['insumo', 'compra', 'cotizacion']
    readonly_fields = ('stock_anterior', 'stock_posterior', 'created_at', 'created_by')
    list_per_page = 30
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Movimiento', {'fields': ('insumo', 'tipo', 'cantidad')}),
        ('Referencias', {'fields': ('compra', 'cotizacion', 'nota'), 'classes': ('collapse',)}),
        ('Auditoría', {'fields': ('stock_anterior', 'stock_posterior', 'created_by', 'created_at')}),
    )
    
    def tipo_badge(self, obj):
        colores = {
            'ENTRADA': '#27ae60',
            'SALIDA': '#e74c3c',
            'AJUSTE_POS': '#3498db',
            'AJUSTE_NEG': '#e67e22',
            'DEVOLUCION': '#9b59b6',
        }
        color = colores.get(obj.tipo, '#666')
        signo = '+' if obj.tipo in ('ENTRADA', 'AJUSTE_POS') else '-'
        return format_html(
            '<span style="background:{}; color:white; padding:2px 8px; border-radius:4px; font-size:11px;">{} {}</span>',
            color, signo, obj.get_tipo_display()
        )
    tipo_badge.short_description = "Tipo"
    tipo_badge.admin_order_field = 'tipo'
    
    def nota_corta(self, obj):
        if obj.nota:
            return obj.nota[:50] + '...' if len(obj.nota) > 50 else obj.nota
        return '-'
    nota_corta.short_description = "Nota"
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.full_clean()
        super().save_model(request, obj, form, change)
    
    def has_delete_permission(self, request, obj=None):
        return False
    
    def has_change_permission(self, request, obj=None):
        if obj:
            return False
        return True
    
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']


# ==========================================
# PLANTILLA DE BARRA
# ==========================================
@admin.register(PlantillaBarra)
class PlantillaBarraAdmin(admin.ModelAdmin):
    
    list_display = ('categoria_display', 'grupo_display', 'insumo_nombre', 'insumo_presentacion', 'proveedor_insumo', 'costo_insumo', 'proporcion', 'activo')
    list_editable = ('proporcion', 'activo')
    list_filter = ('grupo', 'activo')
    search_fields = ('insumo__nombre', 'insumo__proveedor__nombre')
    raw_id_fields = ['insumo']
    list_per_page = 30
    ordering = ['grupo', 'orden', 'categoria']
    
    fieldsets = (
        ('Configuración', {'fields': ('categoria', 'grupo', 'insumo', 'proporcion', 'orden', 'activo')}),
    )
    
    def categoria_display(self, obj):
        return obj.get_categoria_display()
    categoria_display.short_description = "Concepto"
    categoria_display.admin_order_field = 'categoria'
    
    def grupo_display(self, obj):
        colores = {
            'ALCOHOL_NACIONAL': '#e67e22',
            'ALCOHOL_PREMIUM': '#9b59b6',
            'CERVEZA': '#f1c40f',
            'MEZCLADOR': '#3498db',
            'HIELO': '#ecf0f1',
            'COCTELERIA': '#2ecc71',
            'CONSUMIBLE': '#95a5a6',
        }
        color = colores.get(obj.grupo, '#666')
        return format_html(
            '<span style="background:{}; padding:2px 8px; border-radius:4px; color:#fff; font-size:11px;">{}</span>',
            color, obj.get_grupo_display()
        )
    grupo_display.short_description = "Grupo"
    grupo_display.admin_order_field = 'grupo'
    
    def insumo_nombre(self, obj):
        return obj.insumo.nombre
    insumo_nombre.short_description = "Insumo"
    insumo_nombre.admin_order_field = 'insumo__nombre'
    
    def insumo_presentacion(self, obj):
        return obj.insumo.presentacion or "-"
    insumo_presentacion.short_description = "Presentación"
    
    def proveedor_insumo(self, obj):
        if obj.insumo.proveedor:
            return obj.insumo.proveedor.nombre
        return "⚠️ Sin proveedor"
    proveedor_insumo.short_description = "Proveedor"
    
    def costo_insumo(self, obj):
        return f"${obj.insumo.costo_unitario:,.2f}"
    costo_insumo.short_description = "Costo"
    
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']


class RecetaInline(admin.TabularInline):
    model = RecetaSubProducto
    extra = 1
    raw_id_fields = ['insumo'] 
    verbose_name = "Ingrediente"

@admin.register(SubProducto)
class SubProductoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'costo_insumos')
    inlines = [RecetaInline]
    search_fields = ('nombre',)
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']

class ComponenteInline(admin.TabularInline):
    model = ComponenteProducto
    extra = 1
    raw_id_fields = ['subproducto']
    verbose_name = "SubProducto"

@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    inlines = [ComponenteInline]
    list_display = ('nombre', 'calcular_costo', 'sugerencia_precio')
    search_fields = ('nombre',)
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']

@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'tipo_persona', 'es_cliente_fiscal', 'rfc', 'email', 'telefono')
    list_filter = ('tipo_persona', 'es_cliente_fiscal', 'origen')
    search_fields = ('nombre', 'rfc', 'razon_social')
    fieldsets = (
        ('Datos Generales', {'fields': ('nombre', 'email', 'telefono', 'origen', 'fecha_registro')}),
        ('Datos Fiscales', {'fields': ('es_cliente_fiscal', 'tipo_persona', 'rfc', 'razon_social', 'codigo_postal_fiscal', 'regimen_fiscal', 'uso_cfdi')}),
    )
    readonly_fields = ('fecha_registro',)
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']


# ==========================================
# PLAN DE PAGOS
# ==========================================
class ParcialidadInline(admin.TabularInline):
    model = ParcialidadPago
    extra = 0
    fields = ('numero', 'concepto', 'porcentaje', 'monto', 'fecha_limite', 'pagada', 'fecha_pago_real', 'pago_vinculado')
    readonly_fields = ('numero', 'concepto', 'porcentaje', 'monto', 'fecha_limite')
    can_delete = False
    
    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PlanPago)
class PlanPagoAdmin(admin.ModelAdmin):
    list_display = ('cotizacion_folio', 'cliente', 'monto_total', 'num_parcialidades', 'progreso_badge', 'siguiente_pago_info', 'activo')
    list_filter = ('activo',)
    search_fields = ('cotizacion__cliente__nombre', 'cotizacion__nombre_evento')
    readonly_fields = ('cotizacion', 'generado_por', 'fecha_generacion')
    inlines = [ParcialidadInline]
    
    def cotizacion_folio(self, obj):
        return f"COT-{obj.cotizacion.id:03d}"
    cotizacion_folio.short_description = "Folio"
    
    def cliente(self, obj):
        return obj.cotizacion.cliente.nombre
    cliente.short_description = "Cliente"
    
    def monto_total(self, obj):
        return f"${obj.cotizacion.precio_final:,.2f}"
    monto_total.short_description = "Total"
    
    def num_parcialidades(self, obj):
        pagadas = obj.parcialidades_pagadas()
        total = obj.parcialidades.count()
        return f"{pagadas}/{total}"
    num_parcialidades.short_description = "Pagadas"
    
    def progreso_badge(self, obj):
        pagadas = obj.parcialidades_pagadas()
        total = obj.parcialidades.count()
        if total == 0:
            return '-'
        porcentaje = int((pagadas / total) * 100)
        if porcentaje >= 100:
            color = '#27ae60'
        elif porcentaje >= 50:
            color = '#f39c12'
        else:
            color = '#e74c3c'
        return format_html(
            '<div style="width:80px; background:#ecf0f1; border-radius:10px; height:12px; overflow:hidden; display:inline-block;">'
            '<div style="width:{}%; background:{}; height:100%; border-radius:10px;"></div>'
            '</div> <small style="color:{};">{}%</small>',
            porcentaje, color, color, porcentaje
        )
    progreso_badge.short_description = "Progreso"
    
    def siguiente_pago_info(self, obj):
        siguiente = obj.siguiente_pago()
        if not siguiente:
            return format_html('<span style="color:#27ae60; font-weight:bold;">✅ Liquidado</span>')
        
        dias = siguiente.dias_restantes
        if dias < 0:
            return format_html(
                '<span style="color:#e74c3c; font-weight:bold;">⚠️ ${} vencido hace {} días</span>',
                f"{siguiente.monto:,.2f}", abs(dias)
            )
        elif dias <= 7:
            return format_html(
                '<span style="color:#f39c12; font-weight:bold;">🔥 ${} en {} días</span>',
                f"{siguiente.monto:,.2f}", dias
            )
        else:
            return format_html(
                '<span style="color:#3498db;">${} el {}</span>',
                f"{siguiente.monto:,.2f}", siguiente.fecha_limite.strftime('%d/%m/%Y')
            )
    siguiente_pago_info.short_description = "Próximo Pago"
    
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']


class PlanPagoResumenInline(admin.StackedInline):
    model = PlanPago
    extra = 0
    max_num = 1
    can_delete = False
    readonly_fields = ('generado_por', 'fecha_generacion')
    fields = ('activo', 'notas', 'generado_por', 'fecha_generacion')
    verbose_name = "Plan de Pagos"
    verbose_name_plural = "Plan de Pagos"


# ==========================================
# COTIZACIONES
# ==========================================
class ItemCotizacionInline(admin.TabularInline):
    model = ItemCotizacion
    extra = 1
    raw_id_fields = ['producto', 'insumo']
    fields = ('producto', 'insumo', 'descripcion', 'cantidad', 'precio_unitario', 'subtotal')
    readonly_fields = ('subtotal',)

class PagoInline(admin.TabularInline):
    model = Pago
    extra = 0
    fields = ('fecha_pago', 'monto', 'metodo', 'referencia', 'notas', 'usuario', 'created_at')
    readonly_fields = ('usuario', 'created_at')

@admin.register(Cotizacion)
class CotizacionAdmin(admin.ModelAdmin):
    change_form_template = 'admin/comercial/cotizacion/change_form.html'
    inlines = [ItemCotizacionInline, PagoInline, PlanPagoResumenInline]
    list_display = ('folio_cotizacion', 'nombre_evento', 'cliente', 'fecha_evento', 'get_nivel_paquete', 'estado_badge', 'pago_badge', 'precio_final', 'ver_plan_pagos', 'ver_pdf', 'ver_lista_compras', 'enviar_email_btn')
    list_filter = ('estado', 'requiere_factura', 'fecha_evento', 'clima', 'incluye_licor_nacional', 'incluye_licor_premium')
    search_fields = ('id', 'cliente__nombre', 'cliente__rfc', 'nombre_evento')
    raw_id_fields = ['cliente', 'insumo_hielo', 'insumo_refresco', 'insumo_agua', 'insumo_alcohol_basico', 'insumo_alcohol_premium', 'insumo_barman', 'insumo_auxiliar']
    
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']

    fieldsets = (
        ('Información del Evento', {'fields': ('cliente', 'nombre_evento', 'fecha_evento', 'hora_inicio', 'hora_fin', 'num_personas', 'estado')}),
        ('Configuración de Barra', {
            'fields': ('incluye_refrescos', 'incluye_cerveza', 'incluye_licor_nacional', 'incluye_licor_premium', 'incluye_cocteleria_basica', 'incluye_cocteleria_premium', 'clima', 'horas_servicio', 'factor_utilidad_barra', 'resumen_barra_html'),
            'description': 'Selecciona los componentes para armar el paquete.'
        }),
        ('Insumos Base (Costos)', {
            'fields': ('insumo_hielo', 'insumo_refresco', 'insumo_agua', 'insumo_barman', 'insumo_auxiliar', 'insumo_alcohol_basico', 'insumo_alcohol_premium'),
            'classes': ('collapse',),
        }),
        ('Finanzas', {'fields': ('subtotal', 'descuento', 'requiere_factura', 'iva', 'retencion_isr', 'retencion_iva', 'precio_final')}),
        ('Cancelación', {
            'fields': ('motivo_cancelacion', 'cancelada_por', 'fecha_cancelacion'),
            'classes': ('collapse',),
        }),
        ('Documentos', {'fields': ('archivo_pdf', 'enviar_email_btn')}),
    )
    readonly_fields = ('subtotal', 'iva', 'retencion_isr', 'retencion_iva', 'precio_final', 'enviar_email_btn', 'resumen_barra_html', 'cancelada_por', 'fecha_cancelacion')

    def estado_badge(self, obj):
        colores = {
            'BORRADOR': '#95a5a6',
            'COTIZADA': '#3498db',
            'ANTICIPO': '#f39c12',
            'CONFIRMADA': '#27ae60',
            'EN_PREPARACION': '#8e44ad',
            'EJECUTADA': '#2c3e50',
            'CERRADA': '#1abc9c',
            'CANCELADA': '#e74c3c',
        }
        color = colores.get(obj.estado, '#666')
        return format_html(
            '<span style="background:{}; color:white; padding:3px 10px; border-radius:4px; font-size:11px; font-weight:bold;">{}</span>',
            color, obj.get_estado_display()
        )
    estado_badge.short_description = "Estado"
    estado_badge.admin_order_field = 'estado'
    
    def pago_badge(self, obj):
        porcentaje = obj.porcentaje_pagado
        if porcentaje >= 100:
            color = '#27ae60'
            icono = '✅'
        elif porcentaje >= 50:
            color = '#f39c12'
            icono = '🔶'
        elif porcentaje > 0:
            color = '#e67e22'
            icono = '🔸'
        else:
            color = '#e74c3c'
            icono = '⭕'
        
        return format_html(
            '<span style="color:{}; font-weight:bold;">{} {}%</span>',
            color, icono, porcentaje
        )
    pago_badge.short_description = "Pagado"

    def ver_plan_pagos(self, obj):
        """Botón para generar o ver plan de pagos."""
        try:
            plan = obj.plan_pago
            if plan and plan.activo:
                url_pdf = reverse('plan_pagos_pdf', args=[obj.id])
                pagadas = plan.parcialidades_pagadas()
                total = plan.parcialidades.count()
                return format_html(
                    '<a href="{}" target="_blank" class="btn btn-sm" '
                    'style="background:#8e44ad; color:white; padding:4px 10px; border-radius:4px;">'
                    '📋 {}/{}</a>', url_pdf, pagadas, total
                )
        except PlanPago.DoesNotExist:
            pass
        
        if obj.precio_final > 0:
            url_generar = reverse('generar_plan_pagos', args=[obj.id])
            return format_html(
                '<a href="{}" class="btn btn-sm" '
                'style="background:#3498db; color:white; padding:4px 10px; border-radius:4px;" '
                'onclick="return confirm(\'¿Generar plan de pagos para esta cotización?\')">'
                '➕ Plan</a>', url_generar
            )
        return '-'
    ver_plan_pagos.short_description = "Plan Pagos"

    def save_model(self, request, obj, form, change):
        if change:
            old_obj = Cotizacion.objects.filter(pk=obj.pk).values('estado').first()
            old_estado = old_obj['estado'] if old_obj else 'BORRADOR'
            
            if obj.estado != old_estado:
                permitidos = Cotizacion.TRANSICIONES_PERMITIDAS.get(old_estado, [])
                if obj.estado not in permitidos:
                    messages.error(
                        request,
                        f"⛔ No se puede cambiar de '{dict(Cotizacion.ESTADOS).get(old_estado)}' a "
                        f"'{obj.get_estado_display()}'. "
                        f"Transiciones permitidas: {', '.join(dict(Cotizacion.ESTADOS).get(e, e) for e in permitidos) or 'Ninguna'}"
                    )
                    return
                
                if obj.estado == 'CONFIRMADA':
                    try:
                        porcentaje_minimo = float(ConstanteSistema.objects.get(clave='PORCENTAJE_ANTICIPO_MINIMO').valor)
                    except ConstanteSistema.DoesNotExist:
                        porcentaje_minimo = 0
                    
                    if porcentaje_minimo > 0 and obj.precio_final > 0:
                        pagado = obj.total_pagado()
                        porcentaje_pagado = (pagado / obj.precio_final) * 100
                        if porcentaje_pagado < porcentaje_minimo:
                            messages.error(
                                request,
                                f"⛔ Se requiere al menos {porcentaje_minimo}% de anticipo para confirmar. "
                                f"Pagado: {porcentaje_pagado:.1f}% (${pagado:,.2f} de ${obj.precio_final:,.2f})"
                            )
                            return
                
                if old_estado == 'BORRADOR' and obj.estado != 'CANCELADA':
                    if obj.pk and not obj.items.exists():
                        messages.error(request, "⛔ La cotización debe tener al menos un item antes de avanzar.")
                        return
                
                if obj.estado == 'CANCELADA' and not obj.motivo_cancelacion:
                    messages.error(request, "⛔ Debe indicar el motivo de cancelación.")
                    return
                
                if obj.estado == 'CANCELADA':
                    obj.cancelada_por = request.user
                    from django.utils.timezone import now
                    obj.fecha_cancelacion = now()
        
        if obj.estado in ('CONFIRMADA', 'EN_PREPARACION'):
            try:
                from airbnb.validacion_fechas import validar_fecha_disponible
                disponible, mensaje = validar_fecha_disponible(obj.fecha_evento, exclude_cotizacion_id=obj.pk)
                if not disponible:
                    messages.error(
                        request,
                        f"⛔ Conflicto de calendario: {mensaje}. "
                        f"Debes cancelar la reserva de Airbnb primero o elegir otra fecha."
                    )
                    return
            except ImportError:
                pass
    
        if not obj.pk: obj.usuario = request.user
        super().save_model(request, obj, form, change)

    def folio_cotizacion(self, obj): return f"COT-{obj.id:03d}"

    def get_nivel_paquete(self, obj):
        checks = sum([obj.incluye_refrescos, obj.incluye_cerveza, obj.incluye_licor_nacional, obj.incluye_licor_premium, obj.incluye_cocteleria_basica, obj.incluye_cocteleria_premium])
        if checks == 0: return "⛔ Sin Servicio"
        if checks == 1: return "⭐ Básico"
        if checks == 2: return "⭐⭐ Plus"
        if checks >= 3: return "💎 Premium"
        return "Personalizado"
    get_nivel_paquete.short_description = "Paquete"
    
    def resumen_barra_html(self, obj):
        calc = CalculadoraBarraService(obj)
        datos = calc.calcular()
        if not datos:
            return mark_safe('<div style="padding:15px; color:#666;">Seleccione servicios y guarde para calcular.</div>')
        return mark_safe(render_to_string('admin/comercial/resumen_barra_partial.html', {'datos': datos}))
    resumen_barra_html.short_description = "Reporte Ejecutivo"

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects: obj.delete()
        for instance in instances:
            if isinstance(instance, Pago) and not instance.pk: instance.usuario = request.user
            instance.save()
        formset.save_m2m()
        cot = formset.instance
        if isinstance(cot, Cotizacion):
            cot.calcular_totales()
            Cotizacion.objects.filter(pk=cot.pk).update(
                subtotal=cot.subtotal, iva=cot.iva,
                retencion_isr=cot.retencion_isr, retencion_iva=cot.retencion_iva,
                precio_final=cot.precio_final
            )

    def ver_pdf(self, obj):
        try:
            url = reverse('cotizacion_pdf', args=[obj.id])
            return format_html('<a href="{}" target="_blank" class="btn btn-sm" style="background:#17a2b8; color:white; padding:4px 10px; border-radius:4px;">📄 PDF</a>', url)
        except NoReverseMatch: return "-"
    ver_pdf.short_description = "PDF"

    def ver_lista_compras(self, obj):
        try:
            url = reverse('cotizacion_lista_compras', args=[obj.id])
            return format_html('<a href="{}" target="_blank" class="btn btn-sm" style="background:#28a745; color:white; padding:4px 10px; border-radius:4px;">🛒 Lista</a>', url)
        except NoReverseMatch: return "-"
    ver_lista_compras.short_description = "Compras"

    def enviar_email_btn(self, obj):
        if obj.pk:
            try:
                url = reverse('cotizacion_email', args=[obj.id])
                return format_html('<a href="{}" class="btn btn-sm" style="background:#ffc107; color:#333; padding:4px 10px; border-radius:4px;" onclick="return confirm(\'¿Enviar cotización por email?\')">📧 Enviar</a>', url)
            except NoReverseMatch: return "-"
        return "-"
    enviar_email_btn.short_description = "Email"


@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ('cotizacion', 'fecha_pago', 'monto', 'metodo', 'referencia', 'usuario', 'created_at')
    list_filter = ('metodo', 'fecha_pago')
    search_fields = ('cotizacion__cliente__nombre', 'referencia', 'cotizacion__nombre_evento')
    readonly_fields = ('usuario', 'created_at', 'updated_at')
    date_hierarchy = 'fecha_pago'
    
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']
    def save_model(self, request, obj, form, change):
        if not obj.pk: obj.usuario = request.user
        super().save_model(request, obj, form, change)

class GastoInline(admin.TabularInline):
    model = Gasto
    extra = 0
    can_delete = True
    fields = ('cantidad', 'unidad_medida', 'descripcion', 'precio_unitario', 'total_linea', 'categoria', 'evento_relacionado')
    readonly_fields = ('cantidad', 'unidad_medida', 'descripcion', 'precio_unitario', 'total_linea') 
    def get_readonly_fields(self, request, obj=None): return [f for f in self.readonly_fields]

@admin.register(Compra)
class CompraAdmin(admin.ModelAdmin):
    change_list_template = "comercial/compra_change_list.html" 
    list_display = ('fecha_emision', 'proveedor', 'total_format', 'uuid', 'ver_pdf')
    list_filter = ('fecha_emision',)
    search_fields = ('proveedor', 'uuid')
    date_hierarchy = 'fecha_emision'
    inlines = [GastoInline] 
    fieldsets = (
        ('Archivo Fuente (Opcional)', {'fields': ('archivo_xml', 'archivo_pdf')}),
        ('Datos Generales', {'fields': ('fecha_emision', 'proveedor', 'rfc_emisor', 'uuid')}),
        ('Totales Globales', {'fields': ('subtotal', 'descuento', 'iva', 'ret_isr', 'ret_iva', 'total')})
    )
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']
    def get_urls(self):
        urls = super().get_urls()
        my_urls = [path('carga-masiva/', self.admin_site.admin_view(self.carga_masiva_view), name='compra_carga_masiva'),]
        return my_urls + urls
    def carga_masiva_view(self, request):
        if request.method == "POST":
            files = request.FILES.getlist('xml_files')
            if not files:
                messages.error(request, "No seleccionaste ningún archivo.")
                return redirect('.')
            exitos = 0
            errores = 0
            for f in files:
                try:
                    Compra.objects.create(archivo_xml=f)
                    exitos += 1
                except Exception as e:
                    errores += 1
                    print(f"Error subiendo {f.name}: {e}")
            if exitos > 0: messages.success(request, f"✅ Se procesaron {exitos} facturas correctamente.")
            if errores > 0: messages.warning(request, f"⚠️ Hubo problemas con {errores} archivos.")
            return redirect('..')
        return render(request, 'comercial/carga_masiva_xml.html', {'title': 'Carga Masiva de XML'})
    
    def total_format(self, obj): return f"${obj.total:,.2f}"
    total_format.short_description = "Total"
    
    def ver_pdf(self, obj):
        if obj.archivo_pdf:
            return format_html('<a href="{}" target="_blank">📄 Ver</a>', obj.archivo_pdf.url)
        return "-"
    ver_pdf.short_description = "PDF"