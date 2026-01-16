from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.contrib import messages
from django.db.models import Sum
from .models import Insumo, Producto, ComponenteProducto, Cliente, Cotizacion, Pago, Gasto

# --- 1. INSUMOS ---
@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'categoria', 'cantidad_stock', 'unidad_medida')
    list_editable = ('cantidad_stock', 'categoria') 
    list_filter = ('categoria',)
    search_fields = ('nombre',)

# --- 2. PRODUCTOS ---
class ComponenteInline(admin.TabularInline):
    model = ComponenteProducto
    extra = 1

@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    inlines = [ComponenteInline]
    list_display = ('nombre', 'calcular_costo', 'sugerencia_precio')

# --- 3. CLIENTES (¡AQUÍ ESTÁ EL QUE FALTABA!) ---
@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'tipo_persona', 'rfc', 'email', 'telefono')
    list_filter = ('tipo_persona',)
    search_fields = ('nombre', 'rfc')
    fieldsets = (
        ('Datos Generales', {
            'fields': ('nombre', 'email', 'telefono')
        }),
        ('Datos Fiscales', {
            'fields': ('tipo_persona', 'rfc'),
            'description': 'Selecciona "Moral" para activar retenciones de ISR en las cotizaciones.'
        }),
    )

# --- 4. COTIZACIONES (CON IMPUESTOS) ---
@admin.register(Cotizacion)
class CotizacionAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'producto', 'fecha_evento', 'estado', 'subtotal', 'precio_final', 'acciones')
    list_filter = ('estado', 'requiere_factura', 'fecha_evento')
    search_fields = ('cliente__nombre',)
    
    # Formulario organizado
    fieldsets = (
        ('Datos del Evento', {
            'fields': ('cliente', 'producto', 'fecha_evento', 'estado')
        }),
        ('Finanzas', {
            'fields': ('subtotal', 'requiere_factura') 
        }),
        ('Cálculo Fiscal (Automático)', {
            'fields': ('iva', 'retencion_isr', 'retencion_iva', 'precio_final') 
        }),
    )
    
    readonly_fields = ('iva', 'retencion_isr', 'retencion_iva', 'precio_final')

    def save_model(self, request, obj, form, change):
        # 1. Recuperar versión anterior
        cotizacion_anterior = None
        if change:
            try:
                cotizacion_anterior = Cotizacion.objects.get(pk=obj.pk)
            except Cotizacion.DoesNotExist:
                pass

        # === LÓGICA DE INVENTARIO ===
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
                    otros_eventos = Cotizacion.objects.filter(
                        fecha_evento=obj.fecha_evento,
                        estado='CONFIRMADA'
                    ).exclude(id=obj.id)
                    
                    usado_ese_dia = 0
                    for evento in otros_eventos:
                        uso = evento.producto.componentes.filter(insumo=insumo).aggregate(Sum('cantidad'))['cantidad__sum']
                        if uso:
                            usado_ese_dia += uso
                    
                    disponible_real = insumo.cantidad_stock - usado_ese_dia
                    
                    if disponible_real < cantidad_necesaria:
                        errores_logistica.append(f"{insumo.nombre} (Stock: {insumo.cantidad_stock}, Usado hoy: {usado_ese_dia}, Faltan: {cantidad_necesaria - disponible_real})")

                elif insumo.categoria == 'CONSUMIBLE':
                    if insumo.cantidad_stock < cantidad_necesaria:
                        messages.warning(request, f"⚠️ OJO: {insumo.nombre} quedó en negativo.")
                    
                    insumo.cantidad_stock -= cantidad_necesaria
                    insumo.save()

            if errores_logistica:
                messages.error(request, f"⛔ NO SE PUEDE CONFIRMAR: Falta logística para el {obj.fecha_evento}: {', '.join(errores_logistica)}")
                obj.estado = 'BORRADOR' 
            else:
                messages.success(request, f"✅ Evento Confirmado: {producto.nombre}. Recursos asignados.")

        elif cotizacion_anterior and cotizacion_anterior.estado == 'CONFIRMADA' and obj.estado != 'CONFIRMADA':
            for componente in cotizacion_anterior.producto.componentes.all():
                if componente.insumo.categoria == 'CONSUMIBLE':
                    componente.insumo.cantidad_stock += componente.cantidad
                    componente.insumo.save()
            messages.info(request, "ℹ️ Evento cancelado. Consumibles devueltos.")

        super().save_model(request, obj, form, change)

    def acciones(self, obj):
        if obj.id:
            url_pdf = reverse('cotizacion_pdf', args=[obj.id])
            url_email = reverse('cotizacion_email', args=[obj.id])
            return format_html(
                '<a class="button" href="{}" target="_blank" style="background:#447e9b; color:white; padding:4px 8px; border-radius:4px; margin-right:5px; text-decoration:none;">🖨️ PDF</a>'
                '<a class="button" href="{}" style="background:#28a745; color:white; padding:4px 8px; border-radius:4px; text-decoration:none;">📧 Enviar</a>',
                url_pdf, url_email
            )
        return "Guarda primero"
    acciones.allow_tags = True

# --- 5. PAGOS ---
@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ('cotizacion', 'monto', 'metodo', 'fecha_pago')
    list_filter = ('fecha_pago', 'metodo')

# --- 6. GASTOS ---
@admin.register(Gasto)
class GastoAdmin(admin.ModelAdmin):
    list_display = ('fecha_gasto', 'proveedor', 'descripcion', 'monto', 'categoria', 'tiene_xml')
    list_filter = ('fecha_gasto', 'categoria')
    search_fields = ('descripcion', 'proveedor', 'uuid')
    date_hierarchy = 'fecha_gasto'
    
    readonly_fields = ('uuid', 'proveedor', 'monto', 'fecha_gasto', 'descripcion')

    def tiene_xml(self, obj):
        return "✅ Sí" if obj.archivo_xml else "📝 Manual"
    tiene_xml.short_description = "Tipo Registro"
    
    def get_readonly_fields(self, request, obj=None):
        if obj and obj.archivo_xml:
            return self.readonly_fields
        return ('uuid', 'proveedor', 'fecha_gasto', 'descripcion', 'monto') if obj and obj.archivo_xml else ('uuid',)