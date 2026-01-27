from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse, NoReverseMatch
from django.contrib import messages
from django.db.models import Sum
from .models import Insumo, SubProducto, RecetaSubProducto, Producto, ComponenteProducto, Cliente, Cotizacion, ItemCotizacion, Pago, Gasto

@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'categoria', 'cantidad_stock', 'unidad_medida')
    list_editable = ('cantidad_stock', 'categoria')
    list_filter = ('categoria',)
    search_fields = ('nombre',)
    list_per_page = 20

# --- NUEVO: ADMIN PARA SUBPRODUCTOS (NIVEL 2) ---
class RecetaInline(admin.TabularInline):
    model = RecetaSubProducto
    extra = 1
    autocomplete_fields = ['insumo']
    verbose_name = "Ingrediente / Insumo"
    verbose_name_plural = "Receta (Insumos necesarios)"

@admin.register(SubProducto)
class SubProductoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'costo_insumos')
    inlines = [RecetaInline]
    search_fields = ('nombre',)

# --- ADMIN PARA PRODUCTOS (NIVEL 3) ---
class ComponenteInline(admin.TabularInline):
    model = ComponenteProducto
    extra = 1
    autocomplete_fields = ['subproducto']
    verbose_name = "SubProducto incluido"
    verbose_name_plural = "Contenido del Paquete"

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
        ('Datos Generales', {
            'fields': ('nombre', 'email', 'telefono', 'origen', 'fecha_registro')
        }),
        ('Datos Fiscales (Facturación)', {
            'fields': ('es_cliente_fiscal', 'tipo_persona', 'rfc', 'razon_social', 'codigo_postal_fiscal', 'regimen_fiscal', 'uso_cfdi'),
            'description': 'Estos datos se usarán automáticamente al crear solicitudes de factura.'
        }),
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
    fields = ('monto', 'metodo', 'referencia', 'fecha_pago', 'usuario')
    readonly_fields = ('fecha_pago', 'usuario')

