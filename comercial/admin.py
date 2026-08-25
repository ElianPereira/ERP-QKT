import logging
import secrets
from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.db import models as db_models
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import NoReverseMatch, path, reverse
from django.utils import timezone
from django.utils.html import format_html, mark_safe

from core_erp.admin_utils import confirmar_accion_destructiva
from core_erp.descargas import url_descarga

from .choices import PosicionLanding
from .models import (
    AsignacionEspacio,
    AsignacionPersonal,
    Cliente,
    ComponenteProducto,
    Compra,
    ConstanteSistema,
    Cotizacion,
    Descuento,
    DescuentoAplicado,
    Espacio,
    EspacioLanding,
    Gasto,
    GuiaTipoServicio,
    ImagenLanding,
    Insumo,
    ItemCotizacion,
    MovimientoInventario,
    OpenpayTransaccion,
    Pago,
    ParcialidadPago,
    PlanPago,
    PlantillaBarra,
    PortalCliente,
    PreguntaFrecuente,
    Producto,
    ProductoComponente,
    Proveedor,
    RecetaSubProducto,
    RecordatorioPago,
    SubProducto,
    Temporada,
    TestimonioLanding,
    TipoEvento,
)
from .services import CalculadoraBarraService
from .widgets import TimeSlotWidget

logger = logging.getLogger(__name__)

# Estilo estandarizado para botones

BTN = '<a href="{url}" {target} class="btn btn-sm" style="background:{bg}; color:{fg}; padding:4px 10px; border-radius:4px; font-size:11px; font-weight:600; text-decoration:none; display:inline-block; font-family:IBM Plex Sans,sans-serif;" {extra}>{label}</a>'

# Variante compacta de BTN: misma estructura, para columnas con muchos
# botones en una sola fila (ver CotizacionAdmin.acciones_display).
BTN_SM = '<a href="{url}" {target} class="btn btn-sm qkt-accion-btn" style="background:{bg}; color:{fg}; padding:2px 6px; border-radius:3px; font-size:10px; font-weight:600; text-decoration:none; display:inline-block; white-space:nowrap; font-family:IBM Plex Sans,sans-serif;" {extra}>{label}</a>'

# Colores estándar para usar con BTN:
# Verde primario (acciones principales): bg='#2E7D32', fg='white'
# Amarillo marca (documentos especiales): bg='#F5C518', fg='#333'
# Rojo (peligro/cancelar):               bg='#e74c3c', fg='white'
# Gris (neutral/inactivo):               bg='#95a5a6', fg='white'

MEDIA_CONFIG = {
    'css': { 'all': ('css/admin_fix.css', 'css/mobile_fix.css') },
    'js': ('js/tabs_fix.js',)
}

@admin.register(ConstanteSistema)
class ConstanteSistemaAdmin(admin.ModelAdmin):
    list_display = ('clave', 'valor', 'descripcion')
    list_editable = ('valor',)


# ==========================================
# PROVEEDORES
# ==========================================
@admin.register(Proveedor)
class ProveedorAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'rfc', 'contacto', 'telefono', 'email', 'total_insumos', 'activo')
    list_filter = ('activo',)
    list_editable = ('activo',)
    search_fields = ('nombre', 'rfc', 'contacto', 'telefono', 'email')
    list_per_page = 25
    fieldsets = (
        (None, {'fields': ('nombre', 'rfc', 'contacto', 'telefono', 'email')}),
        ('Información Adicional', {'fields': ('notas', 'activo')}),
    )

    def total_insumos(self, obj):
        count = obj.insumo_set.count()
        if count > 0:
            return format_html(
                '<span style="background:#27ae60; color:white; padding:2px 8px; border-radius:4px;">{} insumos</span>',
                count
            )
        return mark_safe('<span style="color:#999;">Sin insumos</span>')
    total_insumos.short_description = "Insumos Vinculados"

    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']


# ==========================================
# INSUMOS
# ==========================================
@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'presentacion', 'categoria', 'proveedor', 'costo_unitario', 'factor_rendimiento', 'cantidad_stock', 'badge_stock')
    list_editable = ('costo_unitario', 'factor_rendimiento', 'categoria')
    list_filter = ('categoria', 'proveedor')
    search_fields = ('nombre', 'proveedor__nombre', 'presentacion')
    autocomplete_fields = ['proveedor']
    list_per_page = 20
    fieldsets = (
        (None, {'fields': ('nombre', 'presentacion', 'categoria', 'unidad_medida')}),
        ('Costos y Stock', {'fields': ('costo_unitario', 'factor_rendimiento', 'cantidad_stock', 'stock_minimo')}),
        ('Proveedor', {'fields': ('proveedor',)}),
        ('Opciones', {'fields': ('crear_como_subproducto',), 'classes': ('collapse',)}),
    )

    def badge_stock(self, obj):
        if obj.stock_minimo > 0 and obj.cantidad_stock < obj.stock_minimo:
            return mark_safe('<span style="background:#e74c3c; color:white; padding:2px 8px; border-radius:4px; font-size:11px;">BAJO</span>')
        elif obj.cantidad_stock > 0:
            return mark_safe('<span style="background:#27ae60; color:white; padding:2px 8px; border-radius:4px; font-size:11px;">OK</span>')
        return mark_safe('<span style="background:#95a5a6; color:white; padding:2px 8px; border-radius:4px; font-size:11px;">Sin stock</span>')
    badge_stock.short_description = "Estado"

    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']


# ==========================================
# INVENTARIOS (antes MovimientoInventario)
# ==========================================
@admin.register(MovimientoInventario)
class MovimientoInventarioAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'insumo', 'tipo_badge', 'cantidad', 'stock_anterior', 'stock_posterior', 'nota_corta', 'created_by')
    list_filter = ('tipo', 'created_at', 'insumo')
    search_fields = ('insumo__nombre', 'nota')
    raw_id_fields = ['insumo', 'compra', 'cotizacion']
    readonly_fields = ('stock_anterior', 'stock_posterior', 'created_at', 'created_by')
    list_per_page = 30
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Movimiento', {'fields': ('insumo', 'tipo', 'cantidad')}),
        ('Referencias', {'fields': ('compra', 'cotizacion', 'nota'), 'classes': ('collapse',)}),
        ('Auditoría', {'fields': ('stock_anterior', 'stock_posterior', 'created_by', 'created_at')}),
    )

    def tipo_badge(self, obj):
        colores = {
            'ENTRADA': '#27ae60', 'SALIDA': '#e74c3c',
            'AJUSTE_POS': '#3498db', 'AJUSTE_NEG': '#e67e22', 'DEVOLUCION': '#9b59b6',
        }
        color = colores.get(obj.tipo, '#666')
        signo = '+' if obj.tipo in ('ENTRADA', 'AJUSTE_POS') else '-'
        return format_html(
            '<span style="background:{}; color:white; padding:2px 8px; border-radius:4px; font-size:11px;">{} {}</span>',
            color, signo, obj.get_tipo_display()
        )
    tipo_badge.short_description = "Tipo"
    tipo_badge.admin_order_field = 'tipo'

    def nota_corta(self, obj):
        return (obj.nota[:50] + '...') if obj.nota and len(obj.nota) > 50 else (obj.nota or '-')
    nota_corta.short_description = "Nota"

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.full_clean()
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        if obj:
            return False
        return True

    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']


# ==========================================
# PLANTILLA DE BARRA
# ==========================================
@admin.register(PlantillaBarra)
class PlantillaBarraAdmin(admin.ModelAdmin):
    list_display = ('categoria_display', 'grupo_display', 'insumo_nombre', 'insumo_presentacion', 'proveedor_insumo', 'costo_insumo', 'proporcion', 'activo')
    list_editable = ('proporcion', 'activo')
    list_filter = ('grupo', 'activo')
    search_fields = ('insumo__nombre', 'insumo__proveedor__nombre')
    raw_id_fields = ['insumo']
    list_per_page = 30
    ordering = ['grupo', 'orden', 'categoria']
    fieldsets = (('Configuración', {'fields': ('categoria', 'grupo', 'insumo', 'proporcion', 'orden', 'activo')}),)

    def categoria_display(self, obj): return obj.get_categoria_display()
    categoria_display.short_description = "Concepto"
    categoria_display.admin_order_field = 'categoria'

    def grupo_display(self, obj):
        colores = {'ALCOHOL_NACIONAL': '#e67e22', 'ALCOHOL_PREMIUM': '#9b59b6', 'CERVEZA': '#f1c40f', 'MEZCLADOR': '#3498db', 'HIELO': '#ecf0f1', 'COCTELERIA': '#2ecc71', 'CONSUMIBLE': '#95a5a6'}
        color = colores.get(obj.grupo, '#666')
        return format_html('<span style="background:{}; padding:2px 8px; border-radius:4px; color:#fff; font-size:11px;">{}</span>', color, obj.get_grupo_display())
    grupo_display.short_description = "Grupo"
    grupo_display.admin_order_field = 'grupo'

    def insumo_nombre(self, obj): return obj.insumo.nombre
    insumo_nombre.short_description = "Insumo"
    insumo_nombre.admin_order_field = 'insumo__nombre'
    def insumo_presentacion(self, obj): return obj.insumo.presentacion or "-"
    insumo_presentacion.short_description = "Presentación"
    def proveedor_insumo(self, obj): return obj.insumo.proveedor.nombre if obj.insumo.proveedor else "Sin proveedor"
    proveedor_insumo.short_description = "Proveedor"
    def costo_insumo(self, obj): return f"${obj.insumo.costo_unitario:,.2f}"
    costo_insumo.short_description = "Costo"

    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']


class RecetaInline(admin.TabularInline):
    model = RecetaSubProducto
    extra = 1
    raw_id_fields = ['insumo']
    verbose_name = "Ingrediente"

@admin.register(SubProducto)
class SubProductoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'costo_display')
    inlines = [RecetaInline]
    search_fields = ('nombre',)
    def costo_display(self, obj): return f"${obj.costo_insumos():,.2f}"
    costo_display.short_description = "Costo Insumos"
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']

class ComponenteInline(admin.TabularInline):
    model = ComponenteProducto
    extra = 1
    raw_id_fields = ['subproducto']
    verbose_name = "SubProducto"


