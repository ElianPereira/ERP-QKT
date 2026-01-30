from django.contrib import admin
from django.utils.html import format_html
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
    list_display = ('nombre', 'categoria', 'cantidad_stock', 'unidad_medida')
    list_editable = ('cantidad_stock', 'categoria')
    list_filter = ('categoria',)
    search_fields = ('nombre',)
    list_per_page = 20

# --- SUBPRODUCTOS ---
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

# --- PRODUCTOS ---
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

# --- CLIENTES ---
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

# --- COTIZACIONES ---
class ItemCotizacionInline(admin.TabularInline):
    model = ItemCotizacion
    extra = 1
    autocomplete_fields = ['producto', 'insumo']
    fields = ('producto', 'insumo', 'descripcion', 'cantidad', 'precio_unitario', 'subtotal')
    readonly_fields = ('subtotal',)

class PagoInline(admin.TabularInline):
    model = Pago
    extra = 0
    fields = ('monto', 'metodo', 'referencia', 'fecha_pago', 'usuario')
    readonly_fields = ('fecha_pago', 'usuario')

@admin.register(Cotizacion)
class CotizacionAdmin(admin.ModelAdmin):
    inlines = [ItemCotizacionInline, PagoInline]
    list_display = ('folio_cotizacion', 'nombre_evento', 'cliente', 'fecha_evento', 'estado', 'precio_final', 'ver_pdf', 'enviar_email_btn')
    list_filter = ('estado', 'requiere_factura', 'fecha_evento')
    search_fields = ('id', 'cliente__nombre', 'cliente__rfc', 'nombre_evento')
    autocomplete_fields = ['cliente'] 
    
    class Media:
        css = {'all': ('css/admin_v2.css',)}

    fieldsets = (
        ('Evento', {'fields': ('cliente', 'nombre_evento', 'fecha_evento', ('hora_inicio', 'hora_fin'), 'estado')}),
        ('Finanzas', {'fields': ('subtotal', 'descuento', 'requiere_factura')}),
        ('Fiscal', {'fields': ('iva', 'retencion_isr', 'retencion_iva', 'precio_final')}),
        ('Archivos', {'fields': ('archivo_pdf', 'enviar_email_btn')}),
    )
    readonly_fields = ('subtotal', 'iva', 'retencion_isr', 'retencion_iva', 'precio_final', 'enviar_email_btn')

    def folio_cotizacion(self, obj): return f"COT-{obj.id:03d}"
    
    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.usuario = request.user
            super().save_model(request, obj, form, change)
            return
        try:
            old = Cotizacion.objects.get(pk=obj.pk)
            obj._estado_previo = old.estado
        except: pass
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
            
            # Stock Logic
            prev = getattr(cot, '_estado_previo', 'BORRADOR')
            if cot.estado == 'CONFIRMADA' and prev != 'CONFIRMADA':
                errores = self._validar_stock(cot)
                if errores:
                    messages.error(request, f"⛔ Stock insuficiente: {', '.join(errores)}")
                    Cotizacion.objects.filter(pk=cot.pk).update(estado='BORRADOR')
                else:
                    self._ajustar_stock(cot, 'restar')
                    messages.success(request, "✅ Stock descontado.")
            elif prev == 'CONFIRMADA' and cot.estado != 'CONFIRMADA':
                self._ajustar_stock(cot, 'sumar')
                messages.info(request, "🔄 Stock devuelto.")

    def _validar_stock(self, cot):
        err = []
        for i_id, cant in self._desglosar(cot).items():
            ins = Insumo.objects.get(id=i_id)
            if ins.cantidad_stock < cant: err.append(f"{ins.nombre} (Req: {cant})")
        return err

    def _ajustar_stock(self, cot, op):
        for i_id, cant in self._desglosar(cot).items():
            ins = Insumo.objects.get(id=i_id)
            ins.cantidad_stock += cant if op == 'sumar' else -cant
            ins.save()

    def _desglosar(self, cot):
        nec = {}
        for it in cot.items.all():
            q = it.cantidad
            if it.producto:
                for c in it.producto.componentes.all():
                    q_sub = c.cantidad * q
                    for r in c.subproducto.receta.all():
                        if r.insumo.categoria == 'CONSUMIBLE':
                            nec[r.insumo.id] = nec.get(r.insumo.id, 0) + (r.cantidad * q_sub)
            elif it.insumo and it.insumo.categoria == 'CONSUMIBLE':
                nec[it.insumo.id] = nec.get(it.insumo.id, 0) + q
        return nec

    def ver_pdf(self, obj):
        if obj.id:
            return format_html('<a href="{}" target="_blank" class="button">PDF</a>', reverse('cotizacion_pdf', args=[obj.id]))
        return "-"
    
    def enviar_email_btn(self, obj):
        if obj.id:
            return format_html('<a href="{}" class="button">Enviar</a>', reverse('cotizacion_email', args=[obj.id]))
        return "-"

