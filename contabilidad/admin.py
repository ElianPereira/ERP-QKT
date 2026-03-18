"""
Admin del Módulo de Contabilidad
"""
from decimal import Decimal
from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Sum

from .models import (
    CuentaContable, UnidadNegocio, CuentaBancaria,
    Poliza, MovimientoContable, ConciliacionBancaria,
    ConfiguracionContable
)


class MovimientoContableInline(admin.TabularInline):
    model = MovimientoContable
    extra = 2
    fields = ['cuenta', 'concepto', 'debe', 'haber', 'referencia']
    autocomplete_fields = ['cuenta']


class SubcuentaInline(admin.TabularInline):
    model = CuentaContable
    fk_name = 'padre'
    extra = 0
    fields = ['codigo_sat', 'nombre', 'naturaleza', 'permite_movimientos', 'activa']
    readonly_fields = ['codigo_sat', 'nombre']
    show_change_link = True
    verbose_name = "Subcuenta"
    verbose_name_plural = "Subcuentas"
    
    def has_add_permission(self, request, obj=None):
        return False


@admin.register(CuentaContable)
class CuentaContableAdmin(admin.ModelAdmin):
    list_display = ['codigo_sat', 'nombre', 'tipo_badge', 'naturaleza_badge', 'nivel', 'permite_movimientos', 'activa']
    list_filter = ['tipo', 'naturaleza', 'nivel', 'activa', 'permite_movimientos']
    search_fields = ['codigo_sat', 'nombre']
    ordering = ['codigo_sat']
    list_per_page = 50
    autocomplete_fields = ['padre']
    inlines = [SubcuentaInline]
    
    @admin.display(description="Tipo", ordering="tipo")
    def tipo_badge(self, obj):
        colores = {
            'ACTIVO': '#2196F3',
            'PASIVO': '#9C27B0',
            'CAPITAL': '#4CAF50',
            'INGRESO': '#8BC34A',
            'COSTO': '#FF9800',
            'GASTO': '#F44336',
            'ORDEN': '#607D8B',
        }
        color = colores.get(obj.tipo, '#666')
        return format_html(
            '<span style="background:{}; color:white; padding:3px 8px; border-radius:4px; font-size:11px;">{}</span>',
            color,
            obj.tipo
        )
    
    @admin.display(description="Nat.", ordering="naturaleza")
    def naturaleza_badge(self, obj):
        if obj.naturaleza == 'D':
            return format_html(
                '<span style="color:#1976D2; font-weight:600;">⬆ D</span>'
            )
        return format_html(
            '<span style="color:#7B1FA2; font-weight:600;">⬇ A</span>'
        )


@admin.register(UnidadNegocio)
class UnidadNegocioAdmin(admin.ModelAdmin):
    list_display = ['clave', 'nombre', 'regimen_fiscal_display', 'activa']
    list_filter = ['regimen_fiscal', 'activa']
    search_fields = ['clave', 'nombre']
    
    @admin.display(description="Régimen Fiscal")
    def regimen_fiscal_display(self, obj):
        return obj.get_regimen_fiscal_display()


@admin.register(CuentaBancaria)
class CuentaBancariaAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'banco', 'clabe_oculta', 'cuenta_contable', 'saldo_display', 'activa']
    list_filter = ['banco', 'activa']
    search_fields = ['nombre', 'banco', 'clabe']
    
    @admin.display(description="CLABE")
    def clabe_oculta(self, obj):
        if obj.clabe:
            return f"****{obj.clabe[-4:]}"
        return "-"
    
    @admin.display(description="Saldo")
    def saldo_display(self, obj):
        saldo = obj.saldo_actual
        color = '#4CAF50' if saldo >= 0 else '#F44336'
        return format_html(
            '<span style="color:{}; font-weight:600;">${:,.2f}</span>',
            color,
            saldo
        )


