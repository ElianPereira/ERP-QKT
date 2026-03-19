"""
Admin del Módulo de Contabilidad
================================
Versión simplificada compatible con Django 6.x + Jazzmin
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
                errores.append(f"{poliza.tipo}-{poliza.folio}")
        
        if aplicadas:
            self.message_user(request, f"✓ {aplicadas} póliza(s) aplicada(s)")
        if errores:
            self.message_user(request, f"✗ No cuadran: {', '.join(errores)}", level='ERROR')
    
    @admin.action(description="✗ Cancelar pólizas seleccionadas")
    def cancelar_polizas(self, request, queryset):
        from django.utils import timezone
        canceladas = queryset.exclude(estado='CANCELADA').update(
            estado='CANCELADA',
            cancelada_por=request.user,
            fecha_cancelacion=timezone.now(),
            motivo_cancelacion='Cancelación masiva desde admin'
        )
        self.message_user(request, f"✓ {canceladas} póliza(s) cancelada(s)")


@admin.register(ConciliacionBancaria)
class ConciliacionBancariaAdmin(admin.ModelAdmin):
    list_display = ['cuenta_bancaria', 'mes', 'anio', 'saldo_segun_banco', 'saldo_segun_libros', 'diferencia', 'estado']
    list_filter = ['estado', 'cuenta_bancaria', 'anio']
    ordering = ['-anio', '-mes']


@admin.register(ConfiguracionContable)
class ConfiguracionContableAdmin(admin.ModelAdmin):
    list_display = ['operacion', 'cuenta', 'descripcion', 'activa']
    list_filter = ['activa']
    search_fields = ['operacion', 'cuenta__codigo_sat', 'descripcion']
    autocomplete_fields = ['cuenta']


@admin.register(MovimientoContable)
class MovimientoContableAdmin(admin.ModelAdmin):
    list_display = ['poliza', 'cuenta', 'concepto', 'debe', 'haber', 'referencia']
    list_filter = ['poliza__tipo', 'poliza__estado', 'cuenta__tipo']
    search_fields = ['cuenta__codigo_sat', 'cuenta__nombre', 'concepto', 'poliza__concepto']
    autocomplete_fields = ['cuenta']
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False