from django.contrib import admin
from django.utils.html import format_html  # <--- Necesario para el HTML del botón
from django.urls import reverse            # <--- Necesario para buscar la URL
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
    # Agregamos 'boton_imprimir' al final de la lista
    list_display = ('cliente', 'producto', 'fecha_evento', 'estado', 'precio_final', 'saldo_pendiente', 'boton_imprimir')
    list_filter = ('estado', 'fecha_evento')
    search_fields = ('cliente__nombre',)
    
    # Opcional: Para que el botón aparezca también al entrar a editar la cotización
    readonly_fields = ('boton_imprimir',)

    # --- LÓGICA DEL BOTÓN ---
    def boton_imprimir(self, obj):
        if obj.id:
            # Buscamos la URL por el nombre que le pusimos en urls.py ('cotizacion_pdf')
            url = reverse('cotizacion_pdf', args=[obj.id])
            # Creamos el botón HTML (usamos la clase 'button' de Django para que se vea bonito)
            return format_html('<a class="button" href="{}" target="_blank" style="background-color: #447e9b; color: white; padding: 3px 10px; border-radius: 5px;">🖨️ Imprimir Recibo</a>', url)
        return "Guardar primero"

    boton_imprimir.short_description = "Acciones" # Título de la columna

@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ('fecha_pago', 'monto', 'metodo', 'cotizacion')
    list_filter = ('fecha_pago', 'metodo')

# Registramos el resto de forma simple
admin.site.register(Insumo)
admin.site.register(Cliente)