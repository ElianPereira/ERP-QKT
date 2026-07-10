from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from .models import (
    ConfiguracionCatalogo, BadgeServicio, SeccionCatalogo,
    CaracteristicaSeccion, TarjetaCatalogo, PaqueteCatalogo,
    ItemPaqueteCatalogo, DescuentoTarjeta,
    QuienesSomos, EstadisticaQuienesSomos, PasoProceso,
    SeccionBadge, OcasionCard, GaleriaSeccion, GaleriaSeccionBullet, GaleriaSeccionFoto,
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


@admin.register(QuienesSomos)
class QuienesSomosAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not QuienesSomos.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EstadisticaQuienesSomos)
class EstadisticaQuienesSomosAdmin(admin.ModelAdmin):
    list_display = ('orden', 'valor', 'etiqueta')
    list_editable = ('valor', 'etiqueta')
    ordering = ['orden']


@admin.register(PasoProceso)
class PasoProcesoAdmin(admin.ModelAdmin):
    list_display = ('orden', 'numero', 'titulo')
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


class SeccionBadgeInline(admin.TabularInline):
    model = SeccionBadge
    extra = 1


class OcasionCardInline(admin.TabularInline):
    model = OcasionCard
    extra = 0


class GaleriaSeccionInline(admin.StackedInline):
    model = GaleriaSeccion
    extra = 0
    max_num = 1
    fields = ('eyebrow', 'titulo', 'titulo_enfasis', 'descripcion')
    show_change_link = True


@admin.register(SeccionCatalogo)
class SeccionCatalogoAdmin(admin.ModelAdmin):
    list_display = ('numero', 'titulo', 'orden', 'activa')
    list_editable = ('orden', 'activa')
    fieldsets = (
        (None, {'fields': ('numero', 'slug', 'categoria', 'titulo', 'titulo_enfasis', 'descripcion', 'orden', 'activa')}),
        ('Imagen de sección (página de detalle)', {'fields': ('imagen_hero', 'imagen_hero_caption')}),
        ('Nota al pie', {'fields': ('nota_pie',)}),
        ('Portada de capítulo (página oscura antes del detalle)', {
            'fields': ('nombre_corto', 'descripcion_cover', 'imagen_cover'),
            'description': 'Si "Nombre corto" está vacío, esta sección no tendrá portada de capítulo.',
        }),
    )
    inlines = [SeccionBadgeInline, CaracteristicaInline, TarjetaInline, OcasionCardInline, GaleriaSeccionInline]
    prepopulated_fields = {'slug': ('titulo',)}

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        # Los inlines (badges, características, ocasiones) no tienen su
        # propio actualizado_en; se toca el padre para que el caché del
        # PDF se invalide también cuando solo cambia una fila hija.
        form.instance.save()


class GaleriaBulletInline(admin.TabularInline):
    model = GaleriaSeccionBullet
    extra = 1


class GaleriaFotoInline(admin.TabularInline):
    model = GaleriaSeccionFoto
    extra = 1


@admin.register(GaleriaSeccion)
class GaleriaSeccionAdmin(admin.ModelAdmin):
    list_display = ('seccion', 'eyebrow', 'titulo')
    inlines = [GaleriaBulletInline, GaleriaFotoInline]

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        form.instance.save()


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
            return mark_safe('<span style="color:#999;">—</span>')
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
    list_display = ('nombre', 'producto', 'proveedor', 'precio_badge', 'orden', 'activo')
    list_editable = ('orden', 'activo')
    raw_id_fields = ['producto']
    inlines = [ItemPaqueteInline]

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        form.instance.save()

    def precio_badge(self, obj):
        precio = obj.get_precio()
        if precio is None:
            return mark_safe('<span style="color:#999;">Precio por confirmar</span>')
        if obj.producto:
            return format_html(
                '${} <span style="background:#1565C0;color:white;padding:2px 7px;'
                'border-radius:10px;font-size:9px;font-weight:600;margin-left:4px;">ERP</span>',
                f'{precio:,.2f}'
            )
        return format_html(
            '${} <span style="background:#607D8B;color:white;padding:2px 7px;'
            'border-radius:10px;font-size:9px;font-weight:600;margin-left:4px;">MANUAL</span>',
            f'{precio:,.2f}'
        )
    precio_badge.short_description = "Precio"
