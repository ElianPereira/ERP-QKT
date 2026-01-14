from django.contrib import admin
from .models import Insumo, Producto, ComponenteProducto, Cliente, Cotizacion, Pago

# Esto permite agregar insumos DENTRO de la pantalla de crear producto
class ComponenteInline(admin.TabularInline):
    model = ComponenteProducto
    extra = 1  # Cuántas filas vacías mostrar por defecto

@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    inlines = [ComponenteInline]
    list_display = ('nombre', 'calcular_costo', 'sugerencia_precio')

@admin.register(Cotizacion)
class CotizacionAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'producto', 'fecha_evento', 'estado', 'precio_final', 'saldo_pendiente')
    list_filter = ('estado', 'fecha_evento')
    search_fields = ('cliente__nombre',)

@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ('fecha_pago', 'monto', 'metodo', 'cotizacion')
    list_filter = ('fecha_pago', 'metodo')

# Registramos el resto de forma simple
admin.site.register(Insumo)
admin.site.register(Cliente)