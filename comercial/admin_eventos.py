"""Admin del catálogo del cotizador de Eventos.

Vive aparte de `comercial/admin.py` (ya de casi 2.000 líneas) y se importa
desde ahí al final, que es lo que Django autodescubre.

**Una sola entrada en el menú.** Antes eran cinco (paquetes, tipos de
mobiliario, niveles de licor, combos de taquiza, extras) para un catálogo que
el propietario ya administra desde Productos; ahora es un único modelo con un
campo `tipo`. Y la asignación producto→opción se captura desde los dos lados:
desde aquí (qué compone esta opción) y desde la pestaña "Cotizador Web" del
propio Producto (en qué opciones entra este producto), que es donde el
propietario ya trabaja.

Ninguna de estas pantallas captura un precio: el precio sale siempre del
`Producto` asignado.
"""

from decimal import Decimal

from django.contrib import admin
from django.utils.html import format_html

from .models import CatalogoEvento, CatalogoEventoProducto, ConfiguracionEventoCotizacion
from .reglas_eventos import MAX_PERSONAS_EVENTO, MIN_PERSONAS_PAQUETE

MEDIA_CONFIG = {
    'css': {'all': ('css/admin_fix.css', 'css/mobile_fix_v4.css')},
    'js': ('js/tabs_fix.js',),
}

# Aforos de referencia para la columna de costo estimado: el tramo más chico y
# el más grande que se pueden cotizar. Sirven para que el propietario vea de un
# vistazo si una asignación produce un importe razonable antes de publicarla.
AFOROS_MUESTRA = (MIN_PERSONAS_PAQUETE, MAX_PERSONAS_EVENTO)


class CatalogoEventoProductoInline(admin.TabularInline):
    """Qué productos componen esta opción, visto desde la opción."""

    model = CatalogoEventoProducto
    extra = 1
    raw_id_fields = ['producto']
    fields = ('producto', 'cantidad_por_persona', 'cantidad_fija', 'concepto', 'activo', 'orden')
    verbose_name = "Producto de esta opción"
    verbose_name_plural = "Productos que componen esta opción"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('producto')


class ProductoEnCatalogoEventoInline(admin.TabularInline):
    """La misma tabla, vista desde el Producto: en qué opciones entra.

    Es el punto de la fusión: el propietario da de alta un producto y ahí mismo
    dice a qué paquete, mobiliario, licor, taquiza o extra pertenece, sin salir
    de la pantalla donde ya está trabajando.
    """

    model = CatalogoEventoProducto
    fk_name = 'producto'
    extra = 0
    fields = ('opcion', 'cantidad_por_persona', 'cantidad_fija', 'concepto', 'activo', 'orden')
    autocomplete_fields = ['opcion']
    verbose_name = "Opción del cotizador de Eventos"
    verbose_name_plural = (
        "Cotizador de Eventos — en qué opciones entra este producto "
        "(captura UNA de las dos cantidades)"
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('opcion')


@admin.register(CatalogoEvento)
class CatalogoEventoAdmin(admin.ModelAdmin):
    inlines = [CatalogoEventoProductoInline]
    list_display = ('nombre', 'tipo', 'codigo', 'badge_pasos', 'badge_asignacion',
                    'estimado_muestra', 'activo', 'orden')
    list_filter = ('tipo', 'activo')
    search_fields = ('nombre', 'codigo')
    list_editable = ('activo', 'orden')
    readonly_fields = ('created_by', 'updated_by', 'created_at', 'updated_at')
    fieldsets = (
        (None, {
            'fields': ('tipo', 'codigo', 'nombre', 'descripcion_corta', 'activo', 'orden'),
            'description': (
                'El <strong>tipo</strong> decide en qué paso del cotizador aparece esta '
                'opción. Los productos que la componen —y su cantidad— van abajo.'
            ),
        }),
        ('Solo si el tipo es Paquete: qué pasos ve el cliente', {
            'fields': ('requiere_mobiliario', 'permite_licores_opcional',
                       'requiere_taquiza', 'permite_extras'),
            'description': (
                'Estas casillas deciden qué <strong>pantallas</strong> se le muestran, no qué cuesta. '
                'Lo que el paquete incluye sin que el cliente lo elija (refrescos, servicio de mesa) '
                'va abajo, como productos de esta opción, con su "Concepto".<br>'
                'El arrendamiento del espacio y las horas extra <strong>no se capturan aquí</strong>: '
                'salen del Producto marcado con rol "Base — Evento" / "Hora extra", que comparten '
                'las dos modalidades.'
            ),
        }),
        ('Solo si el tipo es Extra', {'fields': ('capacidad_maxima_simultanea',)}),
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

    def _asignaciones(self, obj):
        return list(obj.productos.filter(activo=True).select_related('producto'))

    @admin.display(description="Pasos", ordering='tipo')
    def badge_pasos(self, obj):
        if obj.tipo != CatalogoEvento.TIPO_PAQUETE:
            return '—'
        pasos = [etiqueta for bandera, etiqueta in (
            ('requiere_mobiliario', 'Mobiliario'),
            ('permite_licores_opcional', 'Licores'),
            ('requiere_taquiza', 'Taquiza'),
            ('permite_extras', 'Extras'),
        ) if getattr(obj, bandera)]
        return ' · '.join(pasos) or '—'

    @admin.display(description="Asignación")
    def badge_asignacion(self, obj):
        cuantos = len(self._asignaciones(obj))
        if cuantos == 0:
            # Un paquete sin productos propios es legítimo (Esencial solo lleva
            # mobiliario, que se elige aparte); el resto sin productos cobraría
            # $0.00 y el cotizador ni lo ofrece.
            if obj.tipo == CatalogoEvento.TIPO_PAQUETE:
                return format_html('<span style="color:#8a8780;">Sin productos propios</span>')
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


@admin.register(ConfiguracionEventoCotizacion)
class ConfiguracionEventoCotizacionAdmin(admin.ModelAdmin):
    """Solo lectura: la selección nace del cotizador, junto con sus líneas.

    Editarla a mano dejaría la configuración diciendo una cosa y los
    `ItemCotizacion` ya cobrados diciendo otra, sin nada que las reconcilie.
    """

    list_display = ('cotizacion', 'modalidad', 'paquete', 'tipo_mobiliario',
                    'incluir_licores', 'combo_taquiza', 'created_at')
    list_filter = ('modalidad', 'incluir_licores')
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
        return [f.name for f in self.model._meta.fields] + ['extras', 'niveles_licor']
