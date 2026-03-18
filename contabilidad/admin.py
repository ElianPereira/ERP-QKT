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
    
    def tipo_badge(self, obj):
        colores = {
            'ACTIVO': '#2196F3', 'PASIVO': '#9C27B0', 'CAPITAL': '#4CAF50',
            'INGRESO': '#8BC34A', 'COSTO': '#FF9800', 'GASTO': '#F44336', 'ORDEN': '#607D8B',
        }
        color = colores.get(obj.tipo, '#666')
        return format_html(
            '<span style="background:{}; color:white; padding:3px 8px; border-radius:4px; font-size:11px;">{}</span>',
            color, obj.get_tipo_display().split(' - ')[0]
        )
    tipo_badge.short_description = "Tipo"
    
    def naturaleza_badge(self, obj):
        if obj.naturaleza == 'D':
            return format_html('<span style="color:#1976D2; font-weight:600;">⬆ Deudora</span>')
        return format_html('<span style="color:#7B1FA2; font-weight:600;">⬇ Acreedora</span>')
    naturaleza_badge.short_description = "Naturaleza"


@admin.register(UnidadNegocio)
class UnidadNegocioAdmin(admin.ModelAdmin):
    list_display = ['clave', 'nombre', 'regimen_fiscal', 'activa']
    list_filter = ['regimen_fiscal', 'activa']
    search_fields = ['clave', 'nombre']


@admin.register(CuentaBancaria)
class CuentaBancariaAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'banco', 'clabe_display', 'cuenta_contable', 'activa']
    list_filter = ['banco', 'activa']
    search_fields = ['nombre', 'banco', 'clabe']
    
    def clabe_display(self, obj):
        if obj.clabe:
            return f"***{obj.clabe[-4:]}"
        return "-"
    clabe_display.short_description = "CLABE"


@admin.register(Poliza)
class PolizaAdmin(admin.ModelAdmin):
    list_display = ['folio_display', 'tipo_badge', 'fecha', 'concepto_corto', 'unidad_negocio', 'estado_badge']
    list_filter = ['tipo', 'estado', 'unidad_negocio', 'origen', 'fecha']
    search_fields = ['folio', 'concepto']
    date_hierarchy = 'fecha'
    ordering = ['-fecha', '-folio']
    inlines = [MovimientoContableInline]
    
    def folio_display(self, obj):
        return format_html('<strong>{}-{:04d}</strong>', obj.tipo, obj.folio)
    folio_display.short_description = "Folio"
    
    def tipo_badge(self, obj):
        colores = {'I': '#4CAF50', 'E': '#F44336', 'D': '#2196F3'}
        return format_html(
            '<span style="background:{}; color:white; padding:2px 8px; border-radius:4px; font-size:11px;">{}</span>',
            colores.get(obj.tipo, '#666'), obj.get_tipo_display()
        )
    tipo_badge.short_description = "Tipo"
    
    def concepto_corto(self, obj):
        return obj.concepto[:60] + "..." if len(obj.concepto) > 60 else obj.concepto
    concepto_corto.short_description = "Concepto"
    
    def estado_badge(self, obj):
        colores = {'BORRADOR': '#FF9800', 'APLICADA': '#4CAF50', 'CANCELADA': '#9E9E9E'}
        return format_html(
            '<span style="background:{}; color:white; padding:2px 8px; border-radius:4px; font-size:11px;">{}</span>',
            colores.get(obj.estado, '#666'), obj.get_estado_display()
        )
    estado_badge.short_description = "Estado"
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
            obj.folio = Poliza.siguiente_folio(obj.tipo, obj.fecha)
        super().save_model(request, obj, form, change)


@admin.register(ConciliacionBancaria)
class ConciliacionBancariaAdmin(admin.ModelAdmin):
    list_display = ['cuenta_bancaria', 'periodo_display', 'saldo_segun_banco', 'saldo_segun_libros', 'diferencia', 'estado']
    list_filter = ['estado', 'cuenta_bancaria', 'anio']
    ordering = ['-anio', '-mes']
    
    def periodo_display(self, obj):
        meses = ['', 'Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
        return f"{meses[obj.mes]} {obj.anio}"
    periodo_display.short_description = "Período"


@admin.register(ConfiguracionContable)
class ConfiguracionContableAdmin(admin.ModelAdmin):
    list_display = ['operacion', 'cuenta', 'descripcion', 'activa']
    list_filter = ['activa']
    search_fields = ['operacion', 'cuenta__codigo_sat']
    autocomplete_fields = ['cuenta']


@admin.register(MovimientoContable)
class MovimientoContableAdmin(admin.ModelAdmin):
    list_display = ['poliza', 'cuenta', 'concepto', 'debe', 'haber', 'referencia']
    list_filter = ['poliza__tipo', 'cuenta__tipo']
    search_fields = ['cuenta__codigo_sat', 'concepto']
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
