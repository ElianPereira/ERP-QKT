"""Admin de la capa de asignación del cotizador de Eventos.

Vive aparte de `comercial/admin.py` (ya de casi 2.000 líneas) y se importa
desde ahí al final, que es lo que Django autodescubre.

Aquí es donde el propietario asigna qué `Producto` compone cada tier de
mobiliario, cada nivel de licor, cada combo de taquiza y cada extra, con su
cantidad por persona. Ninguna de estas pantallas captura un precio: el precio
sale siempre del `Producto` asignado.
"""

from decimal import Decimal

from django.contrib import admin
from django.utils.html import format_html

from .models import (
    ComboTaquiza,
    ComboTaquizaProducto,
    ConfiguracionEventoCotizacion,
    ExtraEvento,
    NivelLicor,
    NivelLicorProducto,
    PaqueteEvento,
    PaqueteEventoProducto,
    TipoMobiliario,
    TipoMobiliarioProducto,
)
from .reglas_eventos import MAX_PERSONAS_EVENTO, MIN_PERSONAS_PAQUETE

MEDIA_CONFIG = {
    'css': {'all': ('css/admin_fix.css', 'css/mobile_fix_v4.css')},
    'js': ('js/tabs_fix.js',),
}

# Aforos de referencia para la columna de costo estimado: el tramo más chico y
# el más grande que se pueden cotizar. Sirven para que el propietario vea de un
# vistazo si una asignación produce un importe razonable antes de publicarla.
AFOROS_MUESTRA = (MIN_PERSONAS_PAQUETE, MAX_PERSONAS_EVENTO)


class AsignacionInlineBase(admin.TabularInline):
    extra = 1
    raw_id_fields = ['producto']
    fields = ('producto', 'cantidad_por_persona', 'cantidad_fija', 'activo', 'orden')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('producto')


class PaqueteEventoProductoInline(AsignacionInlineBase):
    model = PaqueteEventoProducto
    fields = ('concepto', 'producto', 'cantidad_por_persona', 'cantidad_fija', 'activo', 'orden')
    verbose_name = "Producto incluido automáticamente"
    verbose_name_plural = "Incluido automáticamente (el cliente no lo elige ni lo puede quitar)"


class TipoMobiliarioProductoInline(AsignacionInlineBase):
    model = TipoMobiliarioProducto
    verbose_name_plural = "Productos que componen este mobiliario"


class NivelLicorProductoInline(AsignacionInlineBase):
    model = NivelLicorProducto
    verbose_name_plural = "Productos que componen este nivel"


class ComboTaquizaProductoInline(AsignacionInlineBase):
    model = ComboTaquizaProducto
    verbose_name = "Proteína"
    verbose_name_plural = "Proteínas de este combo"


class CatalogoEventoAdminBase(admin.ModelAdmin):
    """Comportamiento común: auditoría automática y aviso de sin configurar."""

    list_display = ('nombre', 'codigo', 'badge_asignacion', 'estimado_muestra', 'activo', 'orden')
    list_filter = ('activo',)
    search_fields = ('nombre', 'codigo')
    readonly_fields = ('created_by', 'updated_by', 'created_at', 'updated_at')
    relacion_productos = 'productos'

    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    def _asignaciones(self, obj):
        return list(getattr(obj, self.relacion_productos).filter(activo=True).select_related('producto'))

    @admin.display(description="Asignación")
    def badge_asignacion(self, obj):
        cuantos = len(self._asignaciones(obj))
        if cuantos == 0:
            return format_html(
                '<span style="color:#c0392b;font-weight:600;">⚠ Sin productos asignados</span>'
            )
        return format_html('<span style="color:#27ae60;">✓ {} producto(s)</span>', cuantos)

    @admin.display(description="Costo estimado (IVA incl.)")
    def estimado_muestra(self, obj):
        """Cuánto costaría esta opción en el aforo mínimo y en el máximo.

        Es una referencia para revisar la asignación, no el precio que ve el
        cliente: ese sale de `Cotizacion.calcular_totales()`, que convierte el
        IVA una sola vez sobre el subtotal completo de la cotización.
        """
        from core_erp import impuestos

        asignaciones = self._asignaciones(obj)
        if not asignaciones:
            return "—"
        partes = []
        for personas in AFOROS_MUESTRA:
            base = sum(
                (Decimal(str(a.producto.sugerencia_precio())) * a.cantidad_para(personas)
                 for a in asignaciones),
                Decimal('0.00'),
            )
            partes.append(f"{personas} pax: ${impuestos.con_iva(base):,.2f}")
        return " · ".join(partes)


