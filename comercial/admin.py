from django.contrib import admin
from django.utils.html import format_html, mark_safe
from django.urls import reverse, NoReverseMatch, path
from django.contrib import messages
from django.shortcuts import render, redirect
from django.db.models import Sum
from .models import (
    Insumo, SubProducto, RecetaSubProducto, Producto, ComponenteProducto, 
    Cliente, Cotizacion, ItemCotizacion, Pago, 
    Compra, Gasto
)

@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'categoria', 'costo_unitario', 'factor_rendimiento', 'cantidad_stock')
    list_editable = ('costo_unitario', 'factor_rendimiento', 'categoria')
    list_filter = ('categoria',)
    search_fields = ('nombre',)
    list_per_page = 20

class RecetaInline(admin.TabularInline):
    model = RecetaSubProducto
    extra = 1
    autocomplete_fields = ['insumo']
    verbose_name = "Ingrediente"

@admin.register(SubProducto)
class SubProductoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'costo_insumos')
    inlines = [RecetaInline]
    search_fields = ('nombre',)

class ComponenteInline(admin.TabularInline):
    model = ComponenteProducto
    extra = 1
    autocomplete_fields = ['subproducto']
    verbose_name = "SubProducto"

@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    inlines = [ComponenteInline]
    list_display = ('nombre', 'calcular_costo', 'sugerencia_precio')
    search_fields = ('nombre',)

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