class ProductoPaqueteInline(admin.TabularInline):
    model = ProductoComponente
    fk_name = 'producto_padre'
    raw_id_fields = ['producto_hijo']
    verbose_name = 'Producto Incluido'
    verbose_name_plural = 'Productos Incluidos en este Paquete'
    extra = 1
    fields = ('producto_hijo', 'cantidad')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('producto_hijo')


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    inlines = [ComponenteInline, ProductoPaqueteInline]
    list_display = ('nombre', 'costo_display', 'precio_display', 'badge_cotizador', 'badge_paquete', 'badge_upgrade', 'badge_licor')
    list_filter = ('visible_cotizador', 'grupo_cotizador', 'rol_cotizador', 'cotizador_hospedaje', 'es_paquete', 'es_upgrade', 'requiere_licor')
    filter_horizontal = ('hereda_inventario_de',)
    search_fields = ('nombre',)
    fieldsets = (
        (None, {'fields': ('nombre', 'descripcion', 'margen_ganancia', 'precio_venta_fijo', 'imagen_promocional')}),
        ('Estructura del Producto', {
            'fields': ('es_paquete',),
            'description': (
                '<strong>Producto Simple:</strong> Usa la sección "SubProductos" abajo.<br>'
                '<strong>Paquete:</strong> Usa la sección "Productos Incluidos en este Paquete" abajo.'
            ),
        }),
        ('Herencia de Inventario', {
            'fields': ('es_upgrade', 'hereda_inventario_de', 'requiere_licor'),
            'description': (
                'Configura si este producto es un upgrade de uno o varios productos base '
                'para evitar duplicar subproductos al calcular el inventario de una cotización. '
                '<strong>"Hereda inventario de"</strong> solo muestra productos base '
                '(es_upgrade=False) y permite seleccionar varios. '
                '<strong>"Requiere licor"</strong> obliga a que la cotización incluya '
                'Licores Nacionales o Licores Premium.'
            ),
            'classes': ('collapse',),
        }),
        ('Cotizador Web', {
            'fields': (
                'visible_cotizador',
                ('cotizador_evento', 'cotizador_pasadia', 'cotizador_arrendamiento', 'cotizador_hospedaje'),
                'rol_cotizador',
                'grupo_cotizador', 'icono', 'descripcion_corta',
                'orden_cotizador', 'grupo_exclusion',
                ('cantidad_por_persona', 'factor_personas'),
            ),
            'description': 'Configura cómo aparece este producto en el cotizador público.',
        }),
    )

    def costo_display(self, obj): return f"${obj.calcular_costo():,.2f}"
    costo_display.short_description = "Costo (sin IVA)"
    def precio_display(self, obj):
        precio = obj.sugerencia_precio()
        if obj.precio_venta_fijo is not None and obj.precio_venta_fijo > 0:
            return format_html(
                '${} <span style="background:#1565C0;color:white;padding:2px 7px;'
                'border-radius:10px;font-size:9px;font-weight:600;margin-left:4px;">FIJO</span>',
                f'{precio:,.2f}'
            )
        return format_html(
            '${} <span style="background:#607D8B;color:white;padding:2px 7px;'
            'border-radius:10px;font-size:9px;font-weight:600;margin-left:4px;">CALC.</span>',
            f'{precio:,.2f}'
        )
    precio_display.short_description = "Precio sugerido (sin IVA)"

    def badge_cotizador(self, obj):
        if not obj.visible_cotizador:
            return mark_safe('<span style="color:#999;">—</span>')
        servicios = []
        if obj.cotizador_evento:
            servicios.append('E')
        if obj.cotizador_pasadia:
            servicios.append('P')
        if obj.cotizador_arrendamiento:
            servicios.append('A')
        if obj.cotizador_hospedaje:
            servicios.append('H')
        txt = '/'.join(servicios) or '—'
        return format_html(
            '<span style="background:#2E7D32;color:white;padding:2px 8px;'
            'border-radius:4px;font-size:11px;font-weight:600;">'
            '{} {}</span>',
            obj.icono, txt,
        )
    badge_cotizador.short_description = "Cotizador"

    def badge_paquete(self, obj):
        if obj.es_paquete:
            return mark_safe(
                '<span style="background:#9C27B0;color:white;padding:4px 12px;'
                'border-radius:12px;font-size:11px;font-weight:600;">PAQUETE</span>'
            )
        return mark_safe(
            '<span style="background:#607D8B;color:white;padding:4px 12px;'
            'border-radius:12px;font-size:11px;font-weight:600;">SIMPLE</span>'
        )
    badge_paquete.short_description = 'Tipo'

    def badge_upgrade(self, obj):
        if not obj.pk:
            return mark_safe('<span style="color:#999;font-size:11px;">—</span>')
        bases = list(obj.hereda_inventario_de.all()[:3])
        if obj.es_upgrade and bases:
            nombres_full = ', '.join(b.nombre for b in bases)
            nombres_short = ' + '.join(b.nombre[:12] for b in bases)
            return format_html(
                '<span style="background:#E65100;color:white;padding:3px 8px;'
                'border-radius:12px;font-size:10px;font-weight:600;" title="Hereda de: {}">'
                'UPGRADE → {}</span>',
                nombres_full,
                nombres_short,
            )
        if obj.es_upgrade:
            return mark_safe(
                '<span style="background:#FF8F00;color:white;padding:3px 8px;'
                'border-radius:12px;font-size:10px;font-weight:600;">UPGRADE</span>'
            )
        return mark_safe('<span style="color:#999;font-size:11px;">—</span>')
    badge_upgrade.short_description = 'Upgrade'

    def badge_licor(self, obj):
        if obj.requiere_licor:
            return mark_safe(
                '<span style="background:#7B1FA2;color:white;padding:3px 8px;'
                'border-radius:12px;font-size:10px;font-weight:600;">REQ LICOR</span>'
            )
        return mark_safe('<span style="color:#999;font-size:11px;">—</span>')
    badge_licor.short_description = 'Licor'

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == 'hereda_inventario_de':
            kwargs['queryset'] = Producto.objects.all().order_by('nombre')
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']

@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'tipo_persona', 'es_cliente_fiscal', 'rfc', 'email', 'telefono')
    list_filter = ('tipo_persona', 'es_cliente_fiscal', 'origen')
    search_fields = ('nombre', 'rfc', 'razon_social')
    fieldsets = (
        ('Datos Generales', {'fields': ('nombre', 'email', 'telefono', 'origen', 'fecha_registro')}),
        ('Datos Fiscales', {'fields': ('es_cliente_fiscal', 'tipo_persona', 'rfc', 'razon_social', 'codigo_postal_fiscal', 'regimen_fiscal', 'uso_cfdi')}),
    )
    readonly_fields = ('fecha_registro',)
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']


# ==========================================
# PLAN DE PAGOS
# ==========================================
class ParcialidadInline(admin.TabularInline):
    model = ParcialidadPago
    extra = 0
    fields = ('numero', 'concepto', 'porcentaje', 'monto', 'fecha_limite', 'pagada', 'fecha_pago_real', 'pago_vinculado')
    readonly_fields = ('numero', 'concepto', 'porcentaje', 'monto', 'fecha_limite')
    can_delete = False
    def has_add_permission(self, request, obj=None): return False


@admin.register(PlanPago)
class PlanPagoAdmin(admin.ModelAdmin):
    list_display = ('cotizacion_folio', 'cliente', 'monto_total', 'num_parcialidades', 'progreso_badge', 'siguiente_pago_info', 'activo')
    list_filter = ('activo',)
    search_fields = ('cotizacion__cliente__nombre', 'cotizacion__nombre_evento')
    readonly_fields = ('cotizacion', 'generado_por', 'fecha_generacion')
    inlines = [ParcialidadInline]

    def cotizacion_folio(self, obj): return f"COT-{obj.cotizacion.id:03d}"
    cotizacion_folio.short_description = "Folio"
    def cliente(self, obj): return obj.cotizacion.cliente.nombre
    cliente.short_description = "Cliente"
    def monto_total(self, obj): return f"${obj.cotizacion.precio_final:,.2f}"
    monto_total.short_description = "Total"

    def num_parcialidades(self, obj):
        return f"{obj.parcialidades_pagadas()}/{obj.parcialidades.count()}"
    num_parcialidades.short_description = "Pagadas"

    def progreso_badge(self, obj):
        pagadas = obj.parcialidades_pagadas()
        total = obj.parcialidades.count()
        if total == 0:
            return '-'
        pct = int((pagadas / total) * 100)
        color = '#27ae60' if pct >= 100 else '#f39c12' if pct >= 50 else '#e74c3c'
        return format_html(
            '<div style="width:80px; background:#ecf0f1; border-radius:10px; height:12px; overflow:hidden; display:inline-block;">'
            '<div style="width:{}%; background:{}; height:100%; border-radius:10px;"></div>'
            '</div> <small style="color:{};">{}%</small>', pct, color, color, pct)
    progreso_badge.short_description = "Progreso"

    def siguiente_pago_info(self, obj):
        sig = obj.siguiente_pago()
        if not sig:
            return mark_safe('<span style="color:#27ae60; font-weight:bold;">Liquidado</span>')
        dias = sig.dias_restantes
        if dias < 0:
            return format_html('<span style="color:#e74c3c; font-weight:bold;">${} vencido hace {} días</span>', f"{sig.monto:,.2f}", abs(dias))
        elif dias <= 7:
            return format_html('<span style="color:#f39c12; font-weight:bold;">${} en {} días</span>', f"{sig.monto:,.2f}", dias)
        return format_html('<span style="color:#3498db;">${} el {}</span>', f"{sig.monto:,.2f}", sig.fecha_limite.strftime('%d/%m/%Y'))
    siguiente_pago_info.short_description = "Próximo Pago"

    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']

@admin.register(PortalCliente)
class PortalClienteAdmin(admin.ModelAdmin):
    list_display = (
        'cotizacion_folio', 'cliente', 'activo', 'expira_en', 'visitas',
        'ultima_visita', 'link_portal',
    )
    list_filter = ('activo',)
    readonly_fields = (
        'token', 'expira_en', 'visitas', 'ultima_visita', 'created_at', 'created_by',
    )
    raw_id_fields = ('cotizacion',)
    actions = ['regenerar_token']

    def cotizacion_folio(self, obj):
        return f"COT-{obj.cotizacion.id:03d}"
    cotizacion_folio.short_description = "Folio"

    def cliente(self, obj):
        return obj.cotizacion.cliente.nombre
    cliente.short_description = "Cliente"

    def link_portal(self, obj):
        url = obj.get_full_url()
        return format_html('<a href="{}" target="_blank" style="color:#2E7D32;">Abrir portal</a>', url)
    link_portal.short_description = "Link"

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description='Regenerar token y extender acceso 90 días')
    @confirmar_accion_destructiva(
        "¿Regenerar el token del portal? El enlace anterior deja de servir de "
        "inmediato — cualquiera que lo tenga guardado (correo, WhatsApp) pierde "
        "acceso."
    )
    def regenerar_token(self, request, queryset):
        """Invalida la URL anterior y da 90 días de acceso desde hoy.

        Es la vía para volver a compartir el portal sin editar la BD a mano:
        el token viejo deja de servir en cuanto se guarda el nuevo.
        """
        ahora = timezone.now()
        actualizados = 0
        for portal in queryset:
            portal.token = secrets.token_urlsafe(32)
            portal.activo = True
            portal.expira_en = ahora + timedelta(days=90)
            portal.save(update_fields=['token', 'activo', 'expira_en'])
            actualizados += 1
        self.message_user(
            request,
            f"{actualizados} portal(es) regenerado(s): token nuevo y acceso "
            "extendido 90 días.",
        )

    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']

