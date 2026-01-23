from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse, NoReverseMatch
from django.contrib import messages
from django.db.models import Sum
from .models import Insumo, Producto, ComponenteProducto, Cliente, Cotizacion, ItemCotizacion, Pago, Gasto

@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'categoria', 'cantidad_stock', 'unidad_medida')
    list_editable = ('cantidad_stock', 'categoria')
    list_filter = ('categoria',)
    search_fields = ('nombre',)
    list_per_page = 20

class ComponenteInline(admin.TabularInline):
    model = ComponenteProducto
    extra = 1
    autocomplete_fields = ['insumo'] 

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

# --- NUEVO INLINE DE PAGOS ---
class PagoInline(admin.TabularInline):
    model = Pago
    extra = 0
    fields = ('monto', 'metodo', 'referencia', 'fecha_pago', 'usuario')
    readonly_fields = ('fecha_pago', 'usuario') # Para que no se modifique el cobrador

@admin.register(Cotizacion)
class CotizacionAdmin(admin.ModelAdmin):
    inlines = [ItemCotizacionInline, PagoInline] # Agregamos los pagos aquí

    list_display = ('folio_cotizacion', 'cliente', 'fecha_evento', 'hora_inicio', 'estado', 'precio_final', 'usuario')
    list_filter = ('estado', 'requiere_factura', 'fecha_evento')
    search_fields = ('id', 'cliente__nombre', 'cliente__rfc')
    autocomplete_fields = ['cliente', 'producto'] 

    fieldsets = (
        ('Datos del Evento', {
            'fields': ('cliente', 'producto', 'fecha_evento', ('hora_inicio', 'hora_fin'), 'estado')
        }),
        ('Finanzas', {
            'fields': ('subtotal', 'requiere_factura') 
        }),
        ('Cálculo Fiscal (Automático)', {
            'fields': ('iva', 'retencion_isr', 'retencion_iva', 'precio_final') 
        }),
        ('Acciones y Archivos', { 
            'fields': ('archivo_pdf', 'enviar_email_btn') 
        }),
    )
    
    readonly_fields = ('iva', 'retencion_isr', 'retencion_iva', 'precio_final', 'enviar_email_btn')

    def folio_cotizacion(self, obj):
        return f"COT-{obj.id:03d}"
    folio_cotizacion.short_description = "Folio"
    folio_cotizacion.admin_order_field = 'id'

    # --- GUARDADO AUTOMÁTICO DE USUARIO ---
    def save_model(self, request, obj, form, change):
        # Si es una cotización nueva, asignamos el usuario actual
        if not obj.pk:
            obj.usuario = request.user
            
        """
        Lógica central de inventario:
        - Si es 'MOBILIARIO/SERVICIO': Verifica disponibilidad por fecha.
        - Si es 'CONSUMIBLE': Descuenta del stock físico.
        """
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
                recalcular_stock = True
            elif cotizacion_anterior and cotizacion_anterior.producto != obj.producto:
                devolver_stock_anterior = True
                recalcular_stock = True

        if devolver_stock_anterior:
            for componente in cotizacion_anterior.producto.componentes.all():
                if componente.insumo.categoria == 'CONSUMIBLE':
                    componente.insumo.cantidad_stock += componente.cantidad
                    componente.insumo.save()
            messages.info(request, f"🔄 Stock devuelto del paquete anterior: {cotizacion_anterior.producto.nombre}")

        if recalcular_stock:
            errores_logistica = []
            producto = obj.producto
            
            for componente in producto.componentes.all():
                insumo = componente.insumo
                cantidad_necesaria = componente.cantidad

                if insumo.categoria in ['MOBILIARIO', 'SERVICIO']:
                    usado_ese_dia = ComponenteProducto.objects.filter(
                        producto__cotizacion__fecha_evento=obj.fecha_evento,
                        producto__cotizacion__estado='CONFIRMADA',
                        insumo=insumo
                    ).exclude(
                        producto__cotizacion__id=obj.id 
                    ).aggregate(Sum('cantidad'))['cantidad__sum'] or 0
                    
                    disponible_real = insumo.cantidad_stock - usado_ese_dia
                    
                    if disponible_real < cantidad_necesaria:
                        errores_logistica.append(
                            f"{insumo.nombre} (Stock Total: {insumo.cantidad_stock}, Ocupado hoy: {usado_ese_dia}, Faltan: {cantidad_necesaria - disponible_real})"
                        )

                elif insumo.categoria == 'CONSUMIBLE':
                    if insumo.cantidad_stock < cantidad_necesaria:
                        messages.warning(request, f"⚠️ OJO: {insumo.nombre} quedó en negativo (Stock actual: {insumo.cantidad_stock}).")
                    
                    insumo.cantidad_stock -= cantidad_necesaria
                    insumo.save()

            if errores_logistica:
                messages.error(request, f"⛔ NO SE PUEDE CONFIRMAR: Falta logística para el {obj.fecha_evento}: {', '.join(errores_logistica)}")
                obj.estado = 'BORRADOR'
            else:
                messages.success(request, f"✅ Evento Confirmado: {producto.nombre}. Recursos asignados correctamente.")

        elif cotizacion_anterior and cotizacion_anterior.estado == 'CONFIRMADA' and obj.estado != 'CONFIRMADA':
            for componente in cotizacion_anterior.producto.componentes.all():
                if componente.insumo.categoria == 'CONSUMIBLE':
                    componente.insumo.cantidad_stock += componente.cantidad
                    componente.insumo.save()
            messages.info(request, "ℹ️ Evento cancelado/pospuesto. Consumibles devueltos al stock.")

        super().save_model(request, obj, form, change)

    # --- GUARDADO AUTOMÁTICO DE COBRADOR (EN PAGOS) ---
    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            # Si es un Pago y es nuevo, asignamos el usuario actual
            if isinstance(instance, Pago):
                if not instance.pk:
                    instance.usuario = request.user
            instance.save()
        formset.save_m2m()

    # --- BOTONES DE ACCIÓN ---
    def ver_pdf(self, obj):
        if obj.id:
            try:
                url_pdf = reverse('cotizacion_pdf', args=[obj.id])
                return format_html(
                    '<a href="{}" target="_blank" style="background-color:#17a2b8; color:white; padding:5px 10px; border-radius:5px; text-decoration:none;">'
                    '<i class="fas fa-file-pdf"></i> Ver PDF</a>',
                    url_pdf
                )
            except NoReverseMatch:
                return "Falta URL"
        return "-"
    ver_pdf.short_description = "Cotización"
    ver_pdf.allow_tags = True

    def enviar_email_btn(self, obj):
        if obj.id:
            try:
                url_email = reverse('cotizacion_email', args=[obj.id])
                return format_html(
                    '<a href="{}" style="background-color:#28a745; color:white; padding:5px 10px; border-radius:5px; text-decoration:none;">'
                    '<i class="fas fa-envelope"></i> Enviar</a>',
                    url_email
                )
            except NoReverseMatch:
                return "Falta URL"
        return "-"
    enviar_email_btn.short_description = "Email"
    enviar_email_btn.allow_tags = True

@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ('cotizacion', 'monto', 'metodo', 'usuario', 'fecha_pago')
    list_filter = ('fecha_pago', 'metodo', 'usuario')
    autocomplete_fields = ['cotizacion']
    
    # Para que al crear un pago suelto también se guarde el usuario
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
                '<i class="fas fa-file-pdf"></i> PDF</a>',
                obj.archivo_pdf.url
            )
        return "-"
    ver_pdf.short_description = "Comprobante"
    
    def get_readonly_fields(self, request, obj=None):
        if obj and obj.archivo_xml:
            return self.readonly_fields
        return ('uuid',)