class ItemCotizacionInline(admin.TabularInline):
    model = ItemCotizacion
    extra = 1
    autocomplete_fields = ['producto', 'insumo']
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
    list_display = ('folio_cotizacion', 'nombre_evento', 'cliente', 'fecha_evento', 'tipo_barra', 'precio_final', 'ver_pdf', 'enviar_email_btn')
    list_filter = ('estado', 'requiere_factura', 'fecha_evento', 'tipo_barra')
    search_fields = ('id', 'cliente__nombre', 'cliente__rfc', 'nombre_evento')
    autocomplete_fields = ['cliente'] 
    class Media: css = {'all': ('css/admin_fix.css',)}

    fieldsets = (
        ('Evento', {'fields': ('cliente', 'nombre_evento', 'fecha_evento', ('hora_inicio', 'hora_fin'), 'num_personas', 'estado')}),
        ('Configuración Barra', {
            'fields': (
                ('tipo_barra', 'horas_servicio', 'factor_utilidad_barra'),
                ('insumo_hielo', 'insumo_refresco', 'insumo_agua'),
                ('insumo_alcohol_basico', 'insumo_alcohol_premium'),
                ('insumo_barman', 'insumo_auxiliar'),
                'resumen_barra_html'
            ),
            'classes': ('collapse', 'open'), 
        }),
        ('Finanzas', {'fields': ('subtotal', 'descuento', 'requiere_factura')}),
        ('Fiscal', {'fields': ('iva', 'retencion_isr', 'retencion_iva', 'precio_final')}),
        ('Archivos', {'fields': ('archivo_pdf', 'enviar_email_btn')}),
    )
    readonly_fields = ('subtotal', 'iva', 'retencion_isr', 'retencion_iva', 'precio_final', 'enviar_email_btn', 'resumen_barra_html')

    def folio_cotizacion(self, obj): return f"COT-{obj.id:03d}"
    
    def resumen_barra_html(self, obj):
        datos = obj.calcular_barra_insumos()
        if not datos:
            return mark_safe('<span style="color:#6c757d; font-style:italic;">Guarde una selección de barra válida para ver el cálculo.</span>')
        
        style_table = "width:100%; border-collapse: collapse; font-family: 'Segoe UI', Tahoma, sans-serif; font-size: 13px;"
        style_th = "text-align: left; padding: 8px; border-bottom: 2px solid #dee2e6; color: #495057;"
        style_td = "padding: 8px; border-bottom: 1px solid #e9ecef;"
        style_val = "font-weight: 600; text-align: right;"

        seccion_botellas = ""
        if obj.tipo_barra != 'sin_alcohol':
            seccion_botellas = f"<tr><td style='{style_td}'>Botellas:</td><td style='{style_td} {style_val}'>{datos['botellas']} u.</td></tr>"

        html = f"""
        <div style="background-color: white; border: 1px solid #dcdcdc; border-radius: 4px; padding: 0; max-width: 800px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
            <div style="background-color: #f8f9fa; padding: 10px 15px; border-bottom: 1px solid #dcdcdc;">
                <h3 style="margin: 0; color: #333; font-size: 14px; text-transform: uppercase; font-weight: 700;">Resultados Calculadora</h3>
            </div>
            <div style="display: flex; flex-wrap: wrap;">
                <div style="flex: 1; min-width: 300px; padding: 0;">
                    <table style="{style_table} border-right: 1px solid #eee;">
                        <tr><th colspan="2" style="{style_th}">REQUERIMIENTOS</th></tr>
                        {seccion_botellas}
                        <tr><td style="{style_td}">Hielo (Bolsas):</td><td style="{style_td} {style_val}">{datos['bolsas_hielo_20kg']} bolsas</td></tr>
                        <tr><td style="{style_td}">Mezcladores:</td><td style="{style_td} {style_val}">{datos['litros_mezcladores']} L</td></tr>
                        <tr><td style="{style_td}">Agua:</td><td style="{style_td} {style_val}">{datos['litros_agua']} L</td></tr>
                        <tr><td style="{style_td}">Staff:</td><td style="{style_td} {style_val}">{datos['num_barmans']} B / {datos['num_auxiliares']} A</td></tr>
                    </table>
                </div>
                <div style="flex: 1; min-width: 300px; padding: 0; background-color: #fffbf2;">
                    <table style="{style_table}">
                        <tr><th colspan="2" style="{style_th} background-color: #fffbf2; color: #856404;">FINANZAS</th></tr>
                        <tr><td style="{style_td}">Costo Total:</td><td style="{style_td} {style_val} color: #dc3545;">${datos['costo_total_estimado']:,.2f}</td></tr>
                        <tr><td style="{style_td}">Costo Unitario:</td><td style="{style_td} {style_val} text-align: right;">${datos['costo_pax']:,.2f}</td></tr>
                        <tr><td style="{style_td} border-top: 2px solid #e0c482;"><strong>PRECIO SUGERIDO:</strong></td><td style="{style_td} {style_val} color: #28a745; font-size: 15px; border-top: 2px solid #e0c482;">${datos['precio_venta_sugerido_total']:,.2f}</td></tr>
                    </table>
                </div>
            </div>
        </div>
        """
        return mark_safe(html)
    resumen_barra_html.short_description = "Resumen Ejecutivo"

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
            cot.save()

    def ver_pdf(self, obj):
        if obj.id: return format_html('<a href="{}" target="_blank" class="btn btn-info btn-sm" style="white-space: nowrap;">PDF</a>', reverse('cotizacion_pdf', args=[obj.id]))
        return "-"
    ver_pdf.short_description = "PDF"

    def enviar_email_btn(self, obj):
        if obj.id: return format_html('<a href="{}" class="btn btn-success btn-sm" style="white-space: nowrap;">Enviar</a>', reverse('cotizacion_email', args=[obj.id]))
        return "-"
    enviar_email_btn.short_description = "Email"

@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ('fecha_pago', 'cotizacion', 'monto', 'metodo', 'usuario')
    list_filter = ('fecha_pago', 'metodo', 'usuario')
    search_fields = ('cotizacion__cliente__nombre', 'referencia')
    date_hierarchy = 'fecha_pago'
    autocomplete_fields = ['cotizacion']
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
        ('Archivo Fuente', {'fields': ('archivo_xml', 'archivo_pdf')}),
        ('Datos Generales', {'fields': ('fecha_emision', 'proveedor', 'rfc_emisor', 'uuid')}),
        ('Totales Globales', {'fields': ('subtotal', 'descuento', 'iva', 'ret_isr', 'ret_iva', 'total')})
    )
    readonly_fields = ('fecha_emision', 'proveedor', 'rfc_emisor', 'uuid', 'subtotal', 'descuento', 'iva', 'ret_isr', 'ret_iva', 'total')
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
    class Media: css = {'all': ('css/admin_fix.css',)}