class PlanPagoResumenInline(admin.StackedInline):
    model = PlanPago
    extra = 0
    max_num = 1
    can_delete = False
    readonly_fields = ('generado_por', 'fecha_generacion')
    fields = ('activo', 'notas', 'generado_por', 'fecha_generacion')
    verbose_name = "Plan de Pagos"
    verbose_name_plural = "Plan de Pagos"


# ==========================================
# COTIZACIONES
# ==========================================
class ItemCotizacionInline(admin.TabularInline):
    model = ItemCotizacion
    extra = 1
    raw_id_fields = ['producto', 'insumo']
    fields = ('producto', 'insumo', 'descripcion', 'cantidad', 'precio_unitario', 'subtotal')
    readonly_fields = ('subtotal',)

class PagoInline(admin.TabularInline):
    model = Pago
    extra = 0
    fields = ('fecha_pago', 'monto', 'concepto', 'metodo', 'referencia', 'notas', 'usuario', 'created_at')
    readonly_fields = ('usuario', 'created_at')

def autocompletar_cotizacion_nueva(obj):
    """Rellena horario y nombre_evento al crear una Cotizacion desde el admin.

    Horario: Pasadía y Hospedaje tienen horario fijo y conocido de antemano
    (a diferencia de Evento/Arrendamiento, que varían por cotización), así
    que si se deja en blanco no debe quedar "Por definir" en el PDF/portal.

    Nombre del evento: concatenación de tipo de servicio + tipo de evento
    (solo aplica a EVENTO) + primer nombre del cliente, en vez del "Evento
    General" genérico que trae el campo por default.
    """
    from .views_cotizador import (
        HORA_FIN_HOSPEDAJE,
        HORA_FIN_PASADIA,
        HORA_INICIO_HOSPEDAJE,
        HORA_INICIO_PASADIA,
    )
    if obj.tipo_servicio == 'PASADIA':
        if not obj.hora_inicio:
            obj.hora_inicio = HORA_INICIO_PASADIA
        if not obj.hora_fin:
            obj.hora_fin = HORA_FIN_PASADIA
    elif obj.tipo_servicio == 'HOSPEDAJE':
        if not obj.hora_inicio:
            obj.hora_inicio = HORA_INICIO_HOSPEDAJE
        if not obj.hora_fin:
            obj.hora_fin = HORA_FIN_HOSPEDAJE

    primer_nombre = ''
    if obj.cliente_id and obj.cliente.nombre:
        primer_nombre = obj.cliente.nombre.split()[0]
    partes = [obj.get_tipo_servicio_display()]
    if obj.tipo_evento_id:
        partes.append(obj.tipo_evento.nombre)
    if primer_nombre:
        partes.append(primer_nombre)
    obj.nombre_evento = ' - '.join(partes)[:200]