@admin.register(PaqueteEvento)
class PaqueteEventoAdmin(CatalogoEventoAdminBase):
    inlines = [PaqueteEventoProductoInline]
    relacion_productos = 'productos_incluidos'
    list_display = ('nombre', 'codigo', 'badge_pasos', 'badge_asignacion',
                    'estimado_muestra', 'activo', 'orden')
    fieldsets = (
        (None, {'fields': ('codigo', 'nombre', 'descripcion_corta', 'activo', 'orden')}),
        ('Qué pasos ve el cliente', {
            'fields': ('requiere_mobiliario', 'permite_licores_opcional',
                       'requiere_taquiza', 'permite_extras'),
            'description': (
                'Estas casillas deciden qué <strong>pantallas</strong> se le muestran, no qué cuesta. '
                'Lo que el paquete incluye sin que el cliente lo elija (refrescos, servicio de mesa) '
                'va abajo, en "Incluido automáticamente".<br>'
                'El arrendamiento del espacio y las horas extra <strong>no se capturan aquí</strong>: '
                'salen del Producto marcado con rol "Base — Evento" / "Hora extra", que comparten '
                'las dos modalidades.'
            ),
        }),
        ('Auditoría', {'fields': ('created_by', 'updated_by', 'created_at', 'updated_at'),
                       'classes': ('collapse',)}),
    )

    @admin.display(description="Pasos")
    def badge_pasos(self, obj):
        pasos = []
        if obj.requiere_mobiliario:
            pasos.append('Mobiliario')
        if obj.permite_licores_opcional:
            pasos.append('Licores')
        if obj.requiere_taquiza:
            pasos.append('Taquiza')
        if obj.permite_extras:
            pasos.append('Extras')
        return ' · '.join(pasos) or '—'


@admin.register(TipoMobiliario)
class TipoMobiliarioAdmin(CatalogoEventoAdminBase):
    inlines = [TipoMobiliarioProductoInline]


@admin.register(NivelLicor)
class NivelLicorAdmin(CatalogoEventoAdminBase):
    inlines = [NivelLicorProductoInline]


@admin.register(ComboTaquiza)
class ComboTaquizaAdmin(CatalogoEventoAdminBase):
    inlines = [ComboTaquizaProductoInline]


@admin.register(ExtraEvento)
class ExtraEventoAdmin(admin.ModelAdmin):
    """Cabecera y asignación en la misma fila: un extra es un solo producto."""

    list_display = ('nombre', 'codigo', 'producto', 'cantidad_resumen', 'activo', 'orden')
    list_filter = ('activo',)
    search_fields = ('nombre', 'codigo')
    raw_id_fields = ['producto']
    readonly_fields = ('created_by', 'updated_by', 'created_at', 'updated_at')
    fieldsets = (
        (None, {'fields': ('codigo', 'nombre', 'descripcion_corta', 'activo', 'orden')}),
        ('Producto y cantidad', {
            'fields': ('producto', 'cantidad_por_persona', 'cantidad_fija'),
            'description': 'Captura <strong>una sola</strong> de las dos cantidades.',
        }),
        ('Informativo para el cliente', {'fields': ('capacidad_maxima_simultanea',)}),
        ('Auditoría', {'fields': ('created_by', 'updated_by', 'created_at', 'updated_at'),
                       'classes': ('collapse',)}),
    )

    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Cantidad")
    def cantidad_resumen(self, obj):
        if obj.cantidad_por_persona is not None:
            return f"{obj.cantidad_por_persona} por persona"
        return f"{obj.cantidad_fija} fija"


@admin.register(ConfiguracionEventoCotizacion)
class ConfiguracionEventoCotizacionAdmin(admin.ModelAdmin):
    """Solo lectura: la selección nace del cotizador, junto con sus líneas.

    Editarla a mano dejaría la configuración diciendo una cosa y los
    `ItemCotizacion` ya cobrados diciendo otra, sin nada que las reconcilie.
    """

    list_display = ('cotizacion', 'modalidad', 'paquete', 'tipo_mobiliario',
                    'incluir_licores', 'combo_taquiza', 'created_at')
    list_filter = ('modalidad', 'paquete', 'tipo_mobiliario', 'incluir_licores')
    search_fields = ('cotizacion__id', 'cotizacion__nombre_evento')
    raw_id_fields = ['cotizacion']

    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields] + ['extras']