@admin.register(Cotizacion)
class CotizacionAdmin(admin.ModelAdmin):
    inlines = [ItemCotizacionInline, PagoInline]

    list_display = ('folio_cotizacion', 'nombre_evento', 'cliente', 'fecha_evento', 'estado', 'precio_final', 'ver_pdf', 'enviar_email_btn')
    
    list_filter = ('estado', 'requiere_factura', 'fecha_evento')
    search_fields = ('id', 'cliente__nombre', 'cliente__rfc', 'nombre_evento')
    autocomplete_fields = ['cliente'] 

    fieldsets = (
        ('Datos del Evento', {
            'fields': ('cliente', 'nombre_evento', 'fecha_evento', ('hora_inicio', 'hora_fin'), 'estado')
        }),
        ('Finanzas', {
            'fields': ('subtotal', 'descuento', 'requiere_factura') 
        }),
        ('Cálculo Fiscal (Automático)', {
            'fields': ('iva', 'retencion_isr', 'retencion_iva', 'precio_final') 
        }),
        ('Acciones y Archivos', { 
            'fields': ('archivo_pdf', 'enviar_email_btn') 
        }),
    )
    
    readonly_fields = ('subtotal', 'iva', 'retencion_isr', 'retencion_iva', 'precio_final', 'enviar_email_btn')

    def folio_cotizacion(self, obj):
        return f"COT-{obj.id:03d}"
    folio_cotizacion.short_description = "Folio"

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.usuario = request.user
            
        # Forzamos el cálculo antes de guardar el modelo padre
        # (Nota: los inlines se guardan DESPUÉS de save_model, así que el cálculo real 
        # a veces requiere guardar dos veces o usar signals, pero para simplificar:)
        obj.calcular_totales()
        
        # --- LÓGICA DE STOCK (NUEVA JERARQUÍA) ---
        cotizacion_anterior = None
        if change:
            try:
                cotizacion_anterior = Cotizacion.objects.get(pk=obj.pk)
            except Cotizacion.DoesNotExist:
                pass

        recalcular_stock = False
        devolver_stock_anterior = False

        if obj.estado == 'CONFIRMADA':
            if not cotizacion_anterior or cotizacion_anterior.estado != 'CONFIRMADA':
                recalcular_stock = True # Confirmación nueva

        if cotizacion_anterior and cotizacion_anterior.estado == 'CONFIRMADA' and obj.estado != 'CONFIRMADA':
            devolver_stock_anterior = True # Cancelación

        # 1. DEVOLUCIÓN DE STOCK (Si se cancela)
        if devolver_stock_anterior:
            self._ajustar_stock(cotizacion_anterior, operacion='sumar')
            messages.info(request, f"🔄 Stock devuelto por cancelación de: {cotizacion_anterior.nombre_evento}")

        # 2. DESCUENTO DE STOCK (Si se confirma)
        if recalcular_stock:
            errores = self._validar_stock(obj)
            if errores:
                messages.error(request, f"⛔ NO SE PUEDE CONFIRMAR: {', '.join(errores)}")
                obj.estado = 'BORRADOR'
            else:
                self._ajustar_stock(obj, operacion='restar')
                messages.success(request, f"✅ Evento Confirmado. Stock descontado correctamente.")

        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for instance in instances:
            if isinstance(instance, Pago) and not instance.pk:
                instance.usuario = request.user
            instance.save()
        formset.save_m2m()
        
        # Recalcular totales después de guardar los items
        if isinstance(formset.instance, Cotizacion):
            formset.instance.calcular_totales()
            formset.instance.save()

    # --- HELPERS PARA GESTIÓN DE STOCK RECURSIVO ---
    
    def _desglosar_insumos(self, cotizacion):
        """ Retorna un diccionario {insumo_id: cantidad_total_necesaria} recorriendo todo el árbol """
        necesidades = {}
        
        for item in cotizacion.items.all():
            cantidad_item = item.cantidad
            
            # CASO A: Ítem es un Producto (Paquete)
            if item.producto:
                for comp in item.producto.componentes.all(): # Nivel 2: SubProductos
                    subproducto = comp.subproducto
                    cantidad_sub = comp.cantidad * cantidad_item
                    
                    for receta in subproducto.receta.all(): # Nivel 1: Insumos
                        insumo = receta.insumo
                        total_insumo = receta.cantidad * cantidad_sub
                        
                        if insumo.categoria == 'CONSUMIBLE':
                            necesidades[insumo.id] = necesidades.get(insumo.id, 0) + total_insumo

            # CASO B: Ítem es un Insumo directo (Extra)
            elif item.insumo:
                if item.insumo.categoria == 'CONSUMIBLE':
                     necesidades[item.insumo.id] = necesidades.get(item.insumo.id, 0) + item.cantidad

        return necesidades

    def _validar_stock(self, cotizacion):
        errores = []
        necesidades = self._desglosar_insumos(cotizacion)
        
        for insumo_id, cantidad in necesidades.items():
            insumo = Insumo.objects.get(id=insumo_id)
            if insumo.cantidad_stock < cantidad:
                errores.append(f"{insumo.nombre} (Stock: {insumo.cantidad_stock}, Requiere: {cantidad})")
        return errores

    def _ajustar_stock(self, cotizacion, operacion):
        necesidades = self._desglosar_insumos(cotizacion)
        for insumo_id, cantidad in necesidades.items():
            insumo = Insumo.objects.get(id=insumo_id)
            if operacion == 'sumar':
                insumo.cantidad_stock += cantidad
            else:
                insumo.cantidad_stock -= cantidad
            insumo.save()

    # --- BOTONES DE ACCIÓN ---
    def ver_pdf(self, obj):
        if obj.id:
            try:
                url_pdf = reverse('cotizacion_pdf', args=[obj.id])
                return format_html(
                    '<a href="{}" target="_blank" style="background-color:#17a2b8; color:white; padding:5px 10px; border-radius:5px; text-decoration:none;">'
                    '<i class="fas fa-file-pdf"></i> PDF</a>', url_pdf
                )
            except NoReverseMatch: return "-"
        return "-"
    ver_pdf.short_description = "Cotización"
    ver_pdf.allow_tags = True

    def enviar_email_btn(self, obj):
        if obj.id:
            try:
                url_email = reverse('cotizacion_email', args=[obj.id])
                return format_html(
                    '<a href="{}" style="background-color:#28a745; color:white; padding:5px 10px; border-radius:5px; text-decoration:none;">'
                    '<i class="fas fa-envelope"></i> Enviar</a>', url_email
                )
            except NoReverseMatch: return "-"
        return "-"
    enviar_email_btn.short_description = "Email"
    enviar_email_btn.allow_tags = True

@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ('cotizacion', 'monto', 'metodo', 'usuario', 'fecha_pago')
    list_filter = ('fecha_pago', 'metodo', 'usuario')
    autocomplete_fields = ['cotizacion']
    
    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.usuario = request.user
        super().save_model(request, obj, form, change)

@admin.register(Gasto)
class GastoAdmin(admin.ModelAdmin):
    list_display = ('fecha_gasto', 'proveedor', 'descripcion', 'monto', 'categoria', 'tiene_xml', 'ver_pdf')
    list_filter = ('fecha_gasto', 'categoria')
    search_fields = ('descripcion', 'proveedor', 'uuid')
    date_hierarchy = 'fecha_gasto'
    readonly_fields = ('uuid', 'proveedor', 'monto', 'fecha_gasto', 'descripcion')

    def tiene_xml(self, obj):
        return "✅ Sí" if obj.archivo_xml else "📝 Manual"
    tiene_xml.short_description = "Registro"
    
    def ver_pdf(self, obj):
        if obj.archivo_pdf:
            return format_html(
                '<a href="{}" target="_blank" style="background-color:#17a2b8; color:white; padding:5px 10px; border-radius:5px; text-decoration:none;">'
                '<i class="fas fa-file-pdf"></i> PDF</a>', obj.archivo_pdf.url
            )
        return "-"
    ver_pdf.short_description = "Comprobante"
    
    def get_readonly_fields(self, request, obj=None):
        if obj and obj.archivo_xml:
            return self.readonly_fields
        return ('uuid',)