@admin.register(Cotizacion)
class CotizacionAdmin(admin.ModelAdmin):
    change_form_template = 'admin/comercial/cotizacion/change_form.html'
    inlines = [ItemCotizacionInline, PagoInline, PlanPagoResumenInline]
    list_display = ('folio_cotizacion', 'nombre_evento', 'cliente', 'fecha_evento', 'get_nivel_paquete', 'estado_badge', 'pago_badge', 'identificacion_badge', 'precio_final', 'acciones_display')
    list_filter = ('estado', 'fecha_evento', 'clima', 'incluye_licor_nacional', 'incluye_licor_premium')
    search_fields = ('id', 'cliente__nombre', 'cliente__rfc', 'nombre_evento')
    raw_id_fields = ['cliente', 'insumo_hielo', 'insumo_refresco', 'insumo_agua', 'insumo_alcohol_basico', 'insumo_alcohol_premium', 'insumo_barman', 'insumo_auxiliar']
    ordering = ['-fecha_evento', '-id']
    formfield_overrides = {
        db_models.TimeField: {'widget': TimeSlotWidget},
    }

    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']

    fieldsets = (
        ('Información del Evento', {
            'fields': ('cliente', 'tipo_servicio', 'tipo_evento', 'nombre_evento', 'fecha_evento', 'fecha_salida', 'hora_inicio', 'hora_fin', 'num_personas', 'estado'),
            'description': '"Fecha de salida" solo aplica a Hospedaje (checkout, varias noches); en el resto de los servicios déjala vacía.',
        }),
        ('Configuración de Barra', {
            'fields': ('incluye_refrescos', 'incluye_cerveza', 'incluye_licor_nacional', 'incluye_licor_premium', 'incluye_cocteleria_basica', 'incluye_cocteleria_premium', 'clima', 'horas_servicio', 'factor_utilidad_barra', 'resumen_barra_html'),
            'description': 'Selecciona los componentes para armar el paquete.'
        }),
        ('Insumos Base (Costos)', {
            'fields': ('insumo_hielo', 'insumo_refresco', 'insumo_agua', 'insumo_barman', 'insumo_auxiliar', 'insumo_alcohol_basico', 'insumo_alcohol_premium'),
            'classes': ('collapse',),
        }),
        ('Finanzas', {
            'fields': ('subtotal', 'descuento', 'iva', 'retencion_isr', 'retencion_iva', 'precio_final'),
            'description': 'Subtotal y descuento son BASE, sin IVA. '
                           '<strong>Precio final es el total con IVA incluido</strong>: '
                           'es el importe que se le exhibe y se le cobra al cliente.',
        }),
        ('Cancelación', {'fields': ('motivo_cancelacion', 'cancelada_por', 'fecha_cancelacion'), 'classes': ('collapse',)}),
        ('Documentos', {
            'fields': ('archivo_pdf', 'identificacion_oficial', 'identificacion_revisada',
                       'identificacion_revisada_por', 'identificacion_revisada_en', 'enviar_email_btn'),
        }),
    )
    readonly_fields = ('subtotal', 'iva', 'retencion_isr', 'retencion_iva', 'precio_final', 'enviar_email_btn', 'resumen_barra_html', 'cancelada_por', 'fecha_cancelacion', 'identificacion_revisada_por', 'identificacion_revisada_en')

    # --- BADGES CORTOS (Punto 3) ---
    def estado_badge(self, obj):
        colores = {
            'BORRADOR': '#95a5a6', 'COTIZADA': '#3498db',
            'CONFIRMADA': '#2E7D32', 'EJECUTADA': '#1B5E20',
            'CERRADA': '#1abc9c', 'CANCELADA': '#e74c3c',
        }

        etiquetas = {
            'BORRADOR': 'Borrador', 'COTIZADA': 'Cotizada',
            'CONFIRMADA': 'Confirmada', 'EJECUTADA': 'Ejecutada',
            'CERRADA': 'Cerrada', 'CANCELADA': 'Cancelada',
        }
        color = colores.get(obj.estado, '#666')
        label = etiquetas.get(obj.estado, obj.get_estado_display())
        return format_html('<span style="background:{}; color:white; padding:3px 10px; border-radius:4px; font-size:11px; font-weight:bold;">{}</span>', color, label)
    estado_badge.short_description = "Estado"
    estado_badge.admin_order_field = 'estado'

    def pago_badge(self, obj):
        pct = obj.porcentaje_pagado
        if pct >= 100:
            color = '#27ae60'
        elif pct >= 50:
            color = '#f39c12'
        elif pct > 0:
            color = '#e67e22'
        else:
            color = '#e74c3c'
        return format_html('<span style="color:{}; font-weight:bold;">{}%</span>', color, pct)
    pago_badge.short_description = "Pagado"

    def identificacion_badge(self, obj):
        # Las tres cadenas son estáticas (sin interpolar datos de usuario).
        if not obj.identificacion_oficial:
            return mark_safe('<span style="color:#999;">—</span>')  # noqa: S308
        if obj.identificacion_revisada:
            return mark_safe('<span style="color:#27ae60; font-weight:bold;">Revisada</span>')  # noqa: S308
        return mark_safe('<span style="color:#e67e22; font-weight:bold;">Sin revisar</span>')  # noqa: S308
    identificacion_badge.short_description = "INE"

    def get_nivel_paquete(self, obj):
        checks = sum([obj.incluye_refrescos, obj.incluye_cerveza, obj.incluye_licor_nacional, obj.incluye_licor_premium, obj.incluye_cocteleria_basica, obj.incluye_cocteleria_premium])
        if checks == 0:
            return "Sin Barra"
        if checks <= 2:
            return "Básico"
        if checks <= 4:
            return "Plus"
        return "Premium"
    get_nivel_paquete.short_description = "Paquete"

    # --- BOTONES ESTANDARIZADOS (Punto 4, sin emojis) ---
    def ver_plan_pagos(self, obj, compact=False):
        """Botón Plan con opciones de parcialidades."""
        btn_tpl = BTN_SM if compact else BTN
        # Si ya tiene plan activo → botón morado que abre PDF
        try:
            plan = obj.plan_pago
            if plan and plan.activo:
                url_pdf = reverse('plan_pagos_pdf', args=[obj.id])
                pagadas = plan.parcialidades_pagadas()
                total = plan.parcialidades.count()
                return format_html(btn_tpl, url=url_pdf, target='target="_blank"', bg='#2E7D32', fg='white', label=f'{pagadas}/{total}', extra='')
        except PlanPago.DoesNotExist:
            pass

        # Si no tiene plan → dropdown con opciones
        if obj.precio_final > 0:
            url_auto = reverse('generar_plan_pagos', args=[obj.id])
            uid = f'pp-{obj.id}'
            btn_padding = '2px 6px' if compact else '4px 10px'
            btn_font = '10px' if compact else '11px'
            btn_clase = 'qkt-accion-btn' if compact else ''
            return format_html(
                '<div style="position:relative; display:inline-block;">'
                  '<button type="button" class="' + btn_clase + '" onclick="document.getElementById(\'{uid}\').style.display = document.getElementById(\'{uid}\').style.display === \'block\' ? \'none\' : \'block\'" '
                  'style="background:#2E7D32; color:white; padding:' + btn_padding + '; border-radius:3px; font-size:' + btn_font + '; font-weight:600; border:none; cursor:pointer; white-space:nowrap;">'
                  '+ Plan</button>'
                  '<div id="{uid}" style="display:none; position:absolute; top:28px; left:0; z-index:999; background:#383632; border:1px solid #4a4845; border-radius:6px; box-shadow:0 4px 12px rgba(0,0,0,0.3); min-width:130px; padding:4px 0;">'
                    '<a href="{url_auto}" style="display:block; padding:6px 14px; font-size:12px; color:#d4d1c8; text-decoration:none; font-weight:600;" '
                      'onmouseover="this.style.background=\'#4a4845\'" onmouseout="this.style.background=\'transparent\'">Auto</a>'
                    '<a href="{url_2}" style="display:block; padding:6px 14px; font-size:12px; color:#d4d1c8; text-decoration:none;" '
                      'onmouseover="this.style.background=\'#4a4845\'" onmouseout="this.style.background=\'transparent\'">Auto</a>'
                    '<a href="{url_3}" style="display:block; padding:6px 14px; font-size:12px; color:#d4d1c8; text-decoration:none;" '
                      'onmouseover="this.style.background=\'#4a4845\'" onmouseout="this.style.background=\'transparent\'">Auto</a>'
                    '<a href="{url_4}" style="display:block; padding:6px 14px; font-size:12px; color:#d4d1c8; text-decoration:none;" '
                      'onmouseover="this.style.background=\'#4a4845\'" onmouseout="this.style.background=\'transparent\'">Auto</a>'
                    '<a href="{url_5}" style="display:block; padding:6px 14px; font-size:12px; color:#d4d1c8; text-decoration:none;" '
                      'onmouseover="this.style.background=\'#4a4845\'" onmouseout="this.style.background=\'transparent\'">5 pagos</a>'
                    '<a href="{url_6}" style="display:block; padding:6px 14px; font-size:12px; color:#d4d1c8; text-decoration:none;" '
                      'onmouseover="this.style.background=\'#4a4845\'" onmouseout="this.style.background=\'transparent\'">6 pagos</a>'
                  '</div>'
                '</div>',
                uid=uid,
                url_auto=url_auto,
                url_2=f'{url_auto}?parcialidades=2',
                url_3=f'{url_auto}?parcialidades=3',
                url_4=f'{url_auto}?parcialidades=4',
                url_5=f'{url_auto}?parcialidades=5',
                url_6=f'{url_auto}?parcialidades=6',
            )
        return '-'
    ver_plan_pagos.short_description = "Plan"

    def ver_pdf(self, obj, compact=False):
        try:
            url = reverse('cotizacion_pdf', args=[obj.id])
            return format_html(BTN_SM if compact else BTN, url=url, target='target="_blank"', bg='#2E7D32', fg='white', label='PDF', extra='')
        except NoReverseMatch:
            return "-"
    ver_pdf.short_description = "PDF"

    def ver_lista_compras(self, obj, compact=False):
        try:
            url = reverse('cotizacion_lista_compras', args=[obj.id])
            return format_html(BTN_SM if compact else BTN, url=url, target='target="_blank"', bg='#2E7D32', fg='white', label='Lista', extra='')
        except NoReverseMatch:
            return "-"
    ver_lista_compras.short_description = "Compras"

    def enviar_email_btn(self, obj, compact=False):
        if obj.pk:
            try:
                url = reverse('cotizacion_email', args=[obj.id])
                return format_html(BTN_SM if compact else BTN, url=url, target='', bg='#2E7D32', fg='white', label='Email', extra='onclick="return confirm(\'¿Enviar cotización por email?\')"')
            except NoReverseMatch:
                return "-"
        return "-"
    enviar_email_btn.short_description = "Email"

    def resumen_barra_html(self, obj):
        calc = CalculadoraBarraService(obj)
        datos = calc.calcular()
        if not datos:
            return mark_safe('<div style="padding:15px; color:#666;">Seleccione servicios y guarde para calcular.</div>')
        return mark_safe(render_to_string('admin/comercial/resumen_barra_partial.html', {'datos': datos}))  # noqa: S308 -- revisado: solo interpola choices/numeros/HTML fijo, sin texto libre de usuario
    resumen_barra_html.short_description = "Reporte Ejecutivo"

    # --- SAVE MODEL CON VALIDACIONES ---
    def save_model(self, request, obj, form, change):
        if change:
            old_obj = Cotizacion.objects.filter(pk=obj.pk).values(
                'estado', 'identificacion_revisada',
            ).first()
            old_estado = old_obj['estado'] if old_obj else 'BORRADOR'

            if obj.identificacion_revisada and not (old_obj and old_obj['identificacion_revisada']):
                obj.identificacion_revisada_por = request.user
                from django.utils.timezone import now as _now
                obj.identificacion_revisada_en = _now()
            elif not obj.identificacion_revisada:
                obj.identificacion_revisada_por = None
                obj.identificacion_revisada_en = None

            if obj.estado != old_estado:
                permitidos = Cotizacion.TRANSICIONES_PERMITIDAS.get(old_estado, [])
                if obj.estado not in permitidos:
                    messages.error(request, f"No se puede cambiar de '{dict(Cotizacion.ESTADOS).get(old_estado)}' a '{obj.get_estado_display()}'.")
                    return

                if obj.estado == 'CONFIRMADA':
                    try:
                        pct_min = float(ConstanteSistema.objects.get(clave='PORCENTAJE_ANTICIPO_MINIMO').valor)
                    except ConstanteSistema.DoesNotExist:
                        pct_min = 0
                    if pct_min > 0 and obj.precio_final > 0:
                        pagado = obj.total_pagado()
                        pct_pagado = (pagado / obj.precio_final) * 100
                        if pct_pagado < pct_min:
                            messages.error(request, f"Se requiere al menos {pct_min}% de anticipo. Pagado: {pct_pagado:.1f}%")
                            return

                if old_estado == 'BORRADOR' and obj.estado != 'CANCELADA':
                    if obj.pk and not obj.items.exists():
                        messages.error(request, "La cotización debe tener al menos un item antes de avanzar.")
                        return

                if obj.estado == 'CANCELADA' and not obj.motivo_cancelacion:
                    messages.error(request, "Debe indicar el motivo de cancelación.")
                    return
                if obj.estado == 'CANCELADA':
                    obj.cancelada_por = request.user
                    from django.utils.timezone import now
                    obj.fecha_cancelacion = now()

        if not obj.pk:
            obj.usuario = request.user
            autocompletar_cotizacion_nueva(obj)
        super().save_model(request, obj, form, change)

    def folio_cotizacion(self, obj): return f"COT-{obj.id:03d}"

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for instance in instances:
            if isinstance(instance, Pago) and not instance.pk:
                instance.usuario = request.user
            instance.save()
        formset.save_m2m()
        cot = formset.instance
        if isinstance(cot, Cotizacion):
            cot.calcular_totales()
            Cotizacion.objects.filter(pk=cot.pk).update(
                subtotal=cot.subtotal, iva=cot.iva,
                retencion_isr=cot.retencion_isr, retencion_iva=cot.retencion_iva,
                precio_final=cot.precio_final
            )

    def ver_contrato(self, obj, compact=False):
        if obj.id and obj.estado == 'CONFIRMADA':
            # Va al formulario intermedio (no genera nada todavía) — ahí se
            # ven los contratos ya generados (con su PDF) para solo
            # consultarlos, y solo se sube un PDF nuevo a Cloudinary si el
            # usuario decide explícitamente generar uno. Antes este botón
            # apuntaba directo a generar_contrato, que creaba y subía un
            # ContratoServicio + PDF nuevos en CADA clic —incluida cada vez
            # que alguien solo quería volver a ver el contrato— sin borrar
            # nunca los anteriores, inflando el uso de Cloudinary.
            url = reverse('admin:cotizacion_contrato_form', args=[obj.id])
            padding = '2px 6px' if compact else '4px 10px'
            font_size = '10px' if compact else '11px'
            clase = 'btn btn-info btn-sm qkt-accion-btn' if compact else 'btn btn-info btn-sm'
            return format_html(
                '<a href="{}" class="' + clase + '" '
                'style="background:#F5C518;color:#333;border:none;padding:' + padding + ';border-radius:3px;'
                'font-size:' + font_size + ';font-weight:600;text-decoration:none;display:inline-block;white-space:nowrap;">Contrato</a>',
                url
            )
        return mark_safe(f'<span style="color:#95a5a6;font-size:{"10px" if compact else "11px"};">—</span>')  # noqa: S308 -- revisado: solo interpola choices/numeros/HTML fijo, sin texto libre de usuario
    ver_contrato.short_description = "Contrato"

    @admin.display(description="Acciones")
    def acciones_display(self, obj):
        """Agrupa Plan/PDF/Compras/Email/Contrato/Portal en una sola columna,
        en una sola fila (antes cada botón tenía su propia columna, lo que se
        veía apretado y desalineado). Botones en tamaño compacto para que
        quepan todos sin partirse a una segunda línea."""
        partes = ''.join(str(parte) for parte in [
            self.ver_plan_pagos(obj, compact=True),
            self.ver_pdf(obj, compact=True),
            self.ver_lista_compras(obj, compact=True),
            self.enviar_email_btn(obj, compact=True),
            self.ver_contrato(obj, compact=True),
            self.ver_portal(obj, compact=True),
        ])
        return mark_safe(  # noqa: S308 -- revisado: solo interpola choices/numeros/HTML fijo, sin texto libre de usuario
            '<div style="display:flex; flex-wrap:nowrap; align-items:center; gap:3px; '
            'overflow-x:auto; max-width:100%;">' + partes + '</div>'
        )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<int:cotizacion_id>/contrato/',
                self.admin_site.admin_view(self.contrato_form_view),
                name='cotizacion_contrato_form'),
            path('<int:cotizacion_id>/descuentos/',
                self.admin_site.admin_view(self.descuentos_view),
                name='cotizacion_descuentos'),
        ]
        return custom_urls + urls

    def descuentos_view(self, request, cotizacion_id):
        """Página intermedia: muestra descuentos aplicables y ya aplicados,
        y permite aplicar/revertir manualmente. Solo staff con permiso de
        cambio de cotizaciones."""
        from .models import Descuento, DescuentoAplicado
        from .services_descuentos import DescuentoService

        if not request.user.has_perm('comercial.change_cotizacion'):
            messages.error(request, "No tienes permiso para aplicar descuentos.")
            return redirect('admin:comercial_cotizacion_changelist')

        cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)

        if request.method == 'POST':
            accion = request.POST.get('accion')
            try:
                if accion == 'aplicar':
                    descuento = get_object_or_404(Descuento, id=request.POST.get('descuento_id'))
                    DescuentoService.aplicar(cotizacion, descuento, usuario=request.user, modo='MANUAL')
                    messages.success(request, f'Descuento "{descuento.nombre}" aplicado.')
                elif accion == 'aplicar_automaticos':
                    aplicados = DescuentoService.aplicar_automaticos(cotizacion, usuario=request.user)
                    if aplicados:
                        nombres = ", ".join(a.descuento.nombre for a in aplicados)
                        messages.success(request, f'Aplicados automáticamente: {nombres}.')
                    else:
                        messages.info(request, 'No hay descuentos automáticos aplicables.')
                elif accion == 'revertir':
                    da = get_object_or_404(DescuentoAplicado, id=request.POST.get('aplicado_id'), cotizacion=cotizacion)
                    DescuentoService.revertir(da)
                    messages.success(request, f'Descuento "{da.descuento.nombre}" revertido.')
            except Exception as e:
                messages.error(request, f'Error: {e}')
            return redirect(request.path)

        candidatos = DescuentoService.evaluar_automaticos(cotizacion)
        aplicados = cotizacion.descuentos_aplicados.select_related('descuento', 'aplicado_por').all()
        ids_aplicados = set(cotizacion.descuentos_aplicados.filter(activo=True).values_list('descuento_id', flat=True))
        manuales = Descuento.objects.filter(modo='MANUAL', activo=True).exclude(id__in=ids_aplicados)

        context = {
            **self.admin_site.each_context(request),
            'title': f'Descuentos — {cotizacion}',
            'cotizacion': cotizacion,
            'candidatos': candidatos,
            'manuales': manuales,
            'aplicados': aplicados,
            'opts': self.model._meta,
        }
        return render(request, 'admin/comercial/cotizacion/descuentos.html', context)

    actions = ['evaluar_descuentos_aplicables']

    @admin.action(description="Evaluar descuentos aplicables")
    def evaluar_descuentos_aplicables(self, request, queryset):
        """Abre la página de descuentos para una cotización BORRADOR/COTIZADA."""
        if queryset.count() != 1:
            self.message_user(request, "Selecciona exactamente una cotización.", messages.WARNING)
            return
        cot = queryset.first()
        if cot.estado not in ('BORRADOR', 'COTIZADA'):
            self.message_user(request, "Solo BORRADOR o COTIZADA admiten evaluar descuentos.", messages.WARNING)
            return
        from django.urls import reverse as _reverse
        return redirect(_reverse('admin:cotizacion_descuentos', args=[cot.id]))

    def contrato_form_view(self, request, cotizacion_id):
        """Formulario intermedio para seleccionar tipo y depósito antes de generar."""
        from .models import ContratoServicio
        cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)

        if cotizacion.estado != 'CONFIRMADA':
            messages.error(request, "Solo cotizaciones confirmadas.")
            return redirect('admin:comercial_cotizacion_changelist')

        contratos_previos = ContratoServicio.objects.filter(cotizacion=cotizacion).order_by('-generado_en')

        if request.method == 'POST':
            from django.urls import reverse as _reverse
            return redirect(f"/cotizacion/{cotizacion_id}/contrato/generar/?"
                        f"tipo_servicio={request.POST.get('tipo_servicio','EVENTO')}"
                        f"&deposito={request.POST.get('deposito_garantia','0')}")

        context = {
            **self.admin_site.each_context(request),
            'title': f'Generar Contrato — {cotizacion}',
            'cotizacion': cotizacion,
            'contratos_previos': contratos_previos,
            'opts': self.model._meta,
        }
        return render(request, 'admin/comercial/cotizacion/contrato_form.html', context)

    def ver_portal(self, obj, compact=False):
        from .models import PortalCliente
        padding = '2px 6px' if compact else '4px 8px'
        font_size = '10px' if compact else '11px'
        gap = '2px' if compact else '3px'
        btn_style = f'padding:{padding};border-radius:3px;font-size:{font_size};font-weight:600;text-decoration:none;white-space:nowrap;'
        clase = 'qkt-accion-btn' if compact else ''
        try:
            portal = obj.portal
            if portal and portal.activo:
                url = portal.get_full_url()
                tel = ''.join(filter(str.isdigit, obj.cliente.telefono or ''))
                wa_url = (
                    f"https://wa.me/{tel}"
                    f"?text=Hola%20{obj.cliente.nombre}%2C%20aqu%C3%AD%20puedes%20ver%20"
                    f"los%20detalles%20de%20tu%20evento%3A%20{url}"
                )
                copy_id = f"portal-url-{obj.pk}"
                return format_html(
                    '<span style="display:inline-flex; align-items:center; gap:' + gap + ';">'
                    '<a href="{}" target="_blank" class="' + clase + '" style="background:#2E7D32;color:white;' + btn_style + '">Portal</a>'
                    '<a href="{}" target="_blank" class="' + clase + '" style="background:#25D366;color:white;' + btn_style + '">WA</a>'
                    '<span id="{}" style="display:none">{}</span>'
                    '<button class="' + clase + '" onclick="(function(){{var el=document.getElementById(\'{}\');navigator.clipboard.writeText(el.textContent).then(function(){{var b=event.target;var t=b.textContent;b.textContent=\'Copiado!\';b.style.background=\'#27ae60\';setTimeout(function(){{b.textContent=t;b.style.background=\'#607d8b\';}},1500);}});}})();return false;" style="background:#607d8b;color:white;border:none;cursor:pointer;' + btn_style + '">Copiar</button>'
                    '</span>',
                    url, wa_url, copy_id, url, copy_id
                )
        except Exception:
            pass
        return mark_safe(f'<span style="color:#95a5a6;font-size:{font_size};">Sin portal</span>')  # noqa: S308 -- revisado: solo interpola choices/numeros/HTML fijo, sin texto libre de usuario
    ver_portal.short_description = "Portal"
