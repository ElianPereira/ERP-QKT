from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.contrib import messages
from django.db.models import Sum
from .models import Insumo, Producto, ComponenteProducto, Cliente, Cotizacion, Pago

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

# --- 3. COTIZACIONES (Cerebro del ERP) ---
@admin.register(Cotizacion)
class CotizacionAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'producto', 'fecha_evento', 'estado', 'precio_final', 'acciones')
    list_filter = ('estado', 'fecha_evento')
    search_fields = ('cliente__nombre',)
    
    def save_model(self, request, obj, form, change):
        estado_anterior = None
        if obj.pk:
            estado_anterior = Cotizacion.objects.get(pk=obj.pk).estado

        # === CASO A: CONFIRMANDO VENTA ===
        if obj.estado == 'CONFIRMADA' and estado_anterior != 'CONFIRMADA':
            producto = obj.producto
            errores_logistica = [] # Lista de problemas (falta silla o falta mesero)
            
            for componente in producto.componentes.all():
                insumo = componente.insumo
                cantidad_necesaria = componente.cantidad

                # LÓGICA 1: RECURSOS REUTILIZABLES (Mobiliario Y Personal)
                # Ambos dependen de la FECHA. No se gastan, se "ocupan".
                if insumo.categoria in ['MOBILIARIO', 'SERVICIO']:
                    
                    # Buscamos eventos CONFIRMADOS en la MISMA FECHA
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
                        errores_logistica.append(f"{insumo.nombre} (Tienes: {insumo.cantidad_stock}, Ocupados hoy: {usado_ese_dia}, Faltan: {cantidad_necesaria - disponible_real})")

                # LÓGICA 2: CONSUMIBLES (Se restan para siempre)
                elif insumo.categoria == 'CONSUMIBLE':
                    if insumo.cantidad_stock < cantidad_necesaria:
                        messages.warning(request, f"⚠️ OJO: {insumo.nombre} quedó en negativo en el almacén.")
                    
                    insumo.cantidad_stock -= cantidad_necesaria
                    insumo.save()

            # Si hubo errores de logística (Personal o Muebles), DETENEMOS TODO
            if errores_logistica:
                messages.error(request, f"⛔ NO SE PUEDE CONFIRMAR: Falta logística para el {obj.fecha_evento}: {', '.join(errores_logistica)}")
                obj.estado = 'BORRADOR' 
            else:
                messages.success(request, "✅ Evento Confirmado. Recursos asignados y stock actualizado.")

        # === CASO B: CANCELANDO VENTA ===
        elif obj.estado == 'CANCELADA' and estado_anterior == 'CONFIRMADA':
            # Solo devolvemos los consumibles. 
            # El personal y muebles se liberan solos al cambiar el estado (ya no salen en la búsqueda de 'CONFIRMADA').
            for componente in obj.producto.componentes.all():
                if componente.insumo.categoria == 'CONSUMIBLE':
                    componente.insumo.cantidad_stock += componente.cantidad
                    componente.insumo.save()
            messages.info(request, "ℹ️ Evento cancelado. Consumibles devueltos. Personal y Mobiliario liberados.")

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