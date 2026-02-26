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
    Compra, Gasto, ConstanteSistema, PlantillaBarra, Proveedor
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
        return format_html(
            '<span style="color:#999;">Sin insumos</span>'
        )
    total_insumos.short_description = "Insumos Vinculados"

    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']


# ==========================================
# INSUMOS
# ==========================================
@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'presentacion', 'categoria', 'proveedor', 'costo_unitario', 'factor_rendimiento', 'cantidad_stock')
    list_editable = ('costo_unitario', 'factor_rendimiento', 'categoria')
    list_filter = ('categoria', 'proveedor')
    search_fields = ('nombre', 'proveedor__nombre', 'presentacion') 
    autocomplete_fields = ['proveedor']
    list_per_page = 20
    fieldsets = (
        (None, {'fields': ('nombre', 'presentacion', 'categoria', 'unidad_medida')}),
        ('Costos y Stock', {'fields': ('costo_unitario', 'factor_rendimiento', 'cantidad_stock')}),
        ('Proveedor', {'fields': ('proveedor',)}),
        ('Opciones', {'fields': ('crear_como_subproducto',), 'classes': ('collapse',)}),
    )
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']


# ==========================================
# PLANTILLA DE BARRA
# ==========================================
@admin.register(PlantillaBarra)
class PlantillaBarraAdmin(admin.ModelAdmin):
    change_list_template = "admin/comercial/plantillabarra_change_list.html"
    
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

class ItemCotizacionInline(admin.TabularInline):
    model = ItemCotizacion
    extra = 1
    raw_id_fields = ['producto', 'insumo']
    fields = ('producto', 'insumo', 'descripcion', 'cantidad', 'precio_unitario', 'subtotal')
    readonly_fields = ('subtotal',)

class PagoInline(admin.TabularInline):
    model = Pago
    extra = 0
    fields = ('fecha_pago', 'monto', 'metodo', 'referencia', 'usuario')
    readonly_fields = ('usuario',)

@admin.register(Cotizacion)
class CotizacionAdmin(admin.ModelAdmin):
    inlines = [ItemCotizacionInline, PagoInline]
    list_display = ('folio_cotizacion', 'nombre_evento', 'cliente', 'fecha_evento', 'get_nivel_paquete', 'precio_final', 'ver_pdf', 'ver_lista_compras', 'enviar_email_btn')
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
        ('Documentos', {'fields': ('archivo_pdf', 'enviar_email_btn')}),
    )
    readonly_fields = ('subtotal', 'iva', 'retencion_isr', 'retencion_iva', 'precio_final', 'enviar_email_btn', 'resumen_barra_html')

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

    def save_model(self, request, obj, form, change):
        if not obj.pk: obj.usuario = request.user
        super().save_model(request, obj, form, change)

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
            Cotizacion.objects.filter(pk=cot.pk).update(subtotal=cot.subtotal, precio_final=cot.precio_final)

    def ver_pdf(self, obj):
        if obj.id:
            try:
                url = reverse('cotizacion_pdf', args=[obj.id])
                return format_html('<a href="{}" target="_blank" class="btn btn-primary">PDF</a>', url)
            except NoReverseMatch: return "-"
        return "-"
    ver_pdf.short_description = "PDF"

    def ver_lista_compras(self, obj):
        if obj.id:
            try:
                checks = [obj.incluye_refrescos, obj.incluye_cerveza, obj.incluye_licor_nacional, obj.incluye_licor_premium, obj.incluye_cocteleria_basica, obj.incluye_cocteleria_premium]
                if any(checks):
                    url = reverse('cotizacion_lista_compras', args=[obj.id])
                    return format_html('<a href="{}" target="_blank" class="btn btn-warning" style="background-color:#ffc107; color:#212529; border:none;">📋 Lista</a>', url)
            except NoReverseMatch: return "-"
        return "-"
    ver_lista_compras.short_description = "Insumos"

    def enviar_email_btn(self, obj):
        if obj.id:
            try:
                url = reverse('cotizacion_email', args=[obj.id])
                return format_html('<a href="{}" class="btn btn-success">Enviar</a>', url)
            except NoReverseMatch: return "-"
        return "-"
    enviar_email_btn.short_description = "Email"

@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ('fecha_pago', 'cotizacion', 'monto', 'metodo', 'usuario')
    list_filter = ('fecha_pago', 'metodo', 'usuario')
    search_fields = ('cotizacion__cliente__nombre', 'referencia')
    date_hierarchy = 'fecha_pago'
    raw_id_fields = ['cotizacion'] 
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
        context = dict(self.admin_site.each_context(request))
        return render(request, "comercial/carga_masiva.html", context)
    def total_format(self, obj): return f"${obj.total:,.2f}"
    def ver_pdf(self, obj):
        if obj.archivo_pdf: return format_html('<a href="{}" target="_blank" style="background-color:#dc3545; color:white; padding:2px 5px; border-radius:3px;">PDF</a>', obj.archivo_pdf.url)
        return "-"

@admin.register(Gasto)
class GastoAdmin(admin.ModelAdmin):
    change_list_template = "comercial/gasto_change_list.html"
    list_display = ('descripcion', 'categoria', 'total_linea', 'proveedor', 'fecha_gasto', 'evento_relacionado')
    list_filter = ('categoria', 'fecha_gasto', 'proveedor')
    search_fields = ('descripcion', 'proveedor')
    list_editable = ('categoria', 'evento_relacionado') 
    list_per_page = 50
    autocomplete_fields = ['compra', 'evento_relacionado']
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']