@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ('cotizacion', 'monto', 'metodo')

# ==========================================
# GESTIÓN DE COMPRAS Y GASTOS (CON CARGA MASIVA)
# ==========================================

class GastoInline(admin.TabularInline):
    model = Gasto
    extra = 0
    can_delete = True
    fields = ('cantidad', 'unidad_medida', 'descripcion', 'precio_unitario', 'total_linea', 'categoria', 'evento_relacionado')
    readonly_fields = ('cantidad', 'unidad_medida', 'descripcion', 'precio_unitario', 'total_linea') 
    
    def get_readonly_fields(self, request, obj=None):
        return [f for f in self.readonly_fields]

@admin.register(Compra)
class CompraAdmin(admin.ModelAdmin):
    # Template personalizado para agregar el botón de Carga Masiva
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

    # --- LÓGICA PARA CARGA MASIVA ---
    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path('carga-masiva/', self.admin_site.admin_view(self.carga_masiva_view), name='compra_carga_masiva'),
        ]
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
                    # Al crear el objeto Compra, se dispara el método .save() 
                    # que contiene toda tu lógica de lectura XML y creación de Gastos.
                    Compra.objects.create(archivo_xml=f)
                    exitos += 1
                except Exception as e:
                    errores += 1
                    print(f"Error subiendo {f.name}: {e}")

            if exitos > 0:
                messages.success(request, f"✅ Se procesaron {exitos} facturas correctamente.")
            if errores > 0:
                messages.warning(request, f"⚠️ Hubo problemas con {errores} archivos (quizás no eran XML válidos o ya existían).")
            
            return redirect('..') # Regresar a la lista

        context = dict(
           self.admin_site.each_context(request),
        )
        return render(request, "comercial/carga_masiva.html", context)
    # --------------------------------

    def total_format(self, obj): return f"${obj.total:,.2f}"
    
    def ver_pdf(self, obj):
        if obj.archivo_pdf:
            return format_html('<a href="{}" target="_blank" style="background-color:#dc3545; color:white; padding:2px 5px; border-radius:3px;">PDF</a>', obj.archivo_pdf.url)
        return "-"

@admin.register(Gasto)
class GastoAdmin(admin.ModelAdmin):
    change_list_template = "comercial/gasto_list.html"

    list_display = ('descripcion', 'categoria', 'total_linea', 'proveedor', 'fecha_gasto', 'evento_relacionado')
    list_filter = ('categoria', 'fecha_gasto', 'proveedor')
    search_fields = ('descripcion', 'proveedor')
    list_editable = ('categoria', 'evento_relacionado') 
    list_per_page = 50
    # === ¡ESTO CONECTA EL CSS PARA QUE VEAS EL BOTÓN FLOTANTE! ===
    class Media:
        css = {
            'all': ('css/admin_v2.css',)
        }   