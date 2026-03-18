"""
Admin del Módulo de Contabilidad
"""
from decimal import Decimal
from django.contrib import admin
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
    list_display = ['codigo_sat', 'nombre', 'tipo', 'naturaleza', 'nivel', 'permite_movimientos', 'activa']
    list_filter = ['tipo', 'naturaleza', 'nivel', 'activa', 'permite_movimientos']
    search_fields = ['codigo_sat', 'nombre']
    ordering = ['codigo_sat']
    list_per_page = 50
    autocomplete_fields = ['padre']
    inlines = [SubcuentaInline]


@admin.register(UnidadNegocio)
class UnidadNegocioAdmin(admin.ModelAdmin):
    list_display = ['clave', 'nombre', 'regimen_fiscal', 'activa']
    list_filter = ['regimen_fiscal', 'activa']
    search_fields = ['clave', 'nombre']


@admin.register(CuentaBancaria)
class CuentaBancariaAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'banco', 'clabe', 'cuenta_contable', 'activa']
    list_filter = ['banco', 'activa']
    search_fields = ['nombre', 'banco', 'clabe']


@admin.register(Poliza)
class PolizaAdmin(admin.ModelAdmin):
    list_display = ['folio', 'tipo', 'fecha', 'concepto', 'unidad_negocio', 'estado']
    list_filter = ['tipo', 'estado', 'unidad_negocio', 'origen', 'fecha']
    search_fields = ['folio', 'concepto']
    date_hierarchy = 'fecha'
    ordering = ['-fecha', '-folio']
    inlines = [MovimientoContableInline]
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
            obj.folio = Poliza.siguiente_folio(obj.tipo, obj.fecha)
        super().save_model(request, obj, form, change)


@admin.register(ConciliacionBancaria)
class ConciliacionBancariaAdmin(admin.ModelAdmin):
    list_display = ['cuenta_bancaria', 'mes', 'anio', 'saldo_segun_banco', 'saldo_segun_libros', 'diferencia', 'estado']
    list_filter = ['estado', 'cuenta_bancaria', 'anio']
    ordering = ['-anio', '-mes']


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