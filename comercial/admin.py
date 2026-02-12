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

# ==========================================
# CONFIGURACIÓN COMPARTIDA (MEDIA)
# ==========================================
MEDIA_CONFIG = {
    'css': {
        'all': ('css/admin_fix.css', 'css/mobile_fix.css')
    },
    'js': (
        'js/tabs_fix.js',
    )
}

# ==========================================
# 1. INSUMOS Y PRODUCTOS
# ==========================================

@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'categoria', 'costo_unitario', 'factor_rendimiento', 'cantidad_stock')
    list_editable = ('costo_unitario', 'factor_rendimiento', 'categoria')
    list_filter = ('categoria',)
    search_fields = ('nombre',) 
    list_per_page = 20
    
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

# ==========================================
# 2. CLIENTES
# ==========================================

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
# 3. COTIZACIONES (EL MÓDULO PRINCIPAL)
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
    fields = ('fecha_pago', 'monto', 'metodo', 'referencia', 'usuario')
    readonly_fields = ('usuario',)

@admin.register(Cotizacion)
class CotizacionAdmin(admin.ModelAdmin):
    inlines = [ItemCotizacionInline, PagoInline]
    
    # Muestra el botón de PDF, Lista de Compras y Email en la lista principal
    list_display = ('folio_cotizacion', 'nombre_evento', 'cliente', 'fecha_evento', 'get_nivel_paquete', 'precio_final', 'ver_pdf', 'ver_lista_compras', 'enviar_email_btn')
    
    # Filtros actualizados con las nuevas opciones
    list_filter = (
        'estado', 
        'requiere_factura', 
        'fecha_evento', 
        'clima', 
        'incluye_licor_nacional', 
        'incluye_licor_premium',
        'incluye_cocteleria_basica',
        'incluye_cocteleria_premium'
    )
    
    search_fields = ('id', 'cliente__nombre', 'cliente__rfc', 'nombre_evento')
    
    raw_id_fields = [
       'cliente', 
       'insumo_hielo', 'insumo_refresco', 'insumo_agua',
       'insumo_alcohol_basico', 'insumo_alcohol_premium',
       'insumo_barman', 'insumo_auxiliar'
    ]
    
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']

    fieldsets = (
        ('Información del Evento', {
            'fields': (
                'cliente', 
                'nombre_evento', 
                'fecha_evento', 
                'hora_inicio', 
                'hora_fin', 
                'num_personas', 
                'estado'
            )
        }),
        ('Configuración de Barra (Modular)', {
            'fields': (
                # CONFIGURACIÓN VERTICAL
                'incluye_refrescos',
                'incluye_cerveza',
                'incluye_licor_nacional',
                'incluye_licor_premium',
                'incluye_cocteleria_basica',
                'incluye_cocteleria_premium',
                'clima', 
                'horas_servicio',
                'factor_utilidad_barra',
                'resumen_barra_html'
            ),
            'description': 'Selecciona los componentes (Checkboxes) para armar el paquete.'
        }),
        ('Selección de Insumos Base (Costos)', {
            'fields': (
                'insumo_hielo', 
                'insumo_refresco', 
                'insumo_agua',
                'insumo_barman',
                'insumo_auxiliar',
                'insumo_alcohol_basico', 
                'insumo_alcohol_premium',
            ),
            'classes': ('collapse',),
            'description': 'Define qué insumos reales del inventario se usarán para calcular el costo.'
        }),
        ('Finanzas', {
            'fields': (
                'subtotal', 
                'descuento', 
                'requiere_factura',
                'iva', 
                'retencion_isr', 
                'retencion_iva',
                'precio_final'
            )
        }),
        ('Documentos', {'fields': ('archivo_pdf', 'enviar_email_btn')}),
    )
    
    readonly_fields = ('subtotal', 'iva', 'retencion_isr', 'retencion_iva', 'precio_final', 'enviar_email_btn', 'resumen_barra_html')

    def folio_cotizacion(self, obj): return f"COT-{obj.id:03d}"

    def get_nivel_paquete(self, obj):
        checks = sum([
            obj.incluye_refrescos, obj.incluye_cerveza, 
            obj.incluye_licor_nacional, obj.incluye_licor_premium, 
            obj.incluye_cocteleria_basica, obj.incluye_cocteleria_premium
        ])
        if checks == 0: return "⛔ Sin Servicio"
        if checks == 1: return "⭐ Básico"
        if checks == 2: return "⭐⭐ Plus"
        if checks >= 3: return "💎 Premium"
        return "Personalizado"
    get_nivel_paquete.short_description = "Paquete"
    
    def resumen_barra_html(self, obj):
        datos = obj.calcular_barra_insumos()
        if not datos:
            return mark_safe('<div style="padding:15px; color:#666;">Seleccione servicios y guarde para calcular.</div>')
        
        # HTML del reporte en el admin (MARKET SHARE)
        html = f"""
        <div style="border:1px solid #ccc; border-radius:5px; overflow:hidden;">
            <div style="background:#2c3e50; color:white; padding:10px; font-weight:bold;">
                Resumen de Consumo Ponderado (Utilidad: x{datos['margen_aplicado']})
            </div>
            <table style="width:100%; border-collapse:collapse; font-size:13px; font-family:sans-serif;">
                
                <tr style="background:#ecf0f1; font-weight:bold;"><td colspan="3" style="padding:5px;">📦 ALCOHOL Y CERVEZA</td></tr>
                
                <tr style="border-bottom:1px solid #eee;">
                    <td style="padding:8px;">Cerveza (Caguamas):</td>
                    <td style="padding:8px; text-align:right;"><strong>{datos['cervezas_unidades']} u.</strong></td>
                    <td style="padding:8px; text-align:right; color:#7f8c8d;">(Incluido)</td>
                </tr>
                <tr style="border-bottom:1px solid #eee;">
                    <td style="padding:8px;">Licores (Botellas):</td>
                    <td style="padding:8px; text-align:right;">
                        <strong>{datos['botellas']} u.</strong><br>
                        <span style="font-size:10px; color:#666;">(Nac: {datos['botellas_nacional']} / Prem: {datos['botellas_premium']})</span>
                    </td>
                    <td style="padding:8px; text-align:right; color:#c0392b;">${datos['costo_alcohol']:,.2f}</td>
                </tr>

                <tr style="background:#ecf0f1; font-weight:bold;"><td colspan="3" style="padding:5px;">🧊 INSUMOS OPERATIVOS</td></tr>
                
                <tr style="border-bottom:1px solid #eee;">
                    <td style="padding:8px;">Hielo (20kg):</td>
                    <td style="padding:8px; text-align:right;">{datos['bolsas_hielo_20kg']} bolsas</td>
                    <td style="padding:8px; text-align:right; color:#7f8c8d;">--</td>
                </tr>
                <tr style="border-bottom:1px solid #eee;">
                    <td style="padding:8px;">Mixers y Agua:</td>
                    <td style="padding:8px; text-align:right;">{datos['litros_mezcladores']}L Mix / {datos['litros_agua']}L Agua</td>
                    <td style="padding:8px; text-align:right; color:#7f8c8d;">--</td>
                </tr>
                <tr style="border-bottom:1px solid #eee;">
                    <td style="padding:8px;"><strong>Total Insumos Varios:</strong><br><span style="font-size:10px;">(Hielo, Mixers, Fruta, Coctelería)</span></td>
                    <td style="padding:8px; text-align:right;">-</td>
                    <td style="padding:8px; text-align:right; color:#c0392b;">${datos['costo_insumos_varios']:,.2f}</td>
                </tr>

                <tr style="background:#ecf0f1; font-weight:bold;"><td colspan="3" style="padding:5px;">🤵 STAFF</td></tr>
                <tr style="border-bottom:1px solid #eee;">
                    <td style="padding:8px;">Brigada:</td>
                    <td style="padding:8px; text-align:right;">{datos['num_barmans']} Barman / {datos['num_auxiliares']} Aux</td>
                    <td style="padding:8px; text-align:right; color:#c0392b;">${datos['costo_staff']:,.2f}</td>
                </tr>

                <tr style="background:#fff3cd; border-top:2px solid #ffeeba; font-weight:bold;">
                    <td style="padding:8px;">COSTO TOTAL (Puro):</td>
                    <td></td>
                    <td style="padding:8px; text-align:right; color:#d35400;">${datos['costo_total_estimado']:,.2f}</td>
                </tr>
                <tr style="background:#d4edda; border-top:2px solid #c3e6cb; font-weight:bold; font-size:14px;">
                    <td style="padding:8px; color:#155724;">PRECIO SUGERIDO:</td>
                    <td></td>
                    <td style="padding:8px; text-align:right; color:#155724;">${datos['precio_venta_sugerido_total']:,.2f}</td>
                </tr>
            </table>
        </div>
        """
        return mark_safe(html)
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
            cot.save()

    def ver_pdf(self, obj):
        if obj.id:
            try:
                url = reverse('cotizacion_pdf', args=[obj.id])
                return format_html('<a href="{}" target="_blank" class="btn btn-primary">PDF</a>', url)
            except NoReverseMatch: return "-"
        return "-"
    ver_pdf.short_description = "PDF"

    # --- NUEVO BOTÓN PARA VER LISTA DE COMPRAS ---
    def ver_lista_compras(self, obj):
        if obj.id:
            try:
                checks = [
                    obj.incluye_refrescos, obj.incluye_cerveza, 
                    obj.incluye_licor_nacional, obj.incluye_licor_premium,
                    obj.incluye_cocteleria_basica, obj.incluye_cocteleria_premium
                ]
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

# ==========================================
# 4. COMPRAS Y GASTOS (CONTABILIDAD)
# ==========================================

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