@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    change_list_template = "admin/comercial/pago/change_list.html"
    list_display = ('cotizacion', 'tipo_badge', 'concepto', 'fecha_pago', 'monto', 'metodo', 'comision_tpv', 'referencia', 'usuario', 'created_at')
    list_filter = ('tipo', 'concepto', 'metodo', 'fecha_pago')
    search_fields = ('cotizacion__cliente__nombre', 'referencia', 'cotizacion__nombre_evento')
    readonly_fields = ('usuario', 'created_at', 'updated_at')
    date_hierarchy = 'fecha_pago'
    actions = ['registrar_reembolso', 'reembolsar_en_openpay']
    fieldsets = (
        ('Tipo', {'fields': ('tipo', 'concepto')}),
        ('Datos', {'fields': ('cotizacion', 'fecha_pago', 'monto', 'metodo', 'referencia', 'notas')}),
        ('Comisión de terminal', {
            'fields': ('comision_tpv',),
            'description': 'Solo aplica a pagos cobrados con la terminal física (tarjeta de crédito/débito). '
                            'Captura el monto exacto que descontó el banco (IVA incluido) — genera automáticamente '
                            'la póliza del gasto financiero al guardar.',
        }),
        ('Facturación', {'fields': ('solicitar_factura',)}),
        ('Auditoría', {'fields': ('usuario', 'created_at', 'updated_at')}),
    )

    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']

    def tipo_badge(self, obj):
        color = '#e74c3c' if obj.tipo == 'REEMBOLSO' else '#2E7D32'
        return format_html(
            '<span style="background:{};color:white;padding:2px 8px;border-radius:3px;font-size:10px;font-weight:600;">{}</span>',
            color, obj.get_tipo_display()
        )
    tipo_badge.short_description = 'Tipo'

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.usuario = request.user
        super().save_model(request, obj, form, change)

    @confirmar_accion_destructiva(
        "¿Registrar un reembolso espejo de los pagos seleccionados? Crea un "
        "Pago tipo REEMBOLSO por cada uno, reduciendo el ingreso registrado."
    )
    def registrar_reembolso(self, request, queryset):
        """Crea un Pago tipo REEMBOLSO espejo por cada pago seleccionado."""
        creados = 0
        errores = []
        for pago in queryset.filter(tipo='INGRESO'):
            try:
                Pago.objects.create(
                    cotizacion=pago.cotizacion,
                    tipo='REEMBOLSO',
                    fecha_pago=timezone.now().date(),
                    monto=pago.monto,
                    metodo=pago.metodo,
                    referencia=f"REEMB de pago #{pago.pk}",
                    notas=f"Reembolso generado desde admin del pago #{pago.pk}",
                    usuario=request.user,
                )
                creados += 1
            except Exception as e:
                errores.append(f"Pago #{pago.pk}: {e}")
        if creados:
            self.message_user(request, f"{creados} reembolso(s) registrado(s).", messages.SUCCESS)
        for err in errores:
            self.message_user(request, err, messages.ERROR)
    registrar_reembolso.short_description = "Registrar reembolso (espejo del pago)"

    @confirmar_accion_destructiva(
        "¿Reembolsar en Openpay los pagos seleccionados? Dispara el refund "
        "real contra la API de Openpay — mueve dinero de verdad."
    )
    def reembolsar_en_openpay(self, request, queryset):
        """Dispara el refund real en Openpay para pagos que vinieron de ahí.
        Es adicional a 'registrar_reembolso' (que solo crea el registro interno)."""
        from .services_openpay import reembolsar_cargo_openpay
        ok, errores = 0, []
        for pago in queryset:
            resultado = reembolsar_cargo_openpay(pago)
            if resultado['ok']:
                ok += 1
            else:
                errores.append(f"Pago #{pago.pk}: {resultado['mensaje']}")
        if ok:
            self.message_user(request, f"{ok} reembolso(s) procesado(s) en Openpay.", messages.SUCCESS)
        for err in errores:
            self.message_user(request, err, messages.ERROR)
    reembolsar_en_openpay.short_description = "Reembolsar en Openpay (además de registrar reembolso)"

    # ─── Simulador de pago (no guarda nada) ──────────────────────
    # Para "¿cómo se comportaría esto?" sin crear un Pago real — que dispararía
    # póliza contable y solicitud de factura de verdad, y contaminaría el saldo
    # que ve el cliente en su portal. Reusa Pago.full_clean() sobre una
    # instancia en memoria: la misma validación real, sin persistir nada.
    def get_urls(self):
        urls = super().get_urls()
        my_urls = [path('simular/', self.admin_site.admin_view(self.simular_view), name='comercial_pago_simular')]
        return my_urls + urls

    def simular_view(self, request):
        from decimal import Decimal, InvalidOperation

        from django.core.exceptions import ValidationError
        from django.db.models import Q

        from .services import calcular_desglose_proporcional

        q = request.GET.get('q', '').strip()
        cotizacion_id = request.GET.get('cotizacion_id', '').strip()
        monto_str = request.GET.get('monto', '').strip()
        metodo = request.GET.get('metodo') or Pago.METODOS[0][0]

        cotizaciones_encontradas = []
        cotizacion = None
        simulacion = None
        error_monto = None

        if q and not cotizacion_id:
            qs = Cotizacion.objects.select_related('cliente').exclude(estado='CANCELADA')
            folio = q.upper().removeprefix('COT-').strip()
            if folio.isdigit():
                qs = qs.filter(pk=int(folio))
            else:
                qs = qs.filter(Q(cliente__nombre__icontains=q) | Q(nombre_evento__icontains=q))
            cotizaciones_encontradas = list(qs.order_by('-created_at')[:20])

        if cotizacion_id.isdigit():
            cotizacion = Cotizacion.objects.select_related('cliente').filter(pk=int(cotizacion_id)).first()

        if cotizacion and monto_str:
            try:
                monto = Decimal(monto_str)
                if monto <= 0:
                    raise InvalidOperation
            except InvalidOperation:
                error_monto = "Ingresa un monto válido, mayor a cero."
            else:
                pago_prueba = Pago(
                    cotizacion=cotizacion, tipo='INGRESO', concepto='VENTA',
                    monto=monto, metodo=metodo, fecha_pago=timezone.now().date(),
                )
                try:
                    pago_prueba.full_clean()
                except ValidationError as e:
                    error_monto = ' '.join(
                        f"{campo}: {'; '.join(msgs)}" for campo, msgs in e.message_dict.items()
                    )
                else:
                    saldo_antes = cotizacion.saldo_pendiente()
                    minimo, motivo_minimo = cotizacion.monto_minimo_pago_detalle()
                    desglose = calcular_desglose_proporcional(monto, cotizacion)
                    saldo_despues = saldo_antes - monto
                    total_pagado_despues = cotizacion.total_pagado() + monto
                    porcentaje_pagado_despues = (
                        (total_pagado_despues / cotizacion.precio_final * 100)
                        if cotizacion.precio_final else Decimal('0')
                    )
                    porcentaje_minimo_confirmar = cotizacion._get_porcentaje_anticipo_minimo()

                    simulacion = {
                        'monto': monto,
                        'saldo_antes': saldo_antes,
                        'saldo_despues': saldo_despues,
                        'minimo': minimo,
                        'motivo_minimo': motivo_minimo,
                        'cumple_minimo': (monto >= minimo) if minimo else True,
                        'desglose': desglose,
                        'porcentaje_pagado_despues': porcentaje_pagado_despues,
                        'porcentaje_minimo_confirmar': porcentaje_minimo_confirmar,
                        'alcanzaria_confirmar': (
                            porcentaje_minimo_confirmar == 0
                            or porcentaje_pagado_despues >= porcentaje_minimo_confirmar
                        ),
                        'cerraria_cotizacion': saldo_despues <= Decimal('0.50'),
                    }

        context = {
            **self.admin_site.each_context(request),
            'title': "Simulador de pago",
            'q': q,
            'cotizaciones_encontradas': cotizaciones_encontradas,
            'cotizacion': cotizacion,
            'monto_str': monto_str,
            'metodo': metodo,
            'metodos': Pago.METODOS,
            'simulacion': simulacion,
            'error_monto': error_monto,
        }
        return render(request, 'admin/comercial/simulador_pago.html', context)