@admin.register(Poliza)
class PolizaAdmin(admin.ModelAdmin):
    list_display = ['folio_display', 'tipo_badge', 'fecha', 'concepto_corto', 'unidad_negocio', 'total_display', 'estado_badge']
    list_filter = ['tipo', 'estado', 'unidad_negocio', 'origen', 'fecha']
    search_fields = ['folio', 'concepto']
    date_hierarchy = 'fecha'
    ordering = ['-fecha', '-folio']
    readonly_fields = ['created_by', 'created_at', 'cancelada_por', 'fecha_cancelacion']
    inlines = [MovimientoContableInline]
    
    fieldsets = (
        ('Datos de la Póliza', {
            'fields': ('tipo', 'fecha', 'concepto', 'unidad_negocio')
        }),
        ('Estado', {
            'fields': ('estado', 'origen')
        }),
        ('Cancelación', {
            'fields': ('motivo_cancelacion', 'cancelada_por', 'fecha_cancelacion'),
            'classes': ('collapse',)
        }),
        ('Auditoría', {
            'fields': ('created_by', 'created_at'),
            'classes': ('collapse',)
        }),
    )
    
    @admin.display(description="Folio", ordering="folio")
    def folio_display(self, obj):
        return format_html('<strong>{}-{:04d}</strong>', obj.tipo, obj.folio)
    
    @admin.display(description="Tipo", ordering="tipo")
    def tipo_badge(self, obj):
        colores = {
            'I': '#4CAF50',
            'E': '#F44336',
            'D': '#2196F3'
        }
        color = colores.get(obj.tipo, '#666')
        return format_html(
            '<span style="background:{}; color:white; padding:2px 8px; border-radius:4px; font-size:11px;">{}</span>',
            color,
            obj.get_tipo_display()
        )
    
    @admin.display(description="Concepto")
    def concepto_corto(self, obj):
        if len(obj.concepto) > 50:
            return obj.concepto[:50] + "..."
        return obj.concepto
    
    @admin.display(description="Total")
    def total_display(self, obj):
        total = obj.total_debe
        cuadra = obj.esta_cuadrada
        color = '#4CAF50' if cuadra else '#F44336'
        icono = '✓' if cuadra else '✗'
        return format_html(
            '<span style="color:{};">${:,.2f} {}</span>',
            color,
            total,
            icono
        )
    
    @admin.display(description="Estado", ordering="estado")
    def estado_badge(self, obj):
        colores = {
            'BORRADOR': '#FF9800',
            'APLICADA': '#4CAF50',
            'CANCELADA': '#9E9E9E'
        }
        color = colores.get(obj.estado, '#666')
        return format_html(
            '<span style="background:{}; color:white; padding:2px 8px; border-radius:4px; font-size:11px;">{}</span>',
            color,
            obj.get_estado_display()
        )
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
            obj.folio = Poliza.siguiente_folio(obj.tipo, obj.fecha)
        super().save_model(request, obj, form, change)
    
    actions = ['aplicar_polizas', 'cancelar_polizas']
    
    @admin.action(description="✓ Aplicar pólizas seleccionadas")
    def aplicar_polizas(self, request, queryset):
        aplicadas = 0
        errores = []
        for poliza in queryset.filter(estado='BORRADOR'):
            if poliza.esta_cuadrada:
                poliza.aplicar(request.user)
                aplicadas += 1
            else:
                errores.append(f"{poliza}: No cuadra")
        
        if aplicadas:
            self.message_user(request, f"✓ {aplicadas} póliza(s) aplicada(s)")
        if errores:
            self.message_user(request, f"✗ Errores: {', '.join(errores)}", level='ERROR')
    
    @admin.action(description="✗ Cancelar pólizas seleccionadas")
    def cancelar_polizas(self, request, queryset):
        canceladas = queryset.exclude(estado='CANCELADA').update(
            estado='CANCELADA',
            cancelada_por=request.user,
            motivo_cancelacion='Cancelación masiva desde admin'
        )
        self.message_user(request, f"✓ {canceladas} póliza(s) cancelada(s)")


@admin.register(ConciliacionBancaria)
class ConciliacionBancariaAdmin(admin.ModelAdmin):
    list_display = ['cuenta_bancaria', 'periodo_display', 'saldo_segun_banco', 'saldo_segun_libros', 'diferencia_display', 'estado_badge']
    list_filter = ['estado', 'cuenta_bancaria', 'anio']
    ordering = ['-anio', '-mes']
    
    @admin.display(description="Período")
    def periodo_display(self, obj):
        meses = ['', 'Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 
                 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
        return f"{meses[obj.mes]} {obj.anio}"
    
    @admin.display(description="Diferencia")
    def diferencia_display(self, obj):
        if obj.diferencia == 0:
            return format_html('<span style="color:#4CAF50; font-weight:600;">$0.00 ✓</span>')
        color = '#F44336'
        return format_html(
            '<span style="color:{}; font-weight:600;">${:,.2f}</span>',
            color,
            obj.diferencia
        )
    
    @admin.display(description="Estado")
    def estado_badge(self, obj):
        colores = {
            'PENDIENTE': '#FF9800',
            'EN_PROCESO': '#2196F3',
            'CONCILIADA': '#4CAF50'
        }
        color = colores.get(obj.estado, '#666')
        return format_html(
            '<span style="background:{}; color:white; padding:2px 8px; border-radius:4px; font-size:11px;">{}</span>',
            color,
            obj.get_estado_display()
        )


@admin.register(ConfiguracionContable)
class ConfiguracionContableAdmin(admin.ModelAdmin):
    list_display = ['operacion_display', 'cuenta', 'descripcion', 'activa']
    list_filter = ['activa']
    search_fields = ['operacion', 'cuenta__codigo_sat', 'descripcion']
    autocomplete_fields = ['cuenta']
    
    @admin.display(description="Operación", ordering="operacion")
    def operacion_display(self, obj):
        return obj.get_operacion_display()


@admin.register(MovimientoContable)
class MovimientoContableAdmin(admin.ModelAdmin):
    list_display = ['poliza_link', 'cuenta', 'concepto', 'debe_display', 'haber_display', 'referencia']
    list_filter = ['poliza__tipo', 'poliza__estado', 'cuenta__tipo']
    search_fields = ['cuenta__codigo_sat', 'cuenta__nombre', 'concepto', 'poliza__concepto']
    autocomplete_fields = ['cuenta']
    
    @admin.display(description="Póliza")
    def poliza_link(self, obj):
        return format_html(
            '<a href="/admin/contabilidad/poliza/{}/change/">{}-{:04d}</a>',
            obj.poliza.pk,
            obj.poliza.tipo,
            obj.poliza.folio
        )
    
    @admin.display(description="Debe")
    def debe_display(self, obj):
        if obj.debe > 0:
            return format_html('<span style="color:#1976D2;">${:,.2f}</span>', obj.debe)
        return '-'
    
    @admin.display(description="Haber")
    def haber_display(self, obj):
        if obj.haber > 0:
            return format_html('<span style="color:#7B1FA2;">${:,.2f}</span>', obj.haber)
        return '-'
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False