from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from .models import Insumo, Producto, ComponenteProducto, Cliente, Cotizacion, Pago

# Esto permite agregar insumos DENTRO de la pantalla de crear producto
class ComponenteInline(admin.TabularInline):
    model = ComponenteProducto
    extra = 1

@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    inlines = [ComponenteInline]
    list_display = ('nombre', 'calcular_costo', 'sugerencia_precio')

@admin.register(Cotizacion)
class CotizacionAdmin(admin.ModelAdmin):
    # Mostramos la columna 'acciones'
    list_display = ('cliente', 'producto', 'fecha_evento', 'estado', 'precio_final', 'acciones')
    list_filter = ('estado', 'fecha_evento')
    search_fields = ('cliente__nombre',)
    
    # LÓGICA DE LOS BOTONES CORREGIDA
    def acciones(self, obj):
        if obj.id:
            url_pdf = reverse('cotizacion_pdf', args=[obj.id])
            url_email = reverse('cotizacion_email', args=[obj.id])
            
            # CORRECCIÓN: Pasamos el HTML como plantilla y las variables aparte
            return format_html(
                '<a class="button" href="{}" target="_blank" style="background:#447e9b; color:white; padding:4px 8px; border-radius:4px; margin-right:5px; text-decoration:none;">🖨️ PDF</a>'
                '<a class="button" href="{}" style="background:#28a745; color:white; padding:4px 8px; border-radius:4px; text-decoration:none;">📧 Enviar</a>',
                url_pdf,   # Variable 1 va al primer {}
                url_email  # Variable 2 va al segundo {}
            )
        return "Guarda primero"
    
    acciones.allow_tags = True
    acciones.short_description = "Acciones"

@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ('fecha_pago', 'monto', 'metodo', 'cotizacion')
    list_filter = ('fecha_pago', 'metodo')

# Registramos el resto
admin.site.register(Insumo)
admin.site.register(Cliente)