class GastoInline(admin.TabularInline):
    model = Gasto
    extra = 0
    can_delete = True
    fields = ('cantidad', 'unidad_medida', 'descripcion', 'precio_unitario', 'total_linea', 'categoria', 'evento_relacionado')
    readonly_fields = ('cantidad', 'unidad_medida', 'descripcion', 'precio_unitario', 'total_linea')
    def get_readonly_fields(self, request, obj=None): return [f for f in self.readonly_fields]

@admin.register(Compra)
class CompraAdmin(admin.ModelAdmin):
    change_list_template = "comercial/compra_change_list.html"
    list_display = ('fecha_emision', 'proveedor', 'total_format', 'unidad_negocio', 'cuenta_pago', 'es_deducible', 'uuid', 'ver_pdf')
    list_editable = ('es_deducible',)
    list_filter = ('fecha_emision', 'unidad_negocio', 'cuenta_pago', 'es_deducible')
    search_fields = ('proveedor__nombre', 'proveedor_nombre', 'uuid', 'rfc_emisor')
    date_hierarchy = 'fecha_emision'
    autocomplete_fields = ['proveedor']
    readonly_fields = ('proveedor_nombre',)
    inlines = [GastoInline]
    fieldsets = (
        ('Archivo Fuente (Opcional)', {
            'fields': ('archivo_xml', 'archivo_pdf'),
            'description': 'Si no hay factura (ej. compra en el extranjero), deja este bloque vacío y '
                            'captura los datos a mano abajo — el gasto se registrará como no deducible.',
        }),
        ('Datos Generales', {
            'fields': ('fecha_emision', 'proveedor', 'proveedor_nombre', 'rfc_emisor', 'uuid'),
            'description': 'El campo "Proveedor" se busca/crea automáticamente en el catálogo por RFC o '
                            'nombre al guardar (desde el XML o desde el nombre que captures). Puedes '
                            'corregirlo con el buscador, o dar de alta uno nuevo con el botón "+".',
        }),
        ('Contabilidad', {
            'fields': ('unidad_negocio', 'cuenta_pago', 'es_deducible'),
            'description': 'Sin unidad de negocio y cuenta de pago, la póliza generada queda en BORRADOR '
                            'y no se aplica. "Deducible" se fuerza a No automáticamente si no hay UUID '
                            '(factura sin timbrar / sin CFDI).',
        }),
        ('Totales Globales', {'fields': ('subtotal', 'descuento', 'iva', 'ret_isr', 'ret_iva', 'total')})
    )
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']
    def get_urls(self):
        urls = super().get_urls()
        my_urls = [path('carga-masiva/', self.admin_site.admin_view(self.carga_masiva_view), name='compra_carga_masiva')]
        return my_urls + urls
    def carga_masiva_view(self, request):
        from io import BytesIO

        from django.core.files.uploadedfile import InMemoryUploadedFile

        from contabilidad.models import UnidadNegocio

        from .services import analizar_xml_compra

        if request.method == "POST":
            files = request.FILES.getlist('xml_files')
            if not files:
                messages.error(request, "No seleccionaste ningún archivo.")
                return redirect('.')

            # Si forzaron unidad manual, respetarla
            unidad_id = request.POST.get('unidad_negocio')
            unidad_fija = None
            if unidad_id:
                unidad_fija = UnidadNegocio.objects.filter(pk=unidad_id).first()

            ignorar_filtros = request.POST.get('ignorar_filtros') == '1'

            exitos = errores = excluidas = duplicadas = 0
            motivos_excluidas = []
            motivos_duplicadas = []
            for f in files:
                try:
                    f.seek(0)
                    file_content = f.read()
                    if not file_content or len(file_content) < 100:
                        raise ValueError(f"Archivo vacío o muy pequeño ({len(file_content)} bytes)")

                    valido, motivo, unidad_clave, rfc_r, tipo, uso, es_duplicado = analizar_xml_compra(file_content)

                    if not valido and not ignorar_filtros:
                        if es_duplicado:
                            duplicadas += 1
                            motivos_duplicadas.append(f"{f.name}: {motivo}")
                        else:
                            excluidas += 1
                            motivos_excluidas.append(f"{f.name}: {motivo}")
                        continue

                    # Si no se forzó una unidad manual, Compra.save() la
                    # detecta sola a partir del RFC receptor del propio XML
                    # (misma lógica que analizar_xml_compra, sin duplicarla aquí).
                    file_io = BytesIO(file_content)
                    new_file = InMemoryUploadedFile(
                        file=file_io,
                        field_name='archivo_xml',
                        name=f.name,
                        content_type='application/xml',
                        size=len(file_content),
                        charset=None
                    )
                    Compra.objects.create(archivo_xml=new_file, unidad_negocio=unidad_fija)
                    exitos += 1
                except Exception as e:
                    errores += 1
                    print(f"Error subiendo {f.name}: {e}")

            if exitos > 0:
                messages.success(
                    request,
                    f"{exitos} factura(s) del negocio procesada(s) correctamente. Sus líneas de gasto "
                    "quedaron en categoría 'Sin Clasificar' — revísalas y asigna la categoría correcta "
                    "en cada Compra para que los reportes por categoría salgan bien. Las que pertenecen "
                    "a una unidad de negocio con una sola cuenta bancaria activa ya quedaron aplicadas "
                    "solas; el resto se queda en BORRADOR hasta que subas el estado de cuenta del mes y "
                    "uses ahí la acción «Sugerir y aplicar Compras pendientes»."
                )
            if duplicadas > 0:
                resumen_dup = "; ".join(motivos_duplicadas[:5])
                extra_dup = f" (+{duplicadas - 5} más)" if duplicadas > 5 else ""
                messages.warning(
                    request,
                    f"{duplicadas} factura(s) OMITIDA(S) por ser duplicadas (ya existían): {resumen_dup}{extra_dup}"
                )
            if excluidas > 0:
                resumen = "; ".join(motivos_excluidas[:5])
                extra = f" (+{excluidas - 5} más)" if excluidas > 5 else ""
                messages.warning(
                    request,
                    f"{excluidas} factura(s) EXCLUIDA(S) por no pertenecer al negocio: {resumen}{extra}"
                )
            if errores > 0:
                messages.error(request, f"{errores} archivo(s) con errores técnicos (XML corrupto o inválido).")
            return redirect('..')

        # GET: mostrar formulario con unidades de negocio
        unidades = UnidadNegocio.objects.filter(activa=True)
        return render(request, 'comercial/carga_masiva_xml.html', {
            'title': 'Carga Masiva de XML',
            'unidades': unidades,
        })
    def total_format(self, obj): return f"${obj.total:,.2f}"
    total_format.short_description = "Total"
    def ver_pdf(self, obj):
        if obj.archivo_pdf:
            return format_html('<a href="{}" target="_blank">Ver</a>', url_descarga(obj, 'archivo_pdf'))
        return "-"
    ver_pdf.short_description = "PDF"

from .models import ContratoServicio


