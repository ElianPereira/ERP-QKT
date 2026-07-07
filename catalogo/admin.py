from django.contrib import admin
from django.utils.html import format_html
from .models import (
    ConfiguracionCatalogo, BadgeServicio, SeccionCatalogo,
    CaracteristicaSeccion, TarjetaCatalogo, PaqueteCatalogo,
    ItemPaqueteCatalogo, DescuentoTarjeta,
)


@admin.register(ConfiguracionCatalogo)
class ConfiguracionCatalogoAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not ConfiguracionCatalogo.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BadgeServicio)
class BadgeServicioAdmin(admin.ModelAdmin):
    list_display = ('texto', 'orden')
    ordering = ['orden']


class CaracteristicaInline(admin.TabularInline):
    model = CaracteristicaSeccion
    extra = 1


class TarjetaInline(admin.TabularInline):
    model = TarjetaCatalogo
    extra = 0
    fields = ('orden', 'producto', 'titulo', 'mostrar_precio', 'activa')
    raw_id_fields = ['producto']
    show_change_link = True


@admin.register(SeccionCatalogo)
class SeccionCatalogoAdmin(admin.ModelAdmin):
    list_display = ('numero', 'titulo', 'orden', 'activa')
    list_editable = ('orden', 'activa')
    inlines = [CaracteristicaInline, TarjetaInline]
    prepopulated_fields = {'slug': ('titulo',)}


class DescuentoInline(admin.StackedInline):
    model = DescuentoTarjeta
    extra = 0


@admin.register(TarjetaCatalogo)
class TarjetaCatalogoAdmin(admin.ModelAdmin):
    list_display = ('get_titulo', 'seccion', 'producto', 'precio_badge', 'orden', 'activa')
    list_filter = ('seccion', 'activa', 'mostrar_precio')
    raw_id_fields = ['producto']
    inlines = [DescuentoInline]
    search_fields = ('titulo', 'producto__nombre')

    def precio_badge(self, obj):
        precio = obj.get_precio()
        if precio is None:
            return format_html('<span style="color:#999;">—</span>')
        vigente = hasattr(obj, 'descuento') and obj.descuento.esta_vigente()
        if vigente:
            return format_html(
                '<span style="text-decoration:line-through;color:#999;">${}</span> '
                '<strong style="color:#2E7D32;">${}</strong> '
                '<span style="background:#2E7D32;color:white;padding:2px 6px;'
                'border-radius:8px;font-size:10px;">DESCUENTO ACTIVO</span>',
                f'{obj.descuento.precio_regular:,.2f}',
                f'{obj.descuento.precio_descuento:,.2f}',
            )
        return format_html('${}', f'{precio:,.2f}')
    precio_badge.short_description = "Precio"


class ItemPaqueteInline(admin.TabularInline):
    model = ItemPaqueteCatalogo
    extra = 1


@admin.register(PaqueteCatalogo)
class PaqueteCatalogoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'proveedor', 'precio_venta_fijo', 'orden', 'activo')
    list_editable = ('orden', 'activo')
    inlines = [ItemPaqueteInline]