@admin.register(ContratoServicio)
class ContratoServicioAdmin(admin.ModelAdmin):
    list_display  = ('numero', 'cotizacion', 'tipo_servicio', 'deposito_garantia',
                     'generado_en', 'generado_por', 'enviado_email', 'descargar_btn', 'enviar_btn')
    list_filter   = ('tipo_servicio', 'enviado_email', 'generado_en')
    search_fields = ('numero', 'cotizacion__cliente__nombre')
    readonly_fields = ('numero', 'generado_por', 'generado_en', 'enviado_email')

    @admin.display(description="Descargar")
    def descargar_btn(self, obj):
        if obj.archivo:
            return format_html(
                '<a href="{}" target="_blank" class="btn btn-primary">PDF</a>',
                url_descarga(obj, 'archivo')
            )
        return "—"

    @admin.display(description="Email")
    def enviar_btn(self, obj):
        url = reverse('contrato_email', args=[obj.id])
        if obj.enviado_email:
            return mark_safe(
                '<span style="background:#2E7D32;color:white;padding:4px 10px;border-radius:4px;font-size:11px;font-weight:600;">Enviado</span>')
        return format_html(
            '<a href="{}" style="background:#2E7D32;color:white;padding:4px 10px;border-radius:4px;font-size:11px;font-weight:600;text-decoration:none;">Enviar</a>',
            url)

@admin.register(RecordatorioPago)
class RecordatorioPagoAdmin(admin.ModelAdmin):
    list_display = (
        'parcialidad_info', 'cliente', 'fecha_envio',
        'estado_badge', 'monto_parcialidad'
    )
    list_filter = ('estado', 'fecha_envio')
    search_fields = (
        'parcialidad__plan__cotizacion__cliente__nombre',
        'parcialidad__plan__cotizacion__nombre_evento',
    )
    readonly_fields = (
        'parcialidad', 'fecha_envio', 'estado',
        'mensaje_enviado', 'respuesta_api', 'error_detalle', 'created_at'
    )
    date_hierarchy = 'fecha_envio'
    ordering = ['-fecha_envio']

    def parcialidad_info(self, obj):
        cot = obj.parcialidad.plan.cotizacion
        return f"COT-{cot.id:03d} — {obj.parcialidad.concepto}"
    parcialidad_info.short_description = "Parcialidad"

    def cliente(self, obj):
        return obj.parcialidad.plan.cotizacion.cliente.nombre
    cliente.short_description = "Cliente"

    def monto_parcialidad(self, obj):
        return f"${obj.parcialidad.monto:,.2f}"
    monto_parcialidad.short_description = "Monto"

    def estado_badge(self, obj):
        colores = {
            'ENVIADO': ('#2E7D32', 'white'),
            'FALLIDO': ('#e74c3c', 'white'),
            'OMITIDO': ('#95a5a6', 'white'),
        }
        bg, fg = colores.get(obj.estado, ('#333', 'white'))
        return format_html(
            '<span style="background:{};color:{};padding:2px 8px;'
            'border-radius:4px;font-size:11px;font-weight:600;">{}</span>',
            bg, fg, obj.get_estado_display()
        )
    estado_badge.short_description = "Estado"

    def has_add_permission(self, request): return False
    def has_delete_permission(self, request, obj=None): return False



# ==========================================
# ADMIN: ESPACIOS Y ASIGNACIONES (Fase 4)
# ==========================================
@admin.register(Espacio)
class EspacioAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'tipo', 'capacidad_max', 'activo')
    list_filter = ('tipo', 'activo')
    search_fields = ('nombre', 'descripcion')


class AsignacionEspacioInline(admin.TabularInline):
    model = AsignacionEspacio
    extra = 0
    fields = ('espacio', 'fecha', 'hora_inicio', 'hora_fin', 'notas')


class AsignacionPersonalInline(admin.TabularInline):
    model = AsignacionPersonal
    extra = 0
    fields = ('empleado', 'rol', 'fecha', 'hora_inicio', 'hora_fin', 'notas')


@admin.register(AsignacionEspacio)
class AsignacionEspacioAdmin(admin.ModelAdmin):
    list_display = ('espacio', 'cotizacion', 'fecha', 'hora_inicio', 'hora_fin')
    list_filter = ('espacio', 'fecha')
    search_fields = ('cotizacion__nombre_evento', 'cotizacion__cliente__nombre', 'espacio__nombre')
    date_hierarchy = 'fecha'
    autocomplete_fields = ('cotizacion',)


@admin.register(AsignacionPersonal)
class AsignacionPersonalAdmin(admin.ModelAdmin):
    list_display = ('empleado', 'rol', 'cotizacion', 'fecha', 'hora_inicio', 'hora_fin')
    list_filter = ('rol', 'fecha')
    search_fields = ('empleado__nombre', 'cotizacion__nombre_evento', 'cotizacion__cliente__nombre')
    date_hierarchy = 'fecha'
    autocomplete_fields = ('cotizacion',)


# ==========================================
# CONTENIDO DE LANDING PAGE
# ==========================================
class CargaMasivaImagenLandingForm(forms.ModelForm):
    """Valida cada archivo de la carga masiva.

    `ImagenLanding.objects.create(imagen=archivo)` se salta la verificación
    del `ImageField`, así que un PDF renombrado o un `.heic` acabaría en el
    bucket y después rompería la página web con una imagen muerta. Pasando
    por el formulario, ese archivo se rechaza antes de subir nada.
    """

    class Meta:
        model = ImagenLanding
        fields = ('seccion', 'categoria_galeria', 'posicion_vertical',
                  'mostrar_en_galeria', 'titulo', 'imagen', 'orden')


class DesactivarSinArchivoMixin:
    """Acción compartida por los modelos de la landing que guardan imagen.

    Es una acción sobre la selección, no una columna de `list_display`:
    `exists()` es una petición de red por registro y en la lista dispararía
    una por fila en cada carga de página.
    """

    @admin.action(description="Desactivar los que ya no tienen archivo")
    def desactivar_sin_archivo(self, request, queryset):
        # Desactivar, nunca borrar: el archivo se puede volver a subir, pero
        # el orden, la categoría y el alt_text del registro no se recuperan.
        sin_archivo = []
        con_archivo = 0
        for obj in queryset:
            if not obj.imagen:
                continue
            if obj.imagen.storage.exists(obj.imagen.name):
                con_archivo += 1
            else:
                obj.activo = False
                sin_archivo.append(obj)

        if sin_archivo:
            queryset.model.objects.bulk_update(sin_archivo, ['activo'])
            messages.warning(
                request,
                f"{len(sin_archivo)} registro(s) sin archivo en el storage: "
                "quedaron desactivados y fuera de la página web. Vuelve a "
                "subirles la imagen y reactívalos."
            )
        if con_archivo:
            messages.success(request, f"{con_archivo} registro(s) conservan su archivo.")
        if not sin_archivo and not con_archivo:
            messages.info(request, "Ninguno de los registros seleccionados tiene imagen asignada.")


@admin.register(ImagenLanding)
class ImagenLandingAdmin(DesactivarSinArchivoMixin, admin.ModelAdmin):
    list_display = ('preview_mini', 'seccion', 'categoria_galeria', 'mostrar_en_galeria', 'titulo', 'orden', 'activo')
    list_filter = ('seccion', 'categoria_galeria', 'mostrar_en_galeria', 'activo')
    list_editable = ('orden', 'activo')
    list_display_links = ('preview_mini', 'seccion')
    actions = ['desactivar_sin_archivo']
    fieldsets = (
        (None, {
            'fields': ('seccion', 'categoria_galeria', 'mostrar_en_galeria', 'imagen', 'posicion_vertical', 'preview_grande'),
        }),
        ('Detalles', {
            'fields': ('titulo', 'alt_text', 'orden', 'activo'),
        }),
    )
    readonly_fields = ('preview_grande',)

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [path(
            'carga-masiva/',
            self.admin_site.admin_view(self.carga_masiva_view),
            name='imagenlanding_carga_masiva',
        )]
        return my_urls + urls

    def carga_masiva_view(self, request):
        from pathlib import Path

        from django.conf import settings
        from django.core.exceptions import PermissionDenied, TooManyFilesSent

        # admin_view() solo comprueba is_staff; esta vista crea registros, así
        # que exige además el permiso de alta del modelo.
        if not request.user.has_perm('comercial.add_imagenlanding'):
            raise PermissionDenied

        limite = settings.DATA_UPLOAD_MAX_NUMBER_FILES
        contexto = {
            'title': 'Carga masiva de imágenes',
            'secciones': ImagenLanding.SECCION_CHOICES,
            'categorias': ImagenLanding.CATEGORIA_GALERIA_CHOICES,
            'posiciones': PosicionLanding.choices,
            'limite_archivos': limite,
        }
        if request.method != 'POST':
            return render(request, 'comercial/carga_masiva_imagenes.html', contexto)

        try:
            # TooManyFilesSent salta al tocar request.POST/FILES, antes de que
            # la vista pueda contar nada. El límite no se sube globalmente
            # (es superficie de DoS en todo el sitio): se avisa y se pide
            # subir en tandas.
            archivos = sorted(request.FILES.getlist('imagenes'), key=lambda f: f.name)
            seccion = request.POST.get('seccion') or ''
            categoria = request.POST.get('categoria_galeria') or ''
            posicion = request.POST.get('posicion_vertical') or PosicionLanding.CENTER
            mostrar_en_galeria = request.POST.get('mostrar_en_galeria') == '1'
        except TooManyFilesSent:
            messages.error(
                request,
                f"Enviaste más de {limite} archivos de una vez. Súbelos en "
                f"tandas de máximo {limite}."
            )
            return redirect('.')

        if not archivos:
            messages.error(request, "No seleccionaste ninguna imagen.")
            return redirect('.')
        if seccion not in dict(ImagenLanding.SECCION_CHOICES):
            messages.error(request, "Elige una sección válida.")
            return redirect('.')

        # El orden continúa desde el máximo de la sección: el orden en que se
        # nombran los archivos es el orden en que salen en la página.
        orden = ImagenLanding.objects.filter(seccion=seccion).aggregate(
            db_models.Max('orden')
        )['orden__max'] or 0

        creadas = 0
        invalidos = []
        errores = 0
        for archivo in archivos:
            datos = {
                'seccion': seccion,
                'categoria_galeria': categoria,
                'posicion_vertical': posicion,
                'mostrar_en_galeria': mostrar_en_galeria,
                # El alt_text lo escribe una persona: autogenerarlo desde el
                # nombre del archivo no describe nada y ensucia el SEO.
                'titulo': Path(archivo.name).stem[:120],
                'orden': orden + 1,
            }
            form = CargaMasivaImagenLandingForm(datos, {'imagen': archivo})
            if not form.is_valid():
                invalidos.append(archivo.name)
                continue
            try:
                form.save()
            except Exception:
                errores += 1
                logger.exception("Error subiendo la imagen %s en la carga masiva", archivo.name)
                continue
            orden += 1
            creadas += 1

        if creadas:
            messages.success(
                request,
                f"{creadas} imagen(es) creada(s) en «{dict(ImagenLanding.SECCION_CHOICES)[seccion]}». "
                "Les falta el texto alternativo: complétalo en cada una para accesibilidad y SEO."
            )
        if invalidos:
            resumen = ", ".join(invalidos[:5])
            extra = f" (+{len(invalidos) - 5} más)" if len(invalidos) > 5 else ""
            messages.warning(
                request,
                f"{len(invalidos)} archivo(s) descartado(s) por no ser imágenes válidas: {resumen}{extra}. "
                "Los .heic de iPhone hay que convertirlos a JPG antes de subirlos."
            )
        if errores:
            messages.error(request, f"{errores} archivo(s) fallaron al subir por un error técnico.")
        return redirect('..')

    @admin.display(description="Preview")
    def preview_mini(self, obj):
        if obj.imagen:
            return format_html(
                '<img src="{}" style="width:80px;height:55px;object-fit:cover;border-radius:4px;">',
                obj.imagen.url
            )
        return "—"

    @admin.display(description="Vista previa")
    def preview_grande(self, obj):
        if obj.imagen:
            pos = obj.posicion_vertical or 'center'
            return format_html(
                '<div style="width:400px;height:200px;border-radius:6px;overflow:hidden;'
                'background:url({}) center/{} no-repeat;background-position:center {};">'
                '</div>'
                '<p style="margin-top:4px;font-size:11px;color:#666;">Enfoque: {}</p>',
                obj.imagen.url, 'cover', pos, obj.get_posicion_vertical_display()
            )
        return "Sin imagen"


@admin.register(TestimonioLanding)
class TestimonioLandingAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'evento', 'estrellas_display', 'texto_corto', 'activo')
    list_filter = ('activo', 'estrellas')
    list_editable = ('activo',)

    @admin.display(description="Estrellas")
    def estrellas_display(self, obj):
        return format_html(
            '<span style="color:#F5C518;font-size:1.1em;">{}</span>',
            '★' * obj.estrellas + '☆' * (5 - obj.estrellas)
        )

    @admin.display(description="Testimonio")
    def texto_corto(self, obj):
        return obj.texto[:80] + '…' if len(obj.texto) > 80 else obj.texto


@admin.register(EspacioLanding)
class EspacioLandingAdmin(DesactivarSinArchivoMixin, admin.ModelAdmin):
    list_display = ('preview_mini', 'nombre', 'capacidad', 'orden', 'activo')
    list_editable = ('orden', 'activo')
    list_display_links = ('preview_mini', 'nombre')
    actions = ['desactivar_sin_archivo']
    fieldsets = (
        (None, {'fields': ('nombre', 'imagen', 'posicion_vertical', 'capacidad', 'descripcion')}),
        ('Opciones', {'fields': ('orden', 'activo')}),
    )

    @admin.display(description="Preview")
    def preview_mini(self, obj):
        if obj.imagen:
            return format_html(
                '<img src="{}" style="width:80px;height:55px;object-fit:cover;border-radius:4px;">',
                obj.imagen.url
            )
        return "—"


@admin.register(PreguntaFrecuente)
class PreguntaFrecuenteAdmin(admin.ModelAdmin):
    list_display = ('pregunta', 'respuesta_corta', 'orden', 'activo')
    list_editable = ('orden', 'activo')

    @admin.display(description="Respuesta")
    def respuesta_corta(self, obj):
        return obj.respuesta[:100] + '…' if len(obj.respuesta) > 100 else obj.respuesta


# ==========================================
# DESCUENTOS
# ==========================================
@admin.register(TipoEvento)
class TipoEventoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'orden', 'activo')
    list_editable = ('orden', 'activo')
    ordering = ('orden', 'nombre')


@admin.register(GuiaTipoServicio)
class GuiaTipoServicioAdmin(admin.ModelAdmin):
    """Sube/reemplaza el PDF que se manda automáticamente antes del evento
    (comunicacion.enviar_guias, Issue #234). Solo 3 filas posibles (una por
    tipo_servicio con guía), no hace falta ninguna pantalla más elaborada."""
    list_display = ('tipo_servicio', 'archivo_pdf', 'actualizado_en')


@admin.register(Temporada)
class TemporadaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'anio_display', 'fecha_inicio', 'fecha_fin', 'activo')
    list_filter = ('anio', 'activo')
    search_fields = ('nombre',)
    ordering = ('-anio', 'fecha_inicio')

    def anio_display(self, obj):
        return str(obj.anio)
    anio_display.short_description = 'Año'
    anio_display.admin_order_field = 'anio'

    def save_model(self, request, obj, form, change):
        obj.full_clean()
        super().save_model(request, obj, form, change)


@admin.register(Descuento)
class DescuentoAdmin(admin.ModelAdmin):
    list_display = (
        'nombre', 'tipo_valor_badge', 'valor_display', 'modo_badge',
        'activo', 'vigencia', 'acumulable', 'prioridad', 'usos_display',
    )
    list_filter = ('activo', 'modo', 'acumulable', 'tipo_valor', 'temporada')
    search_fields = ('nombre', 'descripcion')
    filter_horizontal = ('tipos_evento',)
    readonly_fields = ('usos', 'created_by', 'created_at', 'updated_by', 'updated_at')
    fieldsets = (
        (None, {'fields': ('nombre', 'descripcion', 'activo')}),
        ('Valor', {'fields': ('tipo_valor', 'valor')}),
        ('Aplicación', {'fields': ('modo', 'acumulable', 'prioridad', 'max_usos', 'usos')}),
        ('Condiciones (opcionales, se evalúan con AND)', {
            'fields': ('monto_minimo', 'fecha_inicio', 'fecha_fin', 'temporada',
                       'tipos_evento', 'tipos_servicio'),
            'description': 'Deja en blanco lo que no aplique. tipos_servicio: lista JSON con EVENTO, PASADIA y/o ARRENDAMIENTO.',
        }),
        ('Auditoría', {'fields': ('created_by', 'created_at', 'updated_by', 'updated_at'), 'classes': ('collapse',)}),
    )

    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']

    def tipo_valor_badge(self, obj):
        color = '#3498db' if obj.tipo_valor == 'PORCENTAJE' else '#9b59b6'
        return format_html(
            '<span style="background:{};color:white;padding:2px 8px;border-radius:4px;font-size:11px;">{}</span>',
            color, obj.get_tipo_valor_display()
        )
    tipo_valor_badge.short_description = 'Tipo'
    tipo_valor_badge.admin_order_field = 'tipo_valor'

    def modo_badge(self, obj):
        color = '#2E7D32' if obj.modo == 'AUTOMATICO' else '#95a5a6'
        return format_html(
            '<span style="background:{};color:white;padding:2px 8px;border-radius:4px;font-size:11px;">{}</span>',
            color, obj.get_modo_display()
        )
    modo_badge.short_description = 'Modo'
    modo_badge.admin_order_field = 'modo'

    def valor_display(self, obj):
        return f"{obj.valor}%" if obj.tipo_valor == 'PORCENTAJE' else f"${obj.valor:,.2f}"
    valor_display.short_description = 'Valor'
    valor_display.admin_order_field = 'valor'

    def vigencia(self, obj):
        if obj.temporada:
            return f"Temporada: {obj.temporada.nombre}"
        if obj.fecha_inicio or obj.fecha_fin:
            ini = obj.fecha_inicio.strftime('%d/%m/%Y') if obj.fecha_inicio else '—'
            fin = obj.fecha_fin.strftime('%d/%m/%Y') if obj.fecha_fin else '—'
            return f"{ini} → {fin}"
        return "Sin restricción"
    vigencia.short_description = 'Vigencia'

    def usos_display(self, obj):
        return f"{obj.usos}/{obj.max_usos}" if obj.max_usos is not None else f"{obj.usos} (∞)"
    usos_display.short_description = 'Usos'

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        obj.full_clean()
        super().save_model(request, obj, form, change)


@admin.register(DescuentoAplicado)
class DescuentoAplicadoAdmin(admin.ModelAdmin):
    """Auditoría inmutable: solo lectura, sin borrado."""
    list_display = (
        'fecha_aplicacion', 'cotizacion', 'descuento', 'monto_aplicado',
        'porcentaje_equivalente', 'modo_aplicacion', 'aplicado_por', 'activo',
    )
    list_filter = ('activo', 'modo_aplicacion', 'descuento', 'fecha_aplicacion')
    search_fields = ('cotizacion__id', 'cotizacion__nombre_evento', 'descuento__nombre')
    date_hierarchy = 'fecha_aplicacion'
    readonly_fields = (
        'cotizacion', 'descuento', 'monto_aplicado', 'porcentaje_equivalente',
        'modo_aplicacion', 'aplicado_por', 'fecha_aplicacion', 'activo', 'notas',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OpenpayTransaccion)
class OpenpayTransaccionAdmin(admin.ModelAdmin):
    list_display = ('openpay_id', 'metodo', 'event_type', 'estado_openpay', 'monto', 'cotizacion', 'autorizacion', 'pago', 'procesado', 'created_at')
    list_filter = ('procesado', 'metodo', 'event_type', 'created_at')
    search_fields = ('openpay_id', 'referencia_pago', 'autorizacion', 'cotizacion__nombre_evento')
    readonly_fields = ('openpay_id', 'event_type', 'metodo', 'estado_openpay', 'monto', 'cotizacion', 'autorizacion', 'pago', 'referencia_pago', 'payload_crudo', 'procesado', 'error_detalle', 'created_at')
    actions = ['borrar_transacciones_de_prueba']

    def has_add_permission(self, request):
        return False  # solo se crean desde el webhook, nunca manual

    @confirmar_accion_destructiva(
        "¿Borrar las transacciones de prueba seleccionadas junto con su Pago "
        "y sus pólizas contables? La cotización queda intacta, pero esto no "
        "se puede deshacer."
    )
    def borrar_transacciones_de_prueba(self, request, queryset):
        """
        Borra las transacciones seleccionadas junto con su Pago y las pólizas
        contables que generaron (pago + comisión). Deja la Cotizacion intacta
        con su saldo pendiente restaurado. Se niega si OPENPAY_MODE ya es
        'production' (ver comercial.services_openpay.borrar_transacciones_openpay_prueba).
        """
        from .services_openpay import borrar_transacciones_openpay_prueba
        try:
            n_transacciones, n_pagos = borrar_transacciones_openpay_prueba(queryset)
        except ValueError as e:
            self.message_user(request, str(e), level=messages.ERROR)
            return
        self.message_user(
            request,
            f"Borradas {n_transacciones} transacciones y {n_pagos} pagos (con sus pólizas). "
            f"Las cotizaciones quedaron intactas con su saldo pendiente restaurado.",
            level=messages.SUCCESS,
        )
    borrar_transacciones_de_prueba.short_description = "Borrar transacción de prueba (y su Pago/